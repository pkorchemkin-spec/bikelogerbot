import os
import sqlite3
import json
from datetime import datetime

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
        CREATE TABLE IF NOT EXISTS maintenance (
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


# ---------- HELPERS ----------

def ensure_user(user_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO maintenance (
            user_id,
            lube_interval_km,
            chain_replace_interval_km,
            last_lube_odometer,
            last_chain_replace_odometer
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, DEFAULT_LUBE_INTERVAL_KM, DEFAULT_CHAIN_REPLACE_INTERVAL_KM, 0, 0),
    )
    created = cur.rowcount > 0
    conn.commit()
    conn.close()
    return created


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def looks_like_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def parse_date(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%d")
    return value


def parse_float(value: str) -> float:
    return float(value.replace(",", "."))


def parse_int(value: str) -> int:
    return int(value)


def avg_speed(distance_km: float, duration_min: int) -> float:
    if duration_min <= 0:
        return 0.0
    return distance_km / (duration_min / 60)


def fmt_speed(speed: float) -> str:
    return f"{speed:.1f} км/ч"


def praise_text(km: float) -> str:
    if km >= 60:
        return "Вот это уже серьёзный выезд. Хорошая работа."
    if km >= 30:
        return "Хорошая тренировка. Нормально покрутил."
    if km >= 10:
        return "Неплохой заезд. Вел точно не скучал."
    return "Даже короткий выезд — всё равно движение вперёд."


def send_target(update: Update):
    if update.callback_query:
        return update.callback_query.message
    return update.message


# ---------- RIDE DATA ----------

def add_ride(user_id: int, ride_date: str, distance_km: float, duration_min: int, note: str = "") -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rides (user_id, ride_date, distance_km, duration_min, note) VALUES (?, ?, ?, ?, ?)",
        (user_id, ride_date, distance_km, duration_min, note),
    )
    ride_id = cur.lastrowid
    conn.commit()
    conn.close()
    return ride_id


def update_ride(user_id: int, ride_id: int, ride_date: str, distance_km: float, duration_min: int, note: str = "") -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE rides
        SET ride_date = ?, distance_km = ?, duration_min = ?, note = ?
        WHERE user_id = ? AND id = ?
        """,
        (ride_date, distance_km, duration_min, note, user_id, ride_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_ride(user_id: int, ride_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rides WHERE user_id = ? AND id = ?", (user_id, ride_id))
    row = cur.fetchone()
    conn.close()
    return row


def rides_count(user_id: int) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM rides WHERE user_id = ?", (user_id,))
    value = int(cur.fetchone()[0])
    conn.close()
    return value


def total_km(user_id: int) -> float:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(distance_km), 0) FROM rides WHERE user_id = ?", (user_id,))
    value = float(cur.fetchone()[0])
    conn.close()
    return value


def total_time(user_id: int) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(duration_min), 0) FROM rides WHERE user_id = ?", (user_id,))
    value = int(cur.fetchone()[0])
    conn.close()
    return value


def get_last_ride(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM rides WHERE user_id = ? ORDER BY ride_date DESC, id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_previous_avg_speed(user_id: int):
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
    speeds = []
    for row in previous:
        duration = int(row["duration_min"])
        if duration > 0:
            speeds.append(avg_speed(float(row["distance_km"]), duration))

    if not speeds:
        return None
    return sum(speeds) / len(speeds)


def list_rides_page(user_id: int, offset: int = 0, limit: int = RIDES_PAGE_SIZE):
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


def reset_all_user_data(user_id: int) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rides WHERE user_id = ?", (user_id,))
    cur.execute(
        """
        UPDATE maintenance
        SET lube_interval_km = ?,
            chain_replace_interval_km = ?,
            last_lube_odometer = 0,
            last_chain_replace_odometer = 0
        WHERE user_id = ?
        """,
        (DEFAULT_LUBE_INTERVAL_KM, DEFAULT_CHAIN_REPLACE_INTERVAL_KM, user_id),
    )
    conn.commit()
    conn.close()


# ---------- MAINTENANCE ----------

def get_maintenance(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM maintenance WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def set_last_lube(user_id: int, odometer_km: float) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE maintenance SET last_lube_odometer = ? WHERE user_id = ?", (odometer_km, user_id))
    conn.commit()
    conn.close()


def set_last_chain_replace(user_id: int, odometer_km: float) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE maintenance SET last_chain_replace_odometer = ? WHERE user_id = ?", (odometer_km, user_id))
    conn.commit()
    conn.close()


def maintenance_warning(user_id: int):
    row = get_maintenance(user_id)
    if not row:
        return None
    remaining = float(row["lube_interval_km"]) - (total_km(user_id) - float(row["last_lube_odometer"]))
    if remaining < 100:
        if remaining <= 0:
            return f"⚠️ Смазку уже пора делать. Перекатал на {-remaining:.1f} км."
        return f"⚠️ До смазки цепи осталось меньше 100 км: примерно {remaining:.1f} км."
    return None


def transmission_text(user_id: int) -> str:
    row = get_maintenance(user_id)
    odo = total_km(user_id)

    lube_left = float(row["lube_interval_km"]) - (odo - float(row["last_lube_odometer"]))
    chain_left = float(row["chain_replace_interval_km"]) - (odo - float(row["last_chain_replace_odometer"]))

    if lube_left <= 0:
        lube_status = f"Смазка: пора, перекатал на {-lube_left:.1f} км"
    else:
        lube_status = f"Смазка: примерно через {lube_left:.1f} км"

    if chain_left <= 0:
        chain_status = f"Замена цепи: пора, перекатал на {-chain_left:.1f} км"
    else:
        chain_status = f"Замена цепи: примерно через {chain_left:.1f} км"

    return (
        "⚙️ Трансмиссия\n"
        f"Общий пробег: {odo:.1f} км\n"
        f"Последняя смазка: на {float(row['last_lube_odometer']):.1f} км\n"
        f"Последняя замена цепи: на {float(row['last_chain_replace_odometer']):.1f} км\n"
        f"Интервал смазки: {float(row['lube_interval_km']):.0f} км\n"
        f"Интервал замены цепи: {float(row['chain_replace_interval_km']):.0f} км\n\n"
        f"{lube_status}\n{chain_status}\n\n"
        "Изменить значения можно так:\n"
        "/set_lube 1234\n"
        "/set_chain 1500"
    )


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


def rides_keyboard(offset: int, total_count: int, rows) -> InlineKeyboardMarkup:
    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(f"✏️ Изменить #{row['id']}", callback_data=f"edit_prompt:{row['id']}")
        ])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Предыдущие", callback_data=f"rides:{max(offset - RIDES_PAGE_SIZE, 0)}"))
    if offset + RIDES_PAGE_SIZE < total_count:
        nav.append(InlineKeyboardButton("Следующие ➡️", callback_data=f"rides:{offset + RIDES_PAGE_SIZE}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("⬅️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(buttons)


# ---------- TEXT BUILDERS ----------

def first_start_text() -> str:
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


def regular_start_text() -> str:
    return (
        "🚴 Бот на месте.\n"
        "Можно добавить поездку сообщением: 25 90\n"
        "Или открыть нужный раздел кнопками ниже."
    )


def added_ride_text(user_id: int, km: float, minutes: int) -> str:
    speed = avg_speed(km, minutes)
    total_distance = total_km(user_id)
    lines = [
        f"Добавил заезд: {km:.1f} км.",
        praise_text(km),
        f"Средняя скорость: {fmt_speed(speed)}.",
        f"Ты уже проехал {total_distance:.1f} км, ВАУ!",
    ]
    warning = maintenance_warning(user_id)
    if warning:
        lines.append(warning)
    return "\n".join(lines)


def stats_text(user_id: int) -> str:
    count = rides_count(user_id)
    distance = total_km(user_id)
    duration = total_time(user_id)
    lines = [
        "📊 Статистика",
        f"Количество заездов: {count}",
        f"Общий километраж: {distance:.1f} км",
        f"Общее время в пути: {duration} мин",
    ]

    last_ride = get_last_ride(user_id)
    if not last_ride:
        lines.append("Тренд средней скорости: пока нет данных.")
    else:
        last_speed = avg_speed(float(last_ride["distance_km"]), int(last_ride["duration_min"]))
        prev_avg = get_previous_avg_speed(user_id)
        if prev_avg is None:
            lines.append(f"Тренд средней скорости: пока недостаточно данных для сравнения. Последний темп — {fmt_speed(last_speed)}.")
        else:
            delta = last_speed - prev_avg
            if abs(delta) < 0.05:
                trend = "почти в том же темпе"
            elif delta > 0:
                trend = f"быстрее среднего на {delta:.1f} км/ч"
            else:
                trend = f"медленнее среднего на {abs(delta):.1f} км/ч"
            lines.append(
                f"Тренд средней скорости: последний заезд {trend}. Последний — {fmt_speed(last_speed)}, предыдущие в среднем — {fmt_speed(prev_avg)}."
            )

    row = get_maintenance(user_id)
    lube_left = float(row["lube_interval_km"]) - (distance - float(row["last_lube_odometer"]))
    chain_left = float(row["chain_replace_interval_km"]) - (distance - float(row["last_chain_replace_odometer"]))

    if lube_left <= 0:
        lube_text = f"смазка нужна сейчас, перекатал на {-lube_left:.1f} км"
    else:
        lube_text = f"смазка примерно через {lube_left:.1f} км"

    if chain_left <= 0:
        chain_text = f"цепь пора менять, перекатал на {-chain_left:.1f} км"
    else:
        chain_text = f"замена цепи примерно через {chain_left:.1f} км"

    lines.append(f"Состояние трансмиссии: {lube_text}; {chain_text}.")
    return "\n".join(lines)


def reset_warning_text() -> str:
    return (
        "🧨 Ты правда хочешь всё удалить?\n"
        "Все заезды, километры и история исчезнут без возможности восстановления."
    )


def rides_page_text(user_id: int, offset: int):
    rows = list_rides_page(user_id, offset, RIDES_PAGE_SIZE)
    total_count = rides_count(user_id)
    if not rows:
        return "📚 Список заездов\nПока пусто.", rows, total_count

    start_num = offset + 1
    end_num = offset + len(rows)
    lines = [f"📚 Список заездов {start_num}-{end_num} из {total_count}"]

    for row in rows:
        speed = avg_speed(float(row["distance_km"]), int(row["duration_min"]))
        note = f" · {row['note']}" if row["note"] else ""
        lines.append("")
        lines.append(
            f"#{row['id']} · {row['ride_date']}\n"
            f"{float(row['distance_km']):.1f} км · {int(row['duration_min'])} мин · {fmt_speed(speed)}{note}"
        )

    return "\n".join(lines), rows, total_count


# ---------- COMMANDS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    created = ensure_user(update.effective_user.id)
    await update.message.reply_text(
        first_start_text() if created else regular_start_text(),
        reply_markup=main_keyboard(),
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    send_to = send_target(update)
    await send_to.reply_text(stats_text(update.effective_user.id), reply_markup=stats_keyboard())


async def transmission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    send_to = send_target(update)
    await send_to.reply_text(transmission_text(update.effective_user.id), reply_markup=main_keyboard())


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rides WHERE user_id = ? ORDER BY ride_date ASC, id ASC", (user_id,))
    rides = [dict(r) for r in cur.fetchall()]
    conn.close()

    data = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user_id": user_id,
        "stats": {
            "rides_count": rides_count(user_id),
            "total_km": total_km(user_id),
            "total_time_min": total_time(user_id),
        },
        "maintenance": dict(get_maintenance(user_id)),
        "rides": rides,
    }

    filename = f"bike_backup_{user_id}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    send_to = send_target(update)
    with open(filename, "rb") as f:
        await send_to.reply_document(
            document=f,
            filename=filename,
            caption="Вот бэкап. На случай если облака опять решат быть драматичными.",
            reply_markup=main_keyboard(),
        )
    os.remove(filename)


async def set_lube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Нужно так: /set_lube 1234", reply_markup=main_keyboard())
        return
    try:
        value = parse_float(context.args[0])
    except Exception:
        await update.message.reply_text("Пробег должен быть числом. Пример: /set_lube 1234", reply_markup=main_keyboard())
        return
    set_last_lube(update.effective_user.id, value)
    await update.message.reply_text(f"Готово. Последнюю смазку записал на {value:.1f} км.", reply_markup=main_keyboard())


async def set_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Нужно так: /set_chain 1500", reply_markup=main_keyboard())
        return
    try:
        value = parse_float(context.args[0])
    except Exception:
        await update.message.reply_text("Пробег должен быть числом. Пример: /set_chain 1500", reply_markup=main_keyboard())
        return
    set_last_chain_replace(update.effective_user.id, value)
    await update.message.reply_text(f"Готово. Последнюю замену цепи записал на {value:.1f} км.", reply_markup=main_keyboard())


async def list_rides_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    text, rows, total_count = rides_page_text(update.effective_user.id, 0)
    await update.message.reply_text(text, reply_markup=rides_keyboard(0, total_count, rows))


async def edit_ride_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    if len(context.args) < 4:
        await update.message.reply_text(
            "Формат такой:\n/edit_ride ID YYYY-MM-DD км минуты заметка\nПример:\n/edit_ride 7 2026-04-01 18 55 вечерний",
            reply_markup=main_keyboard(),
        )
        return

    try:
        ride_id = int(context.args[0])
        ride_date = parse_date(context.args[1])
        km = parse_float(context.args[2])
        minutes = parse_int(context.args[3])
        note = " ".join(context.args[4:]) if len(context.args) > 4 else ""
    except Exception:
        await update.message.reply_text(
            "Формат такой:\n/edit_ride ID YYYY-MM-DD км минуты заметка\nПример:\n/edit_ride 7 2026-04-01 18 55 вечерний",
            reply_markup=main_keyboard(),
        )
        return

    changed = update_ride(update.effective_user.id, ride_id, ride_date, km, minutes, note)
    if not changed:
        await update.message.reply_text(f"Не нашёл заезд #{ride_id}.", reply_markup=main_keyboard())
        return

    await update.message.reply_text(
        f"Заезд #{ride_id} обновил.\nДата: {ride_date}\nПробег: {km:.1f} км\nВремя: {minutes} мин\nСредняя скорость: {fmt_speed(avg_speed(km, minutes))}",
        reply_markup=main_keyboard(),
    )


# ---------- QUICK INPUT ----------

async def quick_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

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
    await update.message.reply_text(added_ride_text(update.effective_user.id, km, minutes), reply_markup=main_keyboard())


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

    if data == "help_add":
        await query.message.reply_text(
            "Чтобы добавить заезд, просто пришли сообщение:\n25 90\nили\n2026-04-08 25 90 вечерний",
            reply_markup=main_keyboard(),
        )
        return

    if data == "stats":
        await query.message.reply_text(stats_text(user_id), reply_markup=stats_keyboard())
        return

    if data == "transmission":
        await query.message.reply_text(transmission_text(user_id), reply_markup=main_keyboard())
        return

    if data == "backup":
        await backup(update, context)
        return

    if data == "reset_confirm":
        await query.message.reply_text(reset_warning_text(), reply_markup=reset_confirm_keyboard())
        return

    if data == "reset_yes":
        reset_all_user_data(user_id)
        await query.message.reply_text("Всё очищено. Начинаем с чистого листа.", reply_markup=main_keyboard())
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
