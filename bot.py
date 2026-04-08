import os
import sqlite3
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

DB_PATH = "bike_log.db"
DEFAULT_SERVICE_INTERVAL_KM = 150.0


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ride_date TEXT NOT NULL,
            distance_km REAL NOT NULL,
            duration_min INTEGER,
            note TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS service_settings (
            user_id INTEGER PRIMARY KEY,
            chain_interval_km REAL NOT NULL DEFAULT 150,
            last_chain_service_odometer REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def ensure_user_settings(user_id: int) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO service_settings (user_id, chain_interval_km, last_chain_service_odometer)
        VALUES (?, ?, ?)
        """,
        (user_id, DEFAULT_SERVICE_INTERVAL_KM, 0),
    )
    conn.commit()
    conn.close()


def add_ride(user_id: int, ride_date: str, distance_km: float, duration_min: Optional[int], note: str) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO rides (user_id, ride_date, distance_km, duration_min, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, ride_date, distance_km, duration_min, note, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def total_distance(user_id: int) -> float:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(distance_km), 0) AS total FROM rides WHERE user_id = ?", (user_id,))
    total = float(cur.fetchone()["total"])
    conn.close()
    return total


def rides_count(user_id: int) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM rides WHERE user_id = ?", (user_id,))
    count = int(cur.fetchone()["cnt"])
    conn.close()
    return count


def list_last_rides(user_id: int, limit: int = 10):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, ride_date, distance_km, duration_min, note
        FROM rides
        WHERE user_id = ?
        ORDER BY ride_date DESC, id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_last_ride(user_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM rides WHERE user_id = ? ORDER BY ride_date DESC, id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    cur.execute("DELETE FROM rides WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True


def get_service_settings(user_id: int) -> sqlite3.Row:
    ensure_user_settings(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT chain_interval_km, last_chain_service_odometer FROM service_settings WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def set_service_interval(user_id: int, interval_km: float) -> None:
    ensure_user_settings(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE service_settings SET chain_interval_km = ? WHERE user_id = ?",
        (interval_km, user_id),
    )
    conn.commit()
    conn.close()


def mark_chain_serviced(user_id: int) -> float:
    odo = total_distance(user_id)
    ensure_user_settings(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE service_settings SET last_chain_service_odometer = ? WHERE user_id = ?",
        (odo, user_id),
    )
    conn.commit()
    conn.close()
    return odo


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ensure_user_settings(user_id)
    text = (
        "🚴 Веложурнал запущен.\n\n"
        "Команды:\n"
        "/add YYYY-MM-DD км минуты заметка\n"
        "Пример: /add 2026-04-08 24.6 82 вечерняя_поездка\n\n"
        "/stats — общий пробег\n"
        "/list — последние поездки\n"
        "/service — статус по цепи\n"
        "/chain_done — отметил смазку цепи\n"
        "/set_chain_interval 150 — интервал обслуживания\n"
        "/delete_last — удалить последнюю поездку"
    )
    await update.message.reply_text(text)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Нужно так: /add YYYY-MM-DD км минуты заметка\n"
            "Пример: /add 2026-04-08 24.6 82 вечерняя_поездка"
        )
        return

    try:
        ride_date = args[0]
        datetime.strptime(ride_date, "%Y-%m-%d")
        distance_km = float(args[1].replace(",", "."))
        duration_min = int(args[2])
        note = " ".join(args[3:]) if len(args) > 3 else ""
    except ValueError:
        await update.message.reply_text("Проверь формат. Пример: /add 2026-04-08 24.6 82 вечерняя_поездка")
        return

    add_ride(user_id, ride_date, distance_km, duration_min, note)
    odo = total_distance(user_id)
    await update.message.reply_text(
        f"Записал.\nДата: {ride_date}\nПробег: {distance_km:.1f} км\nВремя: {duration_min} мин\n"
        f"Общий пробег: {odo:.1f} км"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    total = total_distance(user_id)
    count = rides_count(user_id)
    average = total / count if count else 0
    await update.message.reply_text(
        f"📟 Одометр: {total:.1f} км\nЗаездов: {count}\nСредний заезд: {average:.1f} км"
    )


async def list_rides(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_last_rides(user_id)
    if not rows:
        await update.message.reply_text("Пока пусто. Велосипед молчит, как лес перед дождём.")
        return

    lines = ["Последние поездки:"]
    for row in rows:
        duration = f" · {row['duration_min']} мин" if row['duration_min'] is not None else ""
        note = f" · {row['note']}" if row['note'] else ""
        lines.append(f"• {row['ride_date']} — {row['distance_km']:.1f} км{duration}{note}")
    await update.message.reply_text("\n".join(lines))


async def service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    settings = get_service_settings(user_id)
    odo = total_distance(user_id)
    since_service = odo - float(settings["last_chain_service_odometer"])
    interval = float(settings["chain_interval_km"])
    left = interval - since_service

    if left <= 0:
        status = f"⚠️ Пора смазывать цепь. Перекатал на {-left:.1f} км сверх интервала."
    else:
        status = f"🛠 До смазки цепи осталось примерно {left:.1f} км."

    await update.message.reply_text(
        f"Общий пробег: {odo:.1f} км\n"
        f"После последней смазки: {since_service:.1f} км\n"
        f"Интервал: {interval:.1f} км\n\n{status}"
    )


async def chain_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    odo = mark_chain_serviced(user_id)
    await update.message.reply_text(f"Готово. Смазку цепи отметил на пробеге {odo:.1f} км.")


async def set_chain_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Пример: /set_chain_interval 150")
        return
    try:
        interval = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Интервал должен быть числом. Пример: /set_chain_interval 150")
        return

    set_service_interval(user_id, interval)
    await update.message.reply_text(f"Новый интервал смазки цепи: {interval:.1f} км")


async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ok = delete_last_ride(user_id)
    if not ok:
        await update.message.reply_text("Удалять нечего.")
        return
    await update.message.reply_text(f"Последнюю поездку удалил. Новый общий пробег: {total_distance(user_id):.1f} км")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Укажи переменную окружения TELEGRAM_BOT_TOKEN")

    init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("list", list_rides))
    app.add_handler(CommandHandler("service", service))
    app.add_handler(CommandHandler("chain_done", chain_done))
    app.add_handler(CommandHandler("set_chain_interval", set_chain_interval))
    app.add_handler(CommandHandler("delete_last", delete_last))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
