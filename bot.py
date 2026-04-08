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


def ordinal_ride(n: int) -> str:
    return f"{n}-й заезд"


def avg_speed(km: float, minutes: int) -> float:
    if minutes <= 0:
        return 0.0
    return km / (minutes / 60)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def parse_float(value: str) -> float:
    return float(value.replace(",", "."))


def parse_duration(value: str) -> int:
    value = value.strip()

    if value.isdigit():
        return int(value)

    for sep in (":", ",", "."):
        if sep in value:
            parts = value.split(sep)
            if len(parts) != 2:
                break

            h, m = parts
            if not h.isdigit() or not m.isdigit():
                break

            return int(h) * 60 + int(m)

    raise ValueError


def looks_like_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def add_km_reaction(km: float) -> str:
    if km >= 80:
        return "Ничего себе дистанция. Это уже почти маленькое путешествие."
    if km >= 50:
        return "Солидно. Уже чувствуется характер."
    if km >= 25:
        return "Хорошая дистанция. Приятно звучит."
    if km >= 10:
        return "Нормальный выезд. Вел доволен."
    return "Коротко, но по делу."


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


def rides_count(user_id: int) -> int:
    conn = db()
    value = conn.execute(
        "SELECT COUNT(*) FROM rides WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    conn.close()
    return int(value)


# ---------- STATE ----------

def clear_add_state(context):
    context.user_data.pop("pending_add_step", None)
    context.user_data.pop("pending_add_data", None)


# ---------- TEXT ----------

def first_start_text(name=None):
    return f"🚴 Привет, {name}.\n\nЭто твой веложурнал."


def regular_start_text():
    return "🚴 Бот на месте."


def add_intro_text():
    return (
        "➕ Добавление заезда\n\n"
        "Давай спокойно запишем поездку по шагам.\n\n"
        "С какого числа был заезд?\n"
        "YYYY-MM-DD"
    )


def add_ask_km_text():
    return (
        "Отлично, дату запомнил.\n\n"
        "Сколько проехал?\n"
        "Можно просто числом:\n"
        "25 или 25.5"
    )


def add_ask_time_text(km):
    return (
        f"Принял, {km:.1f} км.\n\n"
        "Сколько заняла поездка?\n"
        "Можно так:\n"
        "90 или 1:30"
    )


def add_ask_note_text(minutes):
    return (
        f"Окей, время: {format_time(minutes)}.\n\n"
        "Добавить заметку?\n"
        "Если нет — отправь -"
    )


def add_done_text(user_id, ride_date, km, minutes, note):
    ride_number = rides_count(user_id)
    return (
        f"Это твой {ordinal_ride(ride_number)}.\n"
        f"{ride_date}\n"
        f"{km:.1f} км за {format_time(minutes)}"
    )


# ---------- UI ----------

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить заезд", callback_data="add_start")],
    ])


def add_kb_first():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ В меню", callback_data="add_cancel")]
    ])


def add_kb_next():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="add_back")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="add_cancel")]
    ])


# ---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    await update.message.reply_text(first_start_text(update.effective_user.first_name), reply_markup=main_kb())


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_start":
        context.user_data["pending_add_step"] = "date"
        context.user_data["pending_add_data"] = {}
        await query.message.reply_text(add_intro_text(), reply_markup=add_kb_first())
        return

    if query.data == "add_cancel":
        clear_add_state(context)
        await query.message.reply_text(regular_start_text(), reply_markup=main_kb())
        return


async def quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    step = context.user_data.get("pending_add_step")
    data = context.user_data.get("pending_add_data", {})

    if step == "date":
        data["date"] = text
        context.user_data["pending_add_step"] = "km"
        await update.message.reply_text(add_ask_km_text(), reply_markup=add_kb_next())
        return

    if step == "km":
        km = parse_float(text)
        data["km"] = km
        context.user_data["pending_add_step"] = "time"
        await update.message.reply_text(add_km_reaction(km) + "\n\n" + add_ask_time_text(km), reply_markup=add_kb_next())
        return

    if step == "time":
        minutes = parse_duration(text)
        data["minutes"] = minutes
        context.user_data["pending_add_step"] = "note"
        await update.message.reply_text(add_ask_note_text(minutes), reply_markup=add_kb_next())
        return

    if step == "note":
        note = "" if text == "-" else text
        add_ride(user_id, data["date"], data["km"], data["minutes"], note)
        clear_add_state(context)

        await update.message.reply_text(
            add_done_text(user_id, data["date"], data["km"], data["minutes"], note),
            reply_markup=main_kb(),
        )


# ---------- MAIN ----------

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    init()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quick))

    app.run_polling()


if __name__ == "__main__":
    main()