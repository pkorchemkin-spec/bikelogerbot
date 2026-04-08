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
DEFAULT_LUBE_INTERVAL_KM = 250
DEFAULT_CHAIN_REPLACE_INTERVAL_KM = 500
RIDES_PAGE_SIZE = 5


# ---------- UTILS ----------

def format_time(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    if hours == 0:
        return f"{mins}м"
    return f"{hours}ч {mins:02d}м"


def avg_speed(km: float, minutes: int) -> float:
    if minutes <= 0:
        return 0.0
    return km / (minutes / 60)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def parse_float(value: str) -> float:
    return float(value.replace(",", "."))


def parse_int(value: str) -> int:
    return int(value)


def looks_like_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# ---------- DB ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        km REAL NOT NULL,
        min INTEGER NOT NULL,
        note TEXT DEFAULT ''
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS maintenance (
        user_id INTEGER PRIMARY KEY,
        last_lube REAL NOT NULL DEFAULT 0,
        last_chain REAL NOT NULL DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()


def ensure_user(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO maintenance (user_id, last_lube, last_chain) VALUES (?, 0, 0)",
        (user_id,),
    )
    created = cur.rowcount > 0
    conn.commit()
    conn.close()
    return created


# ---------- DATA ----------

def add_ride(user_id: int, ride_date: str, km: float, minutes: int, note: str = "") -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rides (user_id, date, km, min, note) VALUES (?, ?, ?, ?, ?)",
        (user_id, ride_date, km, minutes, note),
    )
    conn.commit()
    conn.close()


def update_ride(user_id: int, ride_id: int, ride_date: str, km: float, minutes: int, note: str = "") -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE rides
        SET date = ?, km = ?, min = ?, note = ?
        WHERE user_id = ? AND id = ?
        """,
        (ride_date, km, minutes, note, user_id, ride_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_ride(user_id: int, ride_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM rides WHERE user_id = ? AND id = ?",
        (user_id, ride_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_ride(user_id: int, ride_id: int):
    conn = db()
    row = conn.execute(
        "SELECT * FROM rides WHERE user_id = ? AND id = ?",
        (user_id, ride_id),
    ).fetchone()
    conn.close()
    return row


def total_km(user_id: int) -> float:
    conn = db()
    value = conn.execute(
        "SELECT COALESCE(SUM(km), 0) FROM rides WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    conn.close()
    return float(value)


def total_time(user_id: int) -> int:
    conn = db()
    value = conn.execute(
        "SELECT COALESCE(SUM(min), 0) FROM rides WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    conn.close()
    return int(value)


def rides_count(user_id: int) -> int:
    conn = db()
    value = conn.execute(
        "SELECT COUNT(*) FROM rides WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    conn.close()
    return int(value)


def all_rides(user_id: int):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM rides WHERE user_id = ? ORDER BY date DESC, id DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


def rides_page(user_id: int, offset: int, limit: int = RIDES_PAGE_SIZE):
    conn = db()
    rows = conn.execute(
        """
        SELECT * FROM rides
        WHERE user_id = ?
        ORDER BY date DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    ).fetchall()
    conn.close()
    return rows


