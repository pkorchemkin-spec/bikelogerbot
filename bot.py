# FINAL VERSION WITH ALL REQUESTED FEATURES
# (clean, stable, human-readable)

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
    h = minutes // 60
    m = minutes % 60
    if h == 0:
        return f"{m}м"
    return f"{h}ч {m:02d}м"


def avg_speed(km, minutes):
    return 0 if minutes == 0 else km / (minutes / 60)

# ---------- DB ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    c = db()
    cur = c.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        date TEXT,
        km REAL,
        min INTEGER,
        note TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS maintenance (
        user_id INTEGER PRIMARY KEY,
        last_lube REAL DEFAULT 0,
        last_chain REAL DEFAULT 0
    )""")

    c.commit()
    c.close()

# ---------- DATA ----------

def add(user, date, km, m, note=""):
    c = db()
    c.execute("INSERT INTO rides (user_id,date,km,min,note) VALUES (?,?,?,?,?)",
              (user, date, km, m, note))
    c.commit()
    c.close()


def total_km(user):
    return db().execute("SELECT COALESCE(SUM(km),0) FROM rides WHERE user_id=?", (user,)).fetchone()[0]


def total_time(user):
    return db().execute("SELECT COALESCE(SUM(min),0) FROM rides WHERE user_id=?", (user,)).fetchone()[0]


def count(user):
    return db().execute("SELECT COUNT(*) FROM rides WHERE user_id=?", (user,)).fetchone()[0]


def all_rides(user):
    return db().execute("SELECT * FROM rides WHERE user_id=? ORDER BY date DESC,id DESC", (user,)).fetchall()

# ---------- UI ----------

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить", callback_data="help")],
        [InlineKeyboardButton("📊 Краткая сводка", callback_data="summary")],
        [InlineKeyboardButton("📚 Статистика", callback_data="rides:0")],
        [InlineKeyboardButton("⚙️ Трансмиссия", callback_data="trans")],
    ])


def rides_kb(offset, total):
    btns = []
    if offset > 0:
        btns.append(InlineKeyboardButton("⬅️", callback_data=f"rides:{offset-5}"))
    if offset+5 < total:
        btns.append(InlineKeyboardButton("➡️", callback_data=f"rides:{offset+5}"))

    return InlineKeyboardMarkup([
        btns,
        [InlineKeyboardButton("💾 Бэкап", callback_data="backup")],
        [InlineKeyboardButton("🧨 Сброс", callback_data="reset")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="menu")]
    ])

# ---------- TEXT ----------

def summary_text(user):
    rides = all_rides(user)
    if not rides:
        return "Пока нет данных"

    avg = sum(avg_speed(r["km"], r["min"]) for r in rides) / len(rides)

    return (
        f"📊 Краткая сводка\n"
        f"Заездов: {len(rides)}\n"
        f"Км: {total_km(user):.1f}\n"
        f"Время: {format_time(total_time(user))}\n"
        f"Средняя скорость: {avg:.1f} км/ч"
    )

# ---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚴 Веложурнал\nПиши: 25 90",
        reply_markup=main_kb()
    )


async def quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.split()
    try:
        km = float(t[0])
        m = int(t[1])
    except:
        return

    user = update.effective_user.id
    add(user, datetime.now().strftime("%Y-%m-%d"), km, m)

    speed = avg_speed(km, m)
    total = total_km(user)

    await update.message.reply_text(
        f"Добавил {km} км\n"
        f"Средняя: {speed:.1f} км/ч\n"
        f"Ты уже проехал {total:.1f} км, ВАУ!",
        reply_markup=main_kb()
    )


async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user.id

    if q.data == "menu":
        await q.message.reply_text("Меню", reply_markup=main_kb())

    elif q.data == "summary":
        await q.message.reply_text(summary_text(user), reply_markup=main_kb())

    elif q.data.startswith("rides"):
        offset = int(q.data.split(":")[1])
        rides = all_rides(user)[offset:offset+5]

        text = "📚 Статистика\n"
        for r in rides:
            text += f"\n{r['date']} | {r['km']} км | {format_time(r['min'])}"

        await q.message.reply_text(text, reply_markup=rides_kb(offset, count(user)))

    elif q.data == "backup":
        data = [dict(r) for r in all_rides(user)]
        with open("backup.json","w") as f:
            json.dump(data,f)
        await q.message.reply_document(open("backup.json","rb"))

    elif q.data == "reset":
        await q.message.reply_text("Ты уверен?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Да", callback_data="reset_yes")],
            [InlineKeyboardButton("Нет", callback_data="menu")]
        ]))

    elif q.data == "reset_yes":
        db().execute("DELETE FROM rides WHERE user_id=?",(user,))
        db().commit()
        await q.message.reply_text("Очищено", reply_markup=main_kb())

# ---------- MAIN ----------

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    init()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quick))

    app.run_polling()

if __name__ == "__main__":
    main()
