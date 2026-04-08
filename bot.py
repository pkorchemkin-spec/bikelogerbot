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


def total_duration(user_id: int) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(duration_min), 0) AS total FROM rides WHERE user_id = ?", (user_id,))
    total = int(cur.fetchone()["total"])
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


def service_status_text(user_id: int) -> str:
    settings = get_service_settings(user_id)
    odo = total_distance(user_id)
    since_service = odo - float(settings["last_chain_service_odometer"])
    interval = float(settings["chain_interval_km"])
    left = interval - since_service

    if left <= 0:
        status = f"⚠️ Пора смазывать цепь. Перекатал на {-left:.1f} км сверх интервала."
    elif left <= 20:
        status = f"🟠 До смазки цепи осталось совсем немного — {left:.1f} км."
    else:
        status = f"🛠 До смазки цепи осталось примерно {left:.1f} км."

    return (
        f"Общий пробег: {odo:.1f} км\n"
        f"После последней смазки: {since_service:.1f} км\n"
        f"Интервал: {interval:.1f} км\n\n{status}"
    )


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить", callback_data="menu_add")],
            [InlineKeyboardButton("📟 Статистика", callback_data="menu_stats"), InlineKeyboardButton("📝 Последние", callback_data="menu_list")],
            [InlineKeyboardButton("🛠 Цепь", callback_data="menu_service"), InlineKeyboardButton("💾 Бэкап", callback_data="menu_backup")],
            [InlineKeyboardButton("🗑 Удалить последнюю", callback_data="menu_delete_last")],
        ]
    )


def _menu_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}:{user_id}"


async def clear_previous_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    last_message_id = context.bot_data.get(_menu_key(chat_id, user_id))
    if not last_message_id:
        return
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=last_message_id,
            reply_markup=None,
        )
    except BadRequest:
        pass