def reset_user_data(user_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rides WHERE user_id = ?", (user_id,))
    cur.execute(
        "UPDATE maintenance SET last_lube = 0, last_chain = 0 WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def get_maintenance(user_id: int):
    conn = db()
    row = conn.execute(
        "SELECT * FROM maintenance WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


# ---------- TEXT ----------

def first_start_text() -> str:
    return (
        "🚴 Привет. Это веложурнал-одометр.\n\n"
        "Он нужен, чтобы вручную записывать поездки без GPS, считать общий пробег "
        "и держать обслуживание велосипеда под контролем.\n\n"
        "Что умеет бот:\n"
        "• добавлять заезды вручную\n"
        "• считать общий километраж и общее время\n"
        "• показывать краткую сводку\n"
        "• хранить историю поездок\n"
        "• делать бэкап\n\n"
        "Как быстро добавить поездку:\n"
        "25 90\n\n"
        "Где 25 — километры, 90 — минуты."
    )


def regular_start_text() -> str:
    return (
        "🚴 Бот на месте.\n"
        "Можно добавить поездку сообщением: 25 90\n"
        "Или открыть нужный раздел кнопками ниже."
    )


def praise_text(km: float) -> str:
    if km >= 60:
        return "Вот это уже серьёзный выезд. Хорошая работа."
    if km >= 30:
        return "Хорошая тренировка. Нормально покрутил."
    if km >= 10:
        return "Неплохой заезд. Вел точно не скучал."
    return "Даже короткий выезд — всё равно движение вперёд."


def maintenance_warning_text(user_id: int):
    row = get_maintenance(user_id)
    if row is None:
        return None

    left = DEFAULT_LUBE_INTERVAL_KM - (total_km(user_id) - float(row["last_lube"]))
    if left < 100:
        if left <= 0:
            return f"⚠️ Смазку уже пора делать. Перекатал на {-left:.1f} км."
        return f"⚠️ До смазки цепи осталось меньше 100 км: примерно {left:.1f} км."
    return None


def added_ride_text(user_id: int, km: float, minutes: int) -> str:
    lines = [
        f"Добавил заезд: {km:.1f} км.",
        praise_text(km),
        f"Время: {format_time(minutes)}.",
        f"Средняя скорость: {avg_speed(km, minutes):.1f} км/ч.",
        f"Ты уже проехал {total_km(user_id):.1f} км, ВАУ!",
    ]
    warning = maintenance_warning_text(user_id)
    if warning:
        lines.append(warning)
    return "\n".join(lines)


def summary_text(user_id: int) -> str:
    rides = all_rides(user_id)
    if not rides:
        return (
            "📊 Краткая сводка\n"
            "Пока нет данных.\n"
            "Добавь первый заезд, и бот начнёт вести историю."
        )

    avg = sum(avg_speed(float(r["km"]), int(r["min"])) for r in rides) / len(rides)

    row = get_maintenance(user_id)
    total = total_km(user_id)

    lube_left = DEFAULT_LUBE_INTERVAL_KM - (total - float(row["last_lube"]))
    chain_left = DEFAULT_CHAIN_REPLACE_INTERVAL_KM - (total - float(row["last_chain"]))

    if lube_left <= 0:
        lube_text = f"смазка нужна сейчас, перекатал на {-lube_left:.1f} км"
    else:
        lube_text = f"смазка примерно через {lube_left:.1f} км"

    if chain_left <= 0:
        chain_text = f"цепь пора менять, перекатал на {-chain_left:.1f} км"
    else:
        chain_text = f"замена цепи примерно через {chain_left:.1f} км"

    return (
        f"📊 Краткая сводка\n"
        f"Количество заездов: {rides_count(user_id)}\n"
        f"Общий километраж: {total:.1f} км\n"
        f"Общее время в пути: {format_time(total_time(user_id))}\n"
        f"Средняя скорость: {avg:.1f} км/ч\n"
        f"Состояние трансмиссии: {lube_text}; {chain_text}."
    )


def transmission_text(user_id: int) -> str:
    row = get_maintenance(user_id)
    total = total_km(user_id)

    lube_left = DEFAULT_LUBE_INTERVAL_KM - (total - float(row["last_lube"]))
    chain_left = DEFAULT_CHAIN_REPLACE_INTERVAL_KM - (total - float(row["last_chain"]))

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
        f"Общий пробег: {total:.1f} км\n"
        f"Последняя смазка: на {float(row['last_lube']):.1f} км\n"
        f"Последняя замена цепи: на {float(row['last_chain']):.1f} км\n\n"
        f"{lube_status}\n"
        f"{chain_status}"
    )


def rides_text(user_id: int, offset: int) -> str:
    rows = rides_page(user_id, offset)
    total = rides_count(user_id)

    if not rows:
        return "📚 Статистика\nПока нет заездов."

    start_idx = offset + 1
    end_idx = offset + len(rows)

    lines = [f"📚 Статистика\nПоказаны заезды {start_idx}-{end_idx} из {total}"]

    for r in rows:
        lines.append(
            f"\n{r['date']} | {float(r['km']):.1f} км | {format_time(int(r['min']))} | "
            f"{avg_speed(float(r['km']), int(r['min'])):.1f} км/ч"
        )

    return "\n".join(lines)


def reset_warning_text() -> str:
    return (
        "🧨 Ты правда хочешь всё удалить?\n"
        "Все заезды, километры и история исчезнут без возможности восстановления."
    )


# ---------- UI ----------

def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить", callback_data="help")],
        [InlineKeyboardButton("📊 Краткая сводка", callback_data="summary")],
        [InlineKeyboardButton("📚 Статистика", callback_data="rides:0")],
        [InlineKeyboardButton("⚙️ Трансмиссия", callback_data="trans")],
    ])


