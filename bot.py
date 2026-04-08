import os
import sqlite3
import json
from datetime import datetime
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

DB_PATH = "bike_log.db"
DEFAULT_SERVICE_INTERVAL_KM = 150.0


# ---------- DB ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        ride_date TEXT,
        distance_km REAL,
        duration_min INTEGER,
        note TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS service_settings (
        user_id INTEGER PRIMARY KEY,
        chain_interval_km REAL,
        last_chain_service_odometer REAL
    )
    """)

    conn.commit()
    conn.close()


def ensure_user(user_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR IGNORE INTO service_settings (user_id, chain_interval_km, last_chain_service_odometer)
    VALUES (?, ?, ?)
    """, (user_id, DEFAULT_SERVICE_INTERVAL_KM, 0))

    conn.commit()
    conn.close()


# ---------- DATA ----------

def add_ride(user_id, date, km, minutes, note):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO rides (user_id, ride_date, distance_km, duration_min, note)
    VALUES (?, ?, ?, ?, ?)
    """, (user_id, date, km, minutes, note))

    conn.commit()
    conn.close()


def total_km(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(distance_km),0) FROM rides WHERE user_id=?", (user_id,))
    val = cur.fetchone()[0]
    conn.close()
    return val


def total_time(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(duration_min),0) FROM rides WHERE user_id=?", (user_id,))
    val = cur.fetchone()[0]
    conn.close()
    return val


def rides_count(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM rides WHERE user_id=?", (user_id,))
    val = cur.fetchone()[0]
    conn.close()
    return val


def get_settings(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT chain_interval_km, last_chain_service_odometer FROM service_settings WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def mark_chain(user_id):
    km = total_km(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE service_settings SET last_chain_service_odometer=? WHERE user_id=?", (km, user_id))
    conn.commit()
    conn.close()
    return km


# ---------- UI ----------

def keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить", callback_data="add")],
        [InlineKeyboardButton("📟 Статистика", callback_data="stats"),
         InlineKeyboardButton("🛠 Цепь", callback_data="service")],
        [InlineKeyboardButton("💾 Бэкап", callback_data="backup")]
    ])


def key(chat_id, user_id):
    return f"{chat_id}:{user_id}"


async def clear_menu(context, chat_id, user_id):
    mid = context.bot_data.get(key(chat_id, user_id))
    if not mid:
        return
    try:
        await context.bot.edit_message_reply_markup(chat_id, mid, reply_markup=None)
    except BadRequest:
        pass


async def send(update, context, text):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await clear_menu(context, chat_id, user_id)

    msg = await update.message.reply_text(text, reply_markup=keyboard())
    context.bot_data[key(chat_id, user_id)] = msg.message_id


# ---------- COMMANDS ----------

async def start(update, context):
    ensure_user(update.effective_user.id)
    await send(update, context,
        "🚴 Журнал готов\n\n"
        "Просто пиши: 25 90\n"
        "или: 25 90 вечер\n"
    )


async def stats(update, context):
    u = update.effective_user.id
    km = total_km(u)
    t = total_time(u)
    c = rides_count(u)

    await send(update, context,
        f"📟 {km:.1f} км\n⏱ {t} мин\nзаездов: {c}"
    )


async def service(update, context):
    u = update.effective_user.id
    s = get_settings(u)

    km = total_km(u)
    since = km - s[1]
    left = s[0] - since

    if left <= 0:
        txt = f"⚠️ пора смазать ({-left:.1f} км просрочка)"
    else:
        txt = f"ещё {left:.1f} км"

    await send(update, context,
        f"{txt}\nобщий: {km:.1f}"
    )


async def backup(update, context):
    u = update.effective_user.id
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM rides WHERE user_id=?", (u,))
    rides = [dict(r) for r in cur.fetchall()]
    conn.close()

    data = {
        "km": total_km(u),
        "time": total_time(u),
        "rides": rides
    }

    filename = "backup.json"

    with open(filename, "w") as f:
        json.dump(data, f)

    await clear_menu(context, update.effective_chat.id, u)

    with open(filename, "rb") as f:
        msg = await update.message.reply_document(f, reply_markup=keyboard())

    context.bot_data[key(update.effective_chat.id, u)] = msg.message_id


# ---------- QUICK INPUT ----------

async def quick(update, context):
    text = update.message.text.strip()

    if text.startswith("/"):
        return

    parts = text.split()

    try:
        km = float(parts[0])
        minutes = int(parts[1])
    except:
        return

    note = " ".join(parts[2:]) if len(parts) > 2 else ""

    add_ride(update.effective_user.id,
             datetime.now().strftime("%Y-%m-%d"),
             km, minutes, note)

    await send(update, context, f"добавил {km} км")


# ---------- MAIN ----------

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("нет токена")

    init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("service", service))
    app.add_handler(CommandHandler("backup", backup))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quick))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()