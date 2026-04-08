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
    CREATE TABLE IF NOT EXISTS maintenance (
        user_id INTEGER PRIMARY KEY,
        last_lube REAL DEFAULT 0,
        last_chain REAL DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()


# ---------- HELPERS ----------

def avg_speed(km, minutes):
    if minutes == 0:
        return 0
    return km / (minutes / 60)


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


# ---------- UI ----------

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить", callback_data="help")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("📚 Заезды", callback_data="list")],
        [InlineKeyboardButton("⚙️ Трансмиссия", callback_data="service")],
        [InlineKeyboardButton("💾 Бэкап", callback_data="backup")]
    ])


def stats_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧨 Сбросить ВСЮ статистику", callback_data="reset")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="menu")]
    ])


def reset_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Да, удалить всё", callback_data="reset_yes")],
        [InlineKeyboardButton("⚪ Отмена", callback_data="menu")]
    ])


# ---------- COMMANDS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚴 Веложурнал\n\n"
        "Пиши: 25 90\n"
        "или: 2026-04-08 25 90",
        reply_markup=main_keyboard()
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id

    km = total_km(user)
    t = total_time(user)

    text = (
        f"📊 Статистика\n"
        f"Км: {km:.1f}\n"
        f"Время: {t} мин"
    )

    await update.message.reply_text(text, reply_markup=stats_keyboard())


async def quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.split()

    try:
        km = float(text[0])
        minutes = int(text[1])
    except:
        return

    user = update.effective_user.id

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rides (user_id, ride_date, distance_km, duration_min) VALUES (?, ?, ?, ?)",
        (user, datetime.now().strftime("%Y-%m-%d"), km, minutes)
    )
    conn.commit()
    conn.close()

    total = total_km(user)
    speed = avg_speed(km, minutes)

    msg = (
        f"Добавил {km} км\n"
        f"Средняя: {speed:.1f} км/ч\n"
        f"Ты уже проехал {total:.1f} км, ВАУ!"
    )

    await update.message.reply_text(msg, reply_markup=main_keyboard())


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user.id

    if query.data == "menu":
        await query.message.reply_text("Меню", reply_markup=main_keyboard())

    elif query.data == "stats":
        await query.message.reply_text(
            f"📊 {total_km(user):.1f} км",
            reply_markup=stats_keyboard()
        )

    elif query.data == "reset":
        await query.message.reply_text(
            "Ты точно уверен? Всё удалится.",
            reply_markup=reset_keyboard()
        )

    elif query.data == "reset_yes":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM rides WHERE user_id=?", (user,))
        conn.commit()
        conn.close()

        await query.message.reply_text(
            "Всё очищено.",
            reply_markup=main_keyboard()
        )


# ---------- MAIN ----------

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("нет токена")

    init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quick))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()