def rides_kb(offset: int, total: int, ride_rows) -> InlineKeyboardMarkup:
    rows = []

    for ride in ride_rows:
        rows.append([
            InlineKeyboardButton(
                f"✏️ Изменить {ride['date']} · {float(ride['km']):.1f} км",
                callback_data=f"edit:{ride['id']}:{offset}",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                f"🗑 Удалить {ride['date']} · {float(ride['km']):.1f} км",
                callback_data=f"delete_confirm:{ride['id']}:{offset}",
            )
        ])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"rides:{max(offset - RIDES_PAGE_SIZE, 0)}"))
    if offset + RIDES_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"rides:{offset + RIDES_PAGE_SIZE}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("💾 Бэкап", callback_data="backup")])
    rows.append([InlineKeyboardButton("🧨 Сбросить ВСЮ статистику", callback_data="reset")])
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="menu")])

    return InlineKeyboardMarkup(rows)


def delete_confirm_kb(ride_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Да, удалить", callback_data=f"delete_yes:{ride_id}:{offset}")],
        [InlineKeyboardButton("⚪ Отмена", callback_data=f"rides:{offset}")],
    ])


def reset_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Да, удалить всё", callback_data="reset_yes")],
        [InlineKeyboardButton("⚪ Отмена", callback_data="rides:0")],
    ])


# ---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    created = ensure_user(update.effective_user.id)
    await update.message.reply_text(
        first_start_text() if created else regular_start_text(),
        reply_markup=main_kb(),
    )


