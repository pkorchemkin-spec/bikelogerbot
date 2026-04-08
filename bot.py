import os
import sqlite3
import json
from datetime import datetime
from math import isfinite

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

DB_PATH = "bike_log.db"
DEFAULT_LUBE_INTERVAL_KM = 250.0
DEFAULT_CHAIN_REPLACE_INTERVAL_KM = 500.0
RIDES_PAGE_SIZE = 5


# ---------- DB ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ride_date TEXT NOT NULL,
            distance_km REAL NOT NULL,
            duration_min INTEGER NOT NULL,
            note TEXT DEFAULT ''
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_settings (
            user_id INTEGER PRIMARY KEY,
            lube_interval_km REAL NOT NULL DEFAULT 250,
            chain_replace_interval_km REAL NOT NULL DEFAULT 500,
            last_lube_odometer REAL NOT NULL DEFAULT 0,
            last_chain_replace_odometer REAL NOT NULL DEFAULT 0
        )
        """
    )

    conn.commit()
    conn.close()


# ---------- USER SETUP ----------

def ensure_user(user_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR IGNORE INTO maintenance_settings (
            user_id,
            lube_interval_km,
            chain_replace_interval_km,
            last_lube_odometer,
            last_chain_replace_odometer
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, DEFAULT_LUBE_INTERVAL_KM, DEFAULT_CHAIN_REPLACE_INTERVAL_KM, 0, 0),
    )

    conn.commit()
    created = cur.rowcount > 0
    conn.close()
    return created


# ---------- HELPERS ----------

def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def parse_date(value: str):
    datetime.strptime(value, "%Y-%m-%d")
    return value


def parse_float(value: str):
    return float(value.replace(",", "."))


def parse_int(value: str):
    return int(value)


def calc_avg_speed(distance_km: float, duration_min: int) -> float:
    if duration_min <= 0:
        return 0.0
    return distance_km / (duration_min / 60)


def format_speed(speed: float) -> str:
    return f"{speed:.1f} км/ч"


def format_ride_line(row):
    speed = calc_avg_speed(float(row["distance_km"]), int(row["duration_min"]))
    note = f" · {row['note']}" if row["note"] else ""
    return (
        f"#{row['id']} · {row['ride_date']}\n"
        f"{float(row['distance_km']):.1f} км · {int(row['duration_min'])} мин · {format_speed(speed)}{note}"
    )


# ---------- RIDE DATA ----------

def add_ride(user_id, ride_date, km, minutes, note=""):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rides (user_id, ride_date, distance_km, duration_min, note) VALUES (?, ?, ?, ?, ?)",
        (user_id, ride_date, km, minutes, note),
    )
    conn.commit()
    ride_id = cur.lastrowid
    conn.close()
    return ride_id


def update_ride(user_id, ride_id, ride_date, km, minutes, note=""):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE rides
        SET ride_date = ?, distance_km = ?, duration_min = ?, note = ?
        WHERE user_id = ? AND id = ?
        """,
        (ride_date, km, minutes, note, user_id, ride_id),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def get_ride(user_id, ride_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rides WHERE user_id = ? AND id = ?", (user_id, ride_id))
    row = cur.fetchone()
    conn.close()
    return row


def list_rides_page(user_id, offset=0, limit=RIDES_PAGE_SIZE):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM rides
        WHERE user_id = ?
        ORDER BY ride_date DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def rides_total_count(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM rides WHERE user_id = ?", (user_id,))
    value = cur.fetchone()[0]
    conn.close()
    return value


def total_km(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(distance_km), 0) FROM rides WHERE user_id = ?", (user_id,))
    val = float(cur.fetchone()[0])
    conn.close()
    return val


def total_time(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(duration_min), 0) FROM rides WHERE user_id = ?", (user_id,))
    val = int(cur.fetchone()[0])
    conn.close()
    return val


def rides_count(user_id):
    return rides_total_count(user_id)


def get_last_ride(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM rides WHERE user_id = ? ORDER BY ride_date DESC, id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_previous_rides_avg_speed(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM rides WHERE user_id = ? ORDER BY ride_date DESC, id DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if len(rows) < 2:
        return None

    previous = rows[1:]
    speeds = [calc_avg_speed(float(r["distance_km"]), int(r["duration_min"])) for r in previous if int(r["duration_min"]) > 0]
    if not speeds:
        return None
    return sum(speeds) / len(speeds)


# ---------- MAINTENANCE ----------

def get_maintenance(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT lube_interval_km, chain_replace_interval_km, last_lube_odometer, last_chain_replace_odometer
        FROM maintenance_settings
        WHERE user_id = ?
        """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def set_last_lube(user_id, odometer_km):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE maintenance_settings SET last_lube_odometer = ? WHERE user_id = ?",
        (odometer_km, user_id),
    )
    conn.commit()
    conn.close()


def set_last_chain_replace(user_id, odometer_km):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE maintenance_settings SET last_chain_replace_odometer = ? WHERE user_id = ?",
        (odometer_km, user_id),
    )
    conn.commit()
    conn.close()


def transmission_status_text(user_id):
    m = get_maintenance(user_id)
    odo = total_km(user_id)

    lube_since = odo - float(m["last_lube_odometer"])
    lube_left = float(m["lube_interval_km"]) - lube_since

    replace_since = odo - float(m["last_chain_replace_odometer"])
    replace_left = float(m["chain_replace_interval_km"]) - replace_since

    if lube_left <= 0:
        lube_text = f"Смазка: пора, перекатал на {-lube_left:.1f} км"
    else:
        lube_text = f"Смазка: через {lube_left:.1f} км"

    if replace_left <= 0:
        replace_text = f"Замена цепи: пора, перекатал на {-replace_left:.1f} км"
    else:
        replace_text = f"Замена цепи: через {replace_left:.1f} км"

    return (
        "⚙️ Трансмиссия\n"
        f"Общий пробег: {odo:.1f} км\n"
        f"Последняя смазка: на {float(m['last_lube_odometer']):.1f} км\n"
        f"Последняя замена цепи: на {float(m['last_chain_replace_odometer']):.1f} км\n"
        f"Интервал смазки: {float(m['lube_interval_km']):.0f} км\n"
        f"Интервал замены цепи: {float(m['chain_replace_interval_km']):.0f} км\n\n"
        f"{lube_text}\n{replace_text}\n\n"
        "Изменить можно командами:\n"
        "/set_lube 1234\n"
        "/set_chain 1500"
    )


def short_maintenance_warning(user_id):
    m = get_maintenance(user_id)
    odo = total_km(user_id)
    lube_left = float(m["lube_interval_km"]) - (odo - float(m["last_lube_odometer"]))

    if lube_left < 100:
        if lube_left <= 0:
            return f"⚠️ До смазки уже не осталось запаса — перекатал на {-lube_left:.1f} км."
        return f"⚠️ До смазки цепи осталось меньше 100 км: примерно {lube_left:.1f} км."
    return None


# ---------- UI ----------

def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить заезд", callback_data="help_add")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton("📚 Список заездов", callback_data="rides:0")],
            [InlineKeyboardButton("⚙️ Трансмиссия", callback_data="transmission")],
            [InlineKeyboardButton("💾 Бэкап", callback_data="backup")],
        ]
    )


def stats_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧨 Сбросить ВСЮ статистику", callback_data="reset_confirm")],
            [InlineKeyboardButton("⬅️ В меню", callback_data="menu")],
        ]
    )


def reset_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔴 Да, удалить всё", callback_data="reset_yes")],
            [InlineKeyboardButton("⚪ Отмена", callback_data="menu")],
        ]
    )


def rides_keyboard(offset, total_count, rows):
    buttons = []
    for row in rows:
        buttons.append([InlineKeyboardButton(f"✏️ Изменить #{row['id']}", callback_data=f"edit_prompt:{row['id']}")])

    nav = []
    prev_offset = max(offset - RIDES_PAGE_SIZE, 0)
    next_offset = offset + RIDES_PAGE_SIZE
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Предыдущие", callback_data=f"rides:{prev_offset}"))
    if next_offset < total_count:
        nav.append(InlineKeyboardButton("Следующие ➡️", callback_data=f"rides:{next_offset}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("⬅️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(buttons)


def send_text_target(update):
    if update.callback_query:
        return update.callback_query.message
    return update.message


async def reply_with_menu(update, text):
    target = send_text_target(update)
    await target.reply_text(text, reply_markup=main_keyboard())


# ---------- TEXT BUILDERS ----------

def first_start_text():
    return (
        "🚴 Привет. Это веложурнал-одометр.\n\n"
        "Он нужен, чтобы вручную записывать поездки без GPS, считать общий пробег и не пропускать обслуживание трансмиссии.\n\n"
        "Что умеет бот:\n"
        "• добавлять заезды за сегодня и задним числом\n"
        "• считать общий километраж и общее время\n"
        "• показывать список последних поездок\n"
        "• сравнивать скорость последнего заезда с предыдущими\n"
        "• подсказывать, когда смазать цепь и когда подумать о замене\n"
        "• делать бэкап данных\n\n"
        "Как быстро добавить поездку:\n"
        "25 90\n"
        "или\n"
        "2026-04-08 25 90 вечерний заезд\n\n"
        "Где 25 — километры, 90 — минуты."
    )


def regular_start_text():
    return (
        "🚴 Бот на месте."
        "\nМожно добавить поездку сообщением: 25 90"
        "\nИли открыть нужный раздел кнопками ниже."
    )


def added_ride_text(user_id, ride_date, km, minutes):
    avg_speed = calc_avg_speed(km, minutes)
    total = total_km(user_id)
    praise = pick_praise(km)
    maintenance_warning = short_maintenance_warning(user_id)

    parts = [
        f"Добавил заезд: {km:.1f} км за {minutes} мин.",
        praise,
        f"Средняя скорость: {format_speed(avg_speed)}.",
        f"Ты уже проехал {total:.1f} км, ВАУ!",
    ]
    if maintenance_warning:
        parts.append(maintenance_warning)
    return "\n".join(parts)


def pick_praise(km):
    if km >= 60:
        return "Вот это уже серьёзный выезд. Ноги запомнят, а бот запишет."
    if km >= 30:
        return "Хорошая тренировка. Ровный, уверенный прокат."
    if km >= 10:
        return "Неплохой заезд. Колёса не зря крутились."
    return "Даже короткий выезд — это всё равно движение вперёд."


def stats_text(user_id):
    count = rides_count(user_id)
    total_distance = total_km(user_id)
    total_duration = total_time(user_id)

    lines = [
        "📊 Статистика",
        f"Заездов: {count}",
        f"Общий километраж: {total_distance:.1f} км",
        f"Общее время в пути: {total_duration} мин",
    ]

    last_ride = get_last_ride(user_id)
    if not last_ride:
        lines.append("Тренд скорости: пока нет данных.")
    else:
        previous_avg = get_previous_rides_avg_speed(user_id)
        last_speed = calc_avg_speed(float(last_ride["distance_km"]), int(last_ride["duration_min"]))
        if previous_avg is None:
            lines.append(f"Тренд скорости: пока недостаточно заездов для сравнения. Последний темп — {format_speed(last_speed)}.")
        else:
            delta = last_speed - previous_avg
            if abs(delta) < 0.05:
                trend = "почти в том же темпе"
            elif delta > 0:
                trend = f"быстрее среднего на {delta:.1f} км/ч"
            else:
                trend = f"медленнее среднего на {abs(delta):.1f} км/ч"
            lines.append(
                f"Тренд скорости: последний заезд {trend}. Последний — {format_speed(last_speed)}, раньше в среднем — {format_speed(previous_avg)}."
            )

    m = get_maintenance(user_id)
    odo = total_distance
    lube_left = float(m["lube_interval_km"]) - (odo - float(m["last_lube_odometer"]))
    replace_left = float(m["chain_replace_interval_km"]) - (odo - float(m["last_chain_replace_odometer"]))

    if lube_left <= 0:
        lube_status = f"смазка нужна сейчас, перекатал на {-lube_left:.1f} км"
    else:
        lube_status = f"смазка примерно через {lube_left:.1f} км"

    if replace_left <= 0:
        replace_status = f"цепь пора менять, перекатал на {-replace_left:.1f} км"
    else:
        replace_status = f"замена цепи примерно через {replace_left:.1f} км"

    lines.append(f"Состояние трансмиссии: {lube_status}; {replace_status}.")
    return "
".join(lines)


def reset_warning_text():
    return (
        "🧨 Ты правда хочешь всё удалить?
"
        "Все заезды, километры и история исчезнут без возможности восстановления."
    )


def rides_page_text(user_id, offset):
    rows = list_rides_page(user_id, offset, RIDES_PAGE_SIZE)
    total_count = rides_total_count(user_id)
    if not rows:
        return "📚 Список заездов\nПока пусто.", rows, total_count

    start_num = offset + 1
    end_num = offset + len(rows)
    lines = [f"📚 Список заездов {start_num}-{end_num} из {total_count}"]
    for row in rows:
        lines.append("")
        lines.append(format_ride_line(row))
    return "\n".join(lines), rows, total_count


# ---------- COMMANDS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    created = ensure_user(update.effective_user.id)
    text = first_start_text() if created else regular_start_text()
    await reply_with_menu(update, text)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    target = send_text_target(update)
    await target.reply_text(stats_text(update.effective_user.id), reply_markup=stats_keyboard())


async def transmission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    await reply_with_menu(update, transmission_status_text(update.effective_user.id))


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rides WHERE user_id = ? ORDER BY ride_date ASC, id ASC", (user_id,))
    rides = [dict(r) for r in cur.fetchall()]
    conn.close()

    maintenance = dict(get_maintenance(user_id))
    data = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user_id": user_id,
        "stats": {
            "rides_count": rides_count(user_id),
            "total_km": total_km(user_id),
            "total_time_min": total_time(user_id),
        },
        "maintenance": maintenance,
        "rides": rides,
    }

    filename = f"bike_backup_{user_id}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    with open(filename, "rb") as f:
        target = send_text_target(update)
        await target.reply_document(
            document=f,
            filename=filename,
            caption="Вот бэкап. На случай если облака опять решат быть драматичными.",
            reply_markup=main_keyboard(),
        )

    os.remove(filename)


async def set_lube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    if not context.args:
        await reply_with_menu(update, "Нужно так: /set_lube 1234")
        return
    try:
        odometer = parse_float(context.args[0])
    except Exception:
        await reply_with_menu(update, "Пробег должен быть числом. Пример: /set_lube 1234")
        return

    set_last_lube(update.effective_user.id, odometer)
    await reply_with_menu(update, f"Готово. Последнюю смазку записал на {odometer:.1f} км.")


async def set_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    if not context.args:
        await reply_with_menu(update, "Нужно так: /set_chain 1500")
        return
    try:
        odometer = parse_float(context.args[0])
    except Exception:
        await reply_with_menu(update, "Пробег должен быть числом. Пример: /set_chain 1500")
        return

    set_last_chain_replace(update.effective_user.id, odometer)
    await reply_with_menu(update, f"Готово. Последнюю замену цепи записал на {odometer:.1f} км.")


def reset_all_user_data(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rides WHERE user_id = ?", (user_id,))
    cur.execute(
        """
        UPDATE maintenance_settings
        SET last_lube_odometer = 0,
            last_chain_replace_odometer = 0,
            lube_interval_km = ?,
            chain_replace_interval_km = ?
        WHERE user_id = ?
        """,
        (DEFAULT_LUBE_INTERVAL_KM, DEFAULT_CHAIN_REPLACE_INTERVAL_KM, user_id),
    )
    conn.commit()
    conn.close()


async def list_rides_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    text, rows, total_count = rides_page_text(update.effective_user.id, 0)
    target = send_text_target(update)
    await target.reply_text(text, reply_markup=rides_keyboard(0, total_count, rows))


async def edit_ride_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    if len(context.args) < 3:
        await reply_with_menu(
            update,
            "Формат такой:\n/edit_ride ID YYYY-MM-DD км минуты заметка\nПример:\n/edit_ride 7 2026-04-01 18 55 вечерний",
        )
        return

    try:
        ride_id = int(context.args[0])
        ride_date = parse_date(context.args[1])
        km = parse_float(context.args[2])
        minutes = parse_int(context.args[3])
        note = " ".join(context.args[4:]) if len(context.args) > 4 else ""
    except Exception:
        await reply_with_menu(
            update,
            "Формат такой:\n/edit_ride ID YYYY-MM-DD км минуты заметка\nПример:\n/edit_ride 7 2026-04-01 18 55 вечерний",
        )
        return

    changed = update_ride(update.effective_user.id, ride_id, ride_date, km, minutes, note)
    if not changed:
        await reply_with_menu(update, f"Не нашёл заезд #{ride_id}.")
        return

    speed = calc_avg_speed(km, minutes)
    await reply_with_menu(
        update,
        f"Заезд #{ride_id} обновил.\nДата: {ride_date}\nПробег: {km:.1f} км\nВремя: {minutes} мин\nСредняя скорость: {format_speed(speed)}",
    )


# ---------- QUICK INPUT ----------

async def quick_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("/"):
        return

    ensure_user(update.effective_user.id)
    parts = text.split()

    try:
        if len(parts) >= 2 and not looks_like_date(parts[0]):
            ride_date = today_str()
            km = parse_float(parts[0])
            minutes = parse_int(parts[1])
            note = " ".join(parts[2:]) if len(parts) > 2 else ""
        elif len(parts) >= 3 and looks_like_date(parts[0]):
            ride_date = parse_date(parts[0])
            km = parse_float(parts[1])
            minutes = parse_int(parts[2])
            note = " ".join(parts[3:]) if len(parts) > 3 else ""
        else:
            return
    except Exception:
        return

    add_ride(update.effective_user.id, ride_date, km, minutes, note)
    await reply_with_menu(update, added_ride_text(update.effective_user.id, ride_date, km, minutes))


def looks_like_date(value: str):
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# ---------- CALLBACKS ----------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    ensure_user(user_id)

    if data == "menu":
        await query.message.reply_text(regular_start_text(), reply_markup=main_keyboard())
        return

    if data == "stats":
        await query.message.reply_text(stats_text(user_id), reply_markup=stats_keyboard())
        return

    if data == "reset_confirm":
        await query.message.reply_text(reset_warning_text(), reply_markup=reset_confirm_keyboard())
        return

    if data == "reset_yes":
        reset_all_user_data(user_id)
        await query.message.reply_text("Всё очищено. Начинаем с чистого листа.", reply_markup=main_keyboard())
        return

    if data == "transmission":
        await query.message.reply_text(transmission_status_text(user_id), reply_markup=main_keyboard())
        return

    if data == "backup":
        await backup(update, context)
        return

    if data == "help_add":
        await query.message.reply_text(
            "Чтобы добавить заезд, просто пришли сообщение:\n25 90\nили\n2026-04-08 25 90 вечерний",
            reply_markup=main_keyboard(),
        )
        return

    if data.startswith("rides:"):
        offset = int(data.split(":", 1)[1])
        text, rows, total_count = rides_page_text(user_id, offset)
        await query.message.reply_text(text, reply_markup=rides_keyboard(offset, total_count, rows))
        return

    if data.startswith("edit_prompt:"):
        ride_id = int(data.split(":", 1)[1])
        ride = get_ride(user_id, ride_id)
        if not ride:
            await query.message.reply_text("Не нашёл этот заезд.", reply_markup=main_keyboard())
            return
        await query.message.reply_text(
            "Чтобы изменить поездку, пришли команду в таком виде:\n"
            f"/edit_ride {ride_id} {ride['ride_date']} {float(ride['distance_km']):.1f} {int(ride['duration_min'])} заметка\n\n"
            "Можешь поменять дату, километраж, время и заметку.",
            reply_markup=main_keyboard(),
        )
        return


# ---------- MAIN ----------

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Укажи переменную окружения TELEGRAM_BOT_TOKEN")

    init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("service", transmission))
    app.add_handler(CommandHandler("transmission", transmission))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CommandHandler("list", list_rides_command))
    app.add_handler(CommandHandler("edit_ride", edit_ride_command))
    app.add_handler(CommandHandler("set_lube", set_lube))
    app.add_handler(CommandHandler("set_chain", set_chain))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quick_input))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