async def send_with_fresh_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await clear_previous_menu(context, chat_id, user_id)

    if update.callback_query:
        sent = await update.callback_query.message.reply_text(text,  **kwargs)
    else:
        sent = await update.message.reply_text(text,  **kwargs)

    context.bot_data[_menu_key(chat_id, user_id)] = sent.message_id
    return sent


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ensure_user_settings(user_id)
    text = (
        "🚴 Веложурнал готов.\n\n"
        "Быстрое добавление:\n"
        "• 25 90\n"
        "• 25 90 вечерняя\n"
        "• 2026-04-08 25 90 дождь\n\n"
        "Команды тоже работают:\n"
        "/stats /list /service /chain_done /backup\n"
        "/set_chain_interval 150\n\n"
        "Жми кнопки ниже или просто присылай пробег и минуты сообщением."
    )
    await send_with_fresh_menu(update, context, text)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Нужно так: /add YYYY-MM-DD км минуты заметка\n"
            "Пример: /add 2026-04-08 24.6 82 вечерняя_поездка",
            
        )
        return

    try:
        ride_date = args[0]
        datetime.strptime(ride_date, "%Y-%m-%d")
        distance_km = float(args[1].replace(",", "."))
        duration_min = int(args[2])
        note = " ".join(args[3:]) if len(args) > 3 else ""
    except ValueError:
        await update.message.reply_text(
            "Проверь формат. Пример: /add 2026-04-08 24.6 82 вечерняя_поездка",
            
        )
        return

    add_ride(user_id, ride_date, distance_km, duration_min, note)
    odo = total_distance(user_id)
    chain_note = short_chain_warning(user_id)
    await update.message.reply_text(
        f"Записал.\nДата: {ride_date}\nПробег: {distance_km:.1f} км\nВремя: {duration_min} мин\n"
        f"Общий пробег: {odo:.1f} км\n\n{chain_note}",
        
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    total = total_distance(user_id)
    duration = total_duration(user_id)
    count = rides_count(user_id)
    average = total / count if count else 0
    text = (
        f"📟 Одометр: {total:.1f} км\n"
        f"⏱ Общее время: {duration} мин\n"
        f"Заездов: {count}\n"
        f"Средний заезд: {average:.1f} км"
    )
    if update.message:
        await send_with_fresh_menu(update, context, text)
    elif update.callback_query:
        await send_with_fresh_menu(update, context, text)


async def list_rides(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_last_rides(user_id)
    if not rows:
        text = "Пока пусто. Велосипед молчит, как лес перед дождём."
    else:
        lines = ["Последние поездки:"]
        for row in rows:
            duration = f" · {row['duration_min']} мин" if row['duration_min'] is not None else ""
            note = f" · {row['note']}" if row['note'] else ""
            lines.append(f"• {row['ride_date']} — {row['distance_km']:.1f} км{duration}{note}")
        text = "\n".join(lines)

    if update.message:
        await send_with_fresh_menu(update, context, text)
    elif update.callback_query:
        await send_with_fresh_menu(update, context, text)


async def service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = service_status_text(update.effective_user.id)
    if update.message:
        await send_with_fresh_menu(update, context, text)
    elif update.callback_query:
        await send_with_fresh_menu(update, context, text)


async def chain_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    odo = mark_chain_serviced(user_id)
    await update.message.reply_text(
        f"Готово. Смазку цепи отметил на пробеге {odo:.1f} км.",
        
    )


async def set_chain_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Пример: /set_chain_interval 150", reply_markup=main_keyboard())
        return
    try:
        interval = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "Интервал должен быть числом. Пример: /set_chain_interval 150",
            
        )
        return

    set_service_interval(user_id, interval)
    await update.message.reply_text(
        f"Новый интервал смазки цепи: {interval:.1f} км",
        
    )


async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ok = delete_last_ride(user_id)
    if not ok:
        text = "Удалять нечего."
    else:
        text = f"Последнюю поездку удалил. Новый общий пробег: {total_distance(user_id):.1f} км"

    if update.message:
        await send_with_fresh_menu(update, context, text)
    elif update.callback_query:
        await send_with_fresh_menu(update, context, text)


def short_chain_warning(user_id: int) -> str:
    settings = get_service_settings(user_id)
    odo = total_distance(user_id)
    since_service = odo - float(settings["last_chain_service_odometer"])
    interval = float(settings["chain_interval_km"])
    left = interval - since_service

    if left <= 0:
        return f"⚠️ Пора смазывать цепь. Уже перебор на {-left:.1f} км."
    if left <= 20:
        return f"🟠 До смазки цепи осталось {left:.1f} км."
    return f"🟢 До смазки цепи ещё {left:.1f} км."


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT ride_date, distance_km, duration_min, note, created_at FROM rides WHERE user_id = ? ORDER BY ride_date ASC, id ASC",
        (user_id,),
    )
    rides = [dict(row) for row in cur.fetchall()]
    settings = dict(get_service_settings(user_id))
    conn.close()

    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user_id": user_id,
        "stats": {
            "total_distance_km": total_distance(user_id),
            "total_duration_min": total_duration(user_id),
            "rides_count": rides_count(user_id),
        },
        "service_settings": settings,
        "rides": rides,
    }

    filename = f"bike_backup_{user_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    target = update.message if update.message else update.callback_query.message
    with open(filename, "rb") as f:
        await clear_previous_menu(context, update.effective_chat.id, update.effective_user.id)
    sent = await target.reply_document(document=f, filename=filename, caption="Вот твой бэкап. На всякий пожарный, пока цепь не скрипит.", reply_markup=main_keyboard())

        context.bot_data[_menu_key(update.effective_chat.id, update.effective_user.id)] = sent.message_id
    os.remove(filename)


async def handle_quick_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if text.startswith("/"):
        return

    parts = text.split()
    ride_date = datetime.now().strftime("%Y-%m-%d")

    try:
        if len(parts) >= 2 and is_number(parts[0]) and parts[1].isdigit():
            distance_km = float(parts[0].replace(",", "."))
            duration_min = int(parts[1])
            note = " ".join(parts[2:]) if len(parts) > 2 else ""
        elif len(parts) >= 3 and is_date(parts[0]) and is_number(parts[1]) and parts[2].isdigit():
            ride_date = parts[0]
            distance_km = float(parts[1].replace(",", "."))
            duration_min = int(parts[2])
            note = " ".join(parts[3:]) if len(parts) > 3 else ""
        else:
            return
    except ValueError:
        return

    add_ride(update.effective_user.id, ride_date, distance_km, duration_min, note)
    odo = total_distance(update.effective_user.id)
    await update.message.reply_text(
        f"Записал быстро.\nДата: {ride_date}\nПробег: {distance_km:.1f} км\nВремя: {duration_min} мин\n"
        f"Общий пробег: {odo:.1f} км\n\n{short_chain_warning(update.effective_user.id)}",
        
    )


def is_number(value: str) -> bool:
    try:
        float(value.replace(",", "."))
        return True
    except ValueError:
        return False


def is_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "menu_stats":
        await stats(update, context)
    elif data == "menu_list":
        await list_rides(update, context)
    elif data == "menu_service":
        await service(update, context)
    elif data == "menu_backup":
        await backup(update, context)
    elif data == "menu_delete_last":
        await delete_last(update, context)
    elif data == "menu_add":
        await query.message.reply_text(
            "Пришли сообщением так:\n25 90\nили\n2026-04-08 25 90 вечерняя",
            
        )


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
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quick_add))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