async def quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("/"):
        return

    user_id = update.effective_user.id
    ensure_user(user_id)

    pending_edit_id = context.user_data.get("pending_edit_ride_id")
    pending_edit_offset = context.user_data.get("pending_edit_offset", 0)

    parts = text.split()

    if pending_edit_id:
        try:
            if len(parts) < 3 or not looks_like_date(parts[0]):
                raise ValueError
            ride_date = parts[0]
            km = parse_float(parts[1])
            minutes = parse_int(parts[2])
            note = " ".join(parts[3:]) if len(parts) > 3 else ""
        except Exception:
            current_rows = rides_page(user_id, pending_edit_offset)
            await update.message.reply_text(
                "Для редактирования пришли так:\nYYYY-MM-DD км минуты заметка",
                reply_markup=rides_kb(pending_edit_offset, rides_count(user_id), current_rows),
            )
            return

        changed = update_ride(user_id, pending_edit_id, ride_date, km, minutes, note)

        context.user_data.pop("pending_edit_ride_id", None)
        context.user_data.pop("pending_edit_offset", None)

        current_rows = rides_page(user_id, pending_edit_offset)

        if not changed:
            await update.message.reply_text(
                "Не смог обновить этот заезд.",
                reply_markup=rides_kb(pending_edit_offset, rides_count(user_id), current_rows),
            )
            return

        await update.message.reply_text(
            "Заезд обновил.\n"
            f"Дата: {ride_date}\n"
            f"Дистанция: {km:.1f} км\n"
            f"Время: {format_time(minutes)}\n"
            f"Средняя скорость: {avg_speed(km, minutes):.1f} км/ч",
            reply_markup=rides_kb(pending_edit_offset, rides_count(user_id), current_rows),
        )
        return

    try:
        if len(parts) >= 2 and not looks_like_date(parts[0]):
            ride_date = today_str()
            km = parse_float(parts[0])
            minutes = parse_int(parts[1])
            note = " ".join(parts[2:]) if len(parts) > 2 else ""
        elif len(parts) >= 3 and looks_like_date(parts[0]):
            ride_date = parts[0]
            km = parse_float(parts[1])
            minutes = parse_int(parts[2])
            note = " ".join(parts[3:]) if len(parts) > 3 else ""
        else:
            return
    except Exception:
        return

    add_ride(user_id, ride_date, km, minutes, note)

    await update.message.reply_text(
        added_ride_text(user_id, km, minutes),
        reply_markup=main_kb(),
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    ensure_user(user_id)

    if query.data == "menu":
        await query.message.reply_text(
            regular_start_text(),
            reply_markup=main_kb(),
        )
        return

    if query.data == "help":
        await query.message.reply_text(
            "Чтобы добавить поездку, просто пришли сообщение:\n"
            "25 90\n\n"
            "Или с датой:\n"
            "2026-04-08 25 90 вечерний",
            reply_markup=main_kb(),
        )
        return

    if query.data == "summary":
        await query.message.reply_text(
            summary_text(user_id),
            reply_markup=main_kb(),
        )
        return

    if query.data == "trans":
        await query.message.reply_text(
            transmission_text(user_id),
            reply_markup=main_kb(),
        )
        return

    if query.data.startswith("rides:"):
        offset = int(query.data.split(":")[1])
        rows = rides_page(user_id, offset)
        await query.message.reply_text(
            rides_text(user_id, offset),
            reply_markup=rides_kb(offset, rides_count(user_id), rows),
        )
        return

    if query.data.startswith("edit:"):
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        ride = get_ride(user_id, ride_id)
        current_rows = rides_page(user_id, offset)

        if not ride:
            await query.message.reply_text(
                "Не нашёл этот заезд.",
                reply_markup=rides_kb(offset, rides_count(user_id), current_rows),
            )
            return

        context.user_data["pending_edit_ride_id"] = ride_id
        context.user_data["pending_edit_offset"] = offset

        note_text = ride["note"] if ride["note"] else ""
        await query.message.reply_text(
            "Пришли новые данные одним сообщением:\n"
            "YYYY-MM-DD км минуты заметка\n\n"
            f"Сейчас: {ride['date']} {float(ride['km']):.1f} {int(ride['min'])} {note_text}",
            reply_markup=rides_kb(offset, rides_count(user_id), current_rows),
        )
        return

    if query.data.startswith("delete_confirm:"):
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        ride = get_ride(user_id, ride_id)
        if not ride:
            current_rows = rides_page(user_id, offset)
            await query.message.reply_text(
                "Не нашёл этот заезд.",
                reply_markup=rides_kb(offset, rides_count(user_id), current_rows),
            )
            return

        await query.message.reply_text(
            "Удалить этот заезд?",
            reply_markup=delete_confirm_kb(ride_id, offset),
        )
        return

    if query.data.startswith("delete_yes:"):
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        delete_ride(user_id, ride_id)

        total = rides_count(user_id)
        if offset >= total and offset > 0:
            offset = max(0, offset - RIDES_PAGE_SIZE)

        rows = rides_page(user_id, offset)

        await query.message.reply_text(
            "Заезд удалил.",
            reply_markup=rides_kb(offset, rides_count(user_id), rows),
        )
        await query.message.reply_text(
            rides_text(user_id, offset),
            reply_markup=rides_kb(offset, rides_count(user_id), rows),
        )
        return

    if query.data == "backup":
        rides = [dict(r) for r in all_rides(user_id)]
        payload = {
            "user_id": user_id,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "summary": {
                "rides_count": rides_count(user_id),
                "total_km": total_km(user_id),
                "total_time_min": total_time(user_id),
            },
            "maintenance": dict(get_maintenance(user_id)),
            "rides": rides,
        }

        filename = "backup.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        with open(filename, "rb") as f:
            await query.message.reply_document(f)

        os.remove(filename)
        return

    if query.data == "reset":
        await query.message.reply_text(
            reset_warning_text(),
            reply_markup=reset_kb(),
        )
        return

    if query.data == "reset_yes":
        reset_user_data(user_id)
        await query.message.reply_text(
            "Всё очищено. Начинаем с чистого листа.",
            reply_markup=main_kb(),
        )
        return


# ---------- MAIN ----------

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Укажи переменную окружения TELEGRAM_BOT_TOKEN")

    init()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quick))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()