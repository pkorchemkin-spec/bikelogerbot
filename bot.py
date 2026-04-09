import os
import json
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)


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


def yesterday_str() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def parse_float(value: str) -> float:
    return float(value.replace(",", "."))


def parse_int(value: str) -> int:
    return int(value)


def parse_duration(value: str) -> int:
    value = value.strip()

    if value.isdigit():
        return int(value)

    for sep in (":", ",", "."):
        if sep in value:
            parts = value.split(sep)
            if len(parts) != 2:
                break

            hours_str, mins_str = parts[0].strip(), parts[1].strip()

            if not hours_str.isdigit() or not mins_str.isdigit():
                break

            hours = int(hours_str)
            mins = int(mins_str)

            if mins >= 60:
                raise ValueError("Минуты должны быть меньше 60")

            return hours * 60 + mins

    raise ValueError("Некорректный формат времени")


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
        return "Хорошая дистанция."
    if km >= 10:
        return "Нормальный выезд."
    return "Коротко, но по делу."


# ---------- DB ----------

def db():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Укажи переменную окружения DATABASE_URL")

    return psycopg.connect(database_url, row_factory=dict_row)


def init():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rides (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        date TEXT NOT NULL,
        km DOUBLE PRECISION NOT NULL,
        min INTEGER NOT NULL,
        note TEXT DEFAULT ''
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS maintenance (
        user_id BIGINT PRIMARY KEY,
        last_lube DOUBLE PRECISION NOT NULL DEFAULT 0,
        last_chain DOUBLE PRECISION NOT NULL DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()


def ensure_user(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO maintenance (user_id, last_lube, last_chain)
        VALUES (%s, 0, 0)
        ON CONFLICT (user_id) DO NOTHING
        """,
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
        "INSERT INTO rides (user_id, date, km, min, note) VALUES (%s, %s, %s, %s, %s)",
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
        SET date = %s, km = %s, min = %s, note = %s
        WHERE user_id = %s AND id = %s
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
        "DELETE FROM rides WHERE user_id = %s AND id = %s",
        (user_id, ride_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_ride(user_id: int, ride_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM rides WHERE user_id = %s AND id = %s",
        (user_id, ride_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


def total_km(user_id: int) -> float:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(km), 0) AS total FROM rides WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return float(row["total"])


def total_time(user_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(min), 0) AS total FROM rides WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["total"])


def rides_count(user_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS total FROM rides WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["total"])


def all_rides(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM rides WHERE user_id = %s ORDER BY date DESC, id DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def rides_page(user_id: int, offset: int, limit: int = RIDES_PAGE_SIZE):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM rides
        WHERE user_id = %s
        ORDER BY date DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        (user_id, limit, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def reset_user_data(user_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rides WHERE user_id = %s", (user_id,))
    cur.execute(
        "UPDATE maintenance SET last_lube = 0, last_chain = 0 WHERE user_id = %s",
        (user_id,),
    )
    conn.commit()
    conn.close()


def get_maintenance(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM maintenance WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


# ---------- STATE ----------

def clear_edit_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_edit_ride_id", None)
    context.user_data.pop("pending_edit_offset", None)
    context.user_data.pop("pending_edit_field", None)


def clear_add_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_add_step", None)
    context.user_data.pop("pending_add_data", None)


def cancel_input_states(context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_edit_state(context)
    clear_add_state(context)


# ---------- TEXT ----------

def first_start_text(user_name: str | None = None) -> str:
    name_part = f", {user_name}" if user_name else ""
    return (
        f"🚴 Привет{name_part}.\n\n"
        "Это твой веложурнал — тихий блокнот для дорог, асфальта и внезапных покатушек после мыслей "
        "\"да я всего на полчасика\".\n\n"
        "Здесь можно вручную сохранять поездки, даже если не было GPS, велокомпа или Strava снова решила жить своей жизнью.\n\n"
        "Что умеет бот:\n"
        "• добавлять заезды вручную\n"
        "• считать общий пробег и время\n"
        "• показывать сводку по поездкам\n"
        "• хранить историю заездов\n"
        "• напоминать про обслуживание цепи\n"
        "• делать бэкап данных\n\n"
        "Как добавить поездку быстро:\n"
        "25 90\n\n"
        "Где:\n"
        "25 — километры\n"
        "90 — минуты\n\n"
        "Можно и с датой:\n"
        "2026-04-08 25 90 вечерний заезд\n\n"
        "Ниже кнопки. Всё просто, без магии. Хотя немного магии всё-таки есть."
    )


def regular_start_text() -> str:
    return (
        "🚴 Бот на месте.\n"
        "Можно добавить поездку сообщением: 25 90\n"
        "Или открыть нужный раздел кнопками ниже."
    )


def help_text() -> str:
    return (
        "➕ Как добавить поездку\n\n"
        "Самый быстрый вариант:\n"
        "25 90\n\n"
        "Где 25 — километры, 90 — минуты.\n\n"
        "Можно добавить дату и заметку:\n"
        "2026-04-08 25 90 вечерний\n\n"
        "Форматы такие:\n"
        "• км минуты\n"
        "• YYYY-MM-DD км минуты заметка"
    )


def add_intro_text() -> str:
    return (
        "➕ Добавление заезда\n\n"
        "Давай спокойно запишем поездку по шагам.\n\n"
        "Какого числа был заезд?\n"
        "Напиши дату в формате:\n"
        "YYYY-MM-DD\n"
        f"Например, {today_str()}"
    )


def add_ask_km_text() -> str:
    return (
        "Отлично, дату запомнил.\n\n"
        "Сколько километров проехал?\n"
        "Можно просто числом:\n"
        "25 или 25.5"
    )


def add_ask_time_text(km: float) -> str:
    return (
        f"Принял, {km:.1f} км.\n\n"
        "Сколько заняла поездка?\n"
        "Можно так:\n"
        "90 или 1:30"
    )


def add_ask_note_text(minutes: int) -> str:
    return (
        f"Окей, время: {format_time(minutes)}.\n\n"
        "Хочешь добавить короткую заметку?\n"
        "Например: вечерний заезд\n\n"
        "Если не нужно — отправь «-» или нажми кнопку ниже."
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
    ride_number = rides_count(user_id)

    lines = [
        f"Это твой {ordinal_ride(ride_number)}.",
        f"Дистанция: {km:.1f} км.",
        praise_text(km),
        f"Время: {format_time(minutes)}.",
        f"Средняя скорость: {avg_speed(km, minutes):.1f} км/ч.",
        f"Общий пробег: {total_km(user_id):.1f} км.",
    ]

    warning = maintenance_warning_text(user_id)
    if warning:
        lines.append(warning)

    return "\n".join(lines)


def add_done_text(user_id: int, ride_date: str, km: float, minutes: int, note: str) -> str:
    ride_number = rides_count(user_id)

    lines = [
        "Готово.",
        "",
        f"Это твой {ordinal_ride(ride_number)}.",
        f"{ride_date}",
        "",
        f"{km:.1f} км за {format_time(minutes)}",
        f"Средняя: {avg_speed(km, minutes):.1f} км/ч",
    ]

    if note:
        lines.append(f"Заметка: {note}")

    lines.append(f"Общий пробег: {total_km(user_id):.1f} км")

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
    total = total_km(user_id)

    return (
        f"📊 Краткая сводка\n"
        f"Количество заездов: {rides_count(user_id)}\n"
        f"Общий километраж: {total:.1f} км\n"
        f"Общее время в пути: {format_time(total_time(user_id))}\n"
        f"Средняя скорость: {avg:.1f} км/ч"
    )


def summary_text_inline(user_id: int) -> str:
    text = summary_text(user_id)
    lines = text.split("\n")

    if lines and lines[0].startswith("📊"):
        lines = lines[1:]

    return "\n".join(lines).strip()


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


def ride_global_number(total: int, offset: int, index_on_page: int) -> int:
    return total - offset - index_on_page


def rides_text(user_id: int, offset: int) -> str:
    rows = rides_page(user_id, offset)
    total = rides_count(user_id)

    summary = summary_text_inline(user_id)

    if not rows:
        return summary + "\n\n📚 Статистика\nПока нет заездов."

    start_num = ride_global_number(total, offset, 0)
    end_num = ride_global_number(total, offset, len(rows) - 1)

    lines = [
        summary,
        "",
        f"📚 Статистика\nПоказаны заезды {start_num}-{end_num} из {total}"
    ]

    for idx, r in enumerate(rows):
        num = ride_global_number(total, offset, idx)
        lines.append(
            f"\n{num}. {r['date']} | {float(r['km']):.1f} км | {format_time(int(r['min']))} | "
            f"{avg_speed(float(r['km']), int(r['min'])):.1f} км/ч"
        )

    return "\n".join(lines)


def edit_intro_text(user_id: int, offset: int) -> str:
    rows = rides_page(user_id, offset)
    total = rides_count(user_id)
    if not rows:
        return "✏️ Исправить\nПока нет заездов для редактирования."

    lines = [
        "✏️ Исправить",
        "Сначала выбери номер заезда, который хочешь изменить или удалить."
    ]
    for idx, r in enumerate(rows):
        num = ride_global_number(total, offset, idx)
        lines.append(f"{num}. {r['date']} | {float(r['km']):.1f} км")
    return "\n".join(lines)


def edit_action_text(ride, number: int) -> str:
    note = f"\nКраткое описание: {ride['note']}" if ride["note"] else ""
    return (
        f"Заезд №{number}\n"
        f"Дата: {ride['date']}\n"
        f"Дистанция: {float(ride['km']):.1f} км\n"
        f"Время: {format_time(int(ride['min']))}\n"
        f"Средняя скорость: {avg_speed(float(ride['km']), int(ride['min'])):.1f} км/ч"
        f"{note}\n\n"
        "Что именно вы хотите изменить?"
    )


def edit_date_prompt_text(ride) -> str:
    return (
        "📅 Изменение даты заезда\n\n"
        f"Сейчас: {ride['date']}\n\n"
        "Какую дату поставить?\n"
        "Напиши в формате:\n"
        "YYYY-MM-DD"
    )


def edit_km_prompt_text(ride) -> str:
    return (
        "📏 Изменение дистанции\n\n"
        f"Сейчас: {float(ride['km']):.1f} км\n\n"
        "Сколько километров проехал?\n"
        "Можно просто числом:\n"
        "25 или 25.5"
    )


def edit_time_prompt_text(ride) -> str:
    return (
        "⏱ Изменение времени\n\n"
        f"Сейчас: {format_time(int(ride['min']))}\n\n"
        "Сколько заняла поездка?\n"
        "Можно так:\n"
        "90 или 1:30"
    )


def edit_note_prompt_text(ride) -> str:
    current_note = ride["note"] if ride["note"] else "без описания"
    return (
        "📝 Изменение описания\n\n"
        f"Сейчас: {current_note}\n\n"
        "Напиши новое краткое описание.\n"
        "Или нажми кнопку ниже, чтобы удалить его."
    )


def service_intro_text() -> str:
    return (
        "⚙️ Сброс / Бэкап\n"
        "Здесь можно сохранить свои данные или полностью очистить статистику.\n"
        "Сброс удалит все заезды без возможности восстановления."
    )


def reset_warning_text() -> str:
    return (
        "🧨 Ты правда хочешь всё удалить?\n"
        "Все заезды, километры и история исчезнут без возможности восстановления."
    )


# ---------- UI ----------

def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить заезд", callback_data="add_start")],
        [InlineKeyboardButton("📊 Краткая сводка", callback_data="summary")],
        [InlineKeyboardButton("📚 Статистика", callback_data="rides:0")],
        [InlineKeyboardButton("⚙️ Трансмиссия", callback_data="trans")],
    ])


def summary_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Полная статистика", callback_data="rides:0")],
        [InlineKeyboardButton("⚙️ Состояние трансмиссии", callback_data="trans")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="menu")],
    ])


def rides_kb(offset: int, total: int) -> InlineKeyboardMarkup:
    rows = []

    prev_callback = f"rides:{max(offset - RIDES_PAGE_SIZE, 0)}" if offset > 0 else "noop"
    next_callback = f"rides:{offset + RIDES_PAGE_SIZE}" if offset + RIDES_PAGE_SIZE < total else "noop"

    rows.append([
        InlineKeyboardButton("⬅️", callback_data=prev_callback),
        InlineKeyboardButton("➡️", callback_data=next_callback),
    ])

    rows.append([InlineKeyboardButton("✏️ Изменить данные заездов", callback_data=f"edit_menu:{offset}")])
    rows.append([InlineKeyboardButton("⚙️ Сброс / Бэкап", callback_data=f"service_menu:{offset}")])
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="menu")])

    return InlineKeyboardMarkup(rows)


def edit_select_kb(user_id: int, offset: int) -> InlineKeyboardMarkup:
    rows = rides_page(user_id, offset)
    total = rides_count(user_id)
    buttons = []

    number_row = []
    for idx, ride in enumerate(rows):
        number = ride_global_number(total, offset, idx)
        number_row.append(
            InlineKeyboardButton(
                str(number),
                callback_data=f"edit_pick:{ride['id']}:{offset}:{number}"
            )
        )
    if number_row:
        buttons.append(number_row)

    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"rides:{offset}")])
    return InlineKeyboardMarkup(buttons)


def edit_action_kb(ride_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Дата", callback_data=f"edit_field:{ride_id}:{offset}:date"),
            InlineKeyboardButton("📏 Дистанция", callback_data=f"edit_field:{ride_id}:{offset}:km"),
        ],
        [
            InlineKeyboardButton("⏱ Время", callback_data=f"edit_field:{ride_id}:{offset}:time"),
            InlineKeyboardButton("📝 Описание", callback_data=f"edit_field:{ride_id}:{offset}:note"),
        ],
        [InlineKeyboardButton("🗑 Удалить заезд", callback_data=f"delete_confirm:{ride_id}:{offset}")],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data=f"edit_menu:{offset}"),
            InlineKeyboardButton("🏠 В меню", callback_data="menu"),
        ],
    ])


def edit_field_back_kb(ride_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"edit_field_back:{ride_id}:{offset}")]
    ])


def edit_date_kb(ride_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Сегодня", callback_data=f"edit_date_today:{ride_id}:{offset}"),
            InlineKeyboardButton("Вчера", callback_data=f"edit_date_yesterday:{ride_id}:{offset}"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"edit_field_back:{ride_id}:{offset}")],
    ])


def edit_note_kb(ride_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Удалить описание", callback_data=f"edit_note_clear:{ride_id}:{offset}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"edit_field_back:{ride_id}:{offset}")],
    ])


def service_kb(offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💾 Бэкап", callback_data="backup")],
        [InlineKeyboardButton("🧨 Сброс", callback_data=f"reset:{offset}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"rides:{offset}")],
    ])


def delete_confirm_kb(ride_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Да, удалить", callback_data=f"delete_yes:{ride_id}:{offset}")],
        [InlineKeyboardButton("⚪ Отмена", callback_data=f"edit_menu:{offset}")],
    ])


def reset_kb(offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Да, удалить всё", callback_data=f"reset_yes:{offset}")],
        [InlineKeyboardButton("⚪ Отмена", callback_data=f"service_menu:{offset}")],
    ])


def add_kb_first() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Сегодня", callback_data="add_date_today"),
            InlineKeyboardButton("Вчера", callback_data="add_date_yesterday"),
        ],
        [InlineKeyboardButton("⬅️ В меню", callback_data="add_cancel")],
    ])


def add_kb_next() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="add_back")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="add_cancel")],
    ])


def add_kb_note() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить", callback_data="add_skip_note")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="add_back")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="add_cancel")],
    ])


# ---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    created = ensure_user(user.id)
    cancel_input_states(context)

    await update.message.reply_text(
        first_start_text(user.first_name) if created else regular_start_text(),
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

    pending_add_step = context.user_data.get("pending_add_step")
    pending_add_data = context.user_data.get("pending_add_data", {})

    parts = text.split()

    # ---------- EDIT MODE ----------
    if pending_edit_id:
        pending_edit_field = context.user_data.get("pending_edit_field")
        ride = get_ride(user_id, pending_edit_id)

        if not ride or not pending_edit_field:
            clear_edit_state(context)
            await update.message.reply_text(
                "Не нашёл этот заезд.",
                reply_markup=rides_kb(pending_edit_offset, rides_count(user_id)),
            )
            return

        if pending_edit_field == "date":
            if not looks_like_date(text):
                await update.message.reply_text(
                    "Нужна дата в формате:\nYYYY-MM-DD",
                    reply_markup=edit_date_kb(pending_edit_id, pending_edit_offset),
                )
                return

            changed = save_edited_ride_field(user_id, pending_edit_id, "date", text)

        elif pending_edit_field == "km":
            try:
                km = parse_float(text)
            except Exception:
                await update.message.reply_text(
                    "Не понял дистанцию.\nПопробуй ещё раз:\n25 или 25.5",
                    reply_markup=edit_field_back_kb(pending_edit_id, pending_edit_offset),
                )
                return

            changed = save_edited_ride_field(user_id, pending_edit_id, "km", km)

        elif pending_edit_field == "time":
            try:
                minutes = parse_duration(text)
            except Exception:
                await update.message.reply_text(
                    "Не понял время.\nПопробуй:\n90 или 1:30",
                    reply_markup=edit_field_back_kb(pending_edit_id, pending_edit_offset),
                )
                return

            changed = save_edited_ride_field(user_id, pending_edit_id, "time", minutes)

        elif pending_edit_field == "note":
            changed = save_edited_ride_field(user_id, pending_edit_id, "note", text)

        else:
            changed = False

        if not changed:
            clear_edit_state(context)
            await update.message.reply_text(
                "Не смог обновить этот заезд.",
                reply_markup=rides_kb(pending_edit_offset, rides_count(user_id)),
            )
            return

        updated_ride = get_ride(user_id, pending_edit_id)
        ride_number = get_ride_number_by_id(user_id, pending_edit_id) or 0
        clear_edit_state(context)

        await update.message.reply_text(
            "Готово, обновил.",
            reply_markup=edit_action_kb(pending_edit_id, pending_edit_offset),
        )
        await update.message.reply_text(
            edit_action_text(updated_ride, ride_number),
            reply_markup=edit_action_kb(pending_edit_id, pending_edit_offset),
        )
        return

    # ---------- THOUGHTFUL ADD MODE ----------
    if pending_add_step == "date":
        if not looks_like_date(text):
            await update.message.reply_text(
                "Нужна дата в формате:\nYYYY-MM-DD",
                reply_markup=add_kb_first(),
            )
            return

        pending_add_data["date"] = text
        context.user_data["pending_add_data"] = pending_add_data
        context.user_data["pending_add_step"] = "km"

        await update.message.reply_text(
            add_ask_km_text(),
            reply_markup=add_kb_next(),
        )
        return

    if pending_add_step == "km":
        try:
            km = parse_float(text)
        except Exception:
            await update.message.reply_text(
                "Не понял дистанцию.\nПопробуй ещё раз:\n25 или 25.5",
                reply_markup=add_kb_next(),
            )
            return

        pending_add_data["km"] = km
        context.user_data["pending_add_data"] = pending_add_data
        context.user_data["pending_add_step"] = "time"

        await update.message.reply_text(
            add_km_reaction(km) + "\n\n" + add_ask_time_text(km),
            reply_markup=add_kb_next(),
        )
        return

    if pending_add_step == "time":
        try:
            minutes = parse_duration(text)
        except Exception:
            await update.message.reply_text(
                "Не понял время.\nПопробуй:\n90 или 1:30",
                reply_markup=add_kb_next(),
            )
            return

        pending_add_data["minutes"] = minutes
        context.user_data["pending_add_data"] = pending_add_data
        context.user_data["pending_add_step"] = "note"

        await update.message.reply_text(
            add_ask_note_text(minutes),
            reply_markup=add_kb_note(),
        )
        return

    if pending_add_step == "note":
        note = "" if text == "-" else text

        ride_date = pending_add_data["date"]
        km = pending_add_data["km"]
        minutes = pending_add_data["minutes"]

        add_ride(user_id, ride_date, km, minutes, note)
        clear_add_state(context)

        await update.message.reply_text(
            add_done_text(user_id, ride_date, km, minutes, note),
            reply_markup=main_kb(),
        )
        return

    # ---------- QUICK ADD MODE ----------
    try:
        if len(parts) >= 2 and not looks_like_date(parts[0]):
            parse_float(parts[0])
            parse_duration(parts[1])
        elif len(parts) >= 3 and looks_like_date(parts[0]):
            parse_float(parts[1])
            parse_duration(parts[2])
        else:
            return
    except Exception:
        return

    try:
        if len(parts) >= 2 and not looks_like_date(parts[0]):
            ride_date = today_str()
            km = parse_float(parts[0])
            minutes = parse_duration(parts[1])
            note = " ".join(parts[2:]) if len(parts) > 2 else ""
        elif len(parts) >= 3 and looks_like_date(parts[0]):
            ride_date = parts[0]
            km = parse_float(parts[1])
            minutes = parse_duration(parts[2])
            note = " ".join(parts[3:]) if len(parts) > 3 else ""
        else:
            return
    except Exception:
        return

    clear_add_state(context)
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

    if query.data == "noop":
        return

    if query.data == "add_start":
        clear_edit_state(context)
        clear_add_state(context)
        context.user_data["pending_add_step"] = "date"
        context.user_data["pending_add_data"] = {}

        await query.message.reply_text(
            add_intro_text(),
            reply_markup=add_kb_first(),
        )
        return

    if query.data == "add_date_today":
        context.user_data["pending_add_data"]["date"] = today_str()
        context.user_data["pending_add_step"] = "km"

        await query.message.reply_text(
            add_ask_km_text(),
            reply_markup=add_kb_next(),
        )
        return

    if query.data == "add_date_yesterday":
        context.user_data["pending_add_data"]["date"] = yesterday_str()
        context.user_data["pending_add_step"] = "km"

        await query.message.reply_text(
            add_ask_km_text(),
            reply_markup=add_kb_next(),
        )
        return

    if query.data == "add_skip_note":
        pending_add_data = context.user_data.get("pending_add_data", {})

        ride_date = pending_add_data["date"]
        km = pending_add_data["km"]
        minutes = pending_add_data["minutes"]

        add_ride(user_id, ride_date, km, minutes, "")
        clear_add_state(context)

        await query.message.reply_text(
            add_done_text(user_id, ride_date, km, minutes, ""),
            reply_markup=main_kb(),
        )
        return

    if query.data == "add_cancel":
        cancel_input_states(context)
        await query.message.reply_text(
            regular_start_text(),
            reply_markup=main_kb(),
        )
        return

    if query.data == "add_back":
        step = context.user_data.get("pending_add_step")

        if step == "km":
            context.user_data["pending_add_step"] = "date"
            await query.message.reply_text(
                add_intro_text(),
                reply_markup=add_kb_first(),
            )
            return

        if step == "time":
            context.user_data["pending_add_step"] = "km"
            await query.message.reply_text(
                add_ask_km_text(),
                reply_markup=add_kb_next(),
            )
            return

        if step == "note":
            context.user_data["pending_add_step"] = "time"
            km = context.user_data.get("pending_add_data", {}).get("km")
            if km is None:
                context.user_data["pending_add_step"] = "date"
                await query.message.reply_text(
                    add_intro_text(),
                    reply_markup=add_kb_first(),
                )
                return

            await query.message.reply_text(
                add_km_reaction(km) + "\n\n" + add_ask_time_text(km),
                reply_markup=add_kb_next(),
            )
            return

        return

    if query.data == "menu":
        cancel_input_states(context)
        await query.message.reply_text(
            regular_start_text(),
            reply_markup=main_kb(),
        )
        return

    if query.data == "help":
        cancel_input_states(context)
        await query.message.reply_text(
            help_text(),
            reply_markup=main_kb(),
        )
        return

    if query.data == "summary":
        cancel_input_states(context)
        await query.message.reply_text(
            summary_text(user_id),
            reply_markup=summary_kb(),
        )
        return

    if query.data == "trans":
        cancel_input_states(context)
        await query.message.reply_text(
            transmission_text(user_id),
            reply_markup=main_kb(),
        )
        return

    if query.data.startswith("rides:"):
        cancel_input_states(context)
        offset = int(query.data.split(":")[1])
        await query.message.reply_text(
            rides_text(user_id, offset),
            reply_markup=rides_kb(offset, rides_count(user_id)),
        )
        return

    if query.data.startswith("edit_menu:"):
        cancel_input_states(context)
        offset = int(query.data.split(":")[1])
        await query.message.reply_text(
            edit_intro_text(user_id, offset),
            reply_markup=edit_select_kb(user_id, offset),
        )
        return

    if query.data.startswith("edit_pick:"):
        _, ride_id_str, offset_str, number_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)
        number = int(number_str)

        ride = get_ride(user_id, ride_id)
        if not ride:
            await query.message.reply_text(
                "Не нашёл этот заезд.",
                reply_markup=edit_select_kb(user_id, offset),
            )
            return

        await query.message.reply_text(
            edit_action_text(ride, number),
            reply_markup=edit_action_kb(ride_id, offset),
        )
        return

    if query.data.startswith("edit_field_back:"):
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        ride = get_ride(user_id, ride_id)
        if not ride:
            clear_edit_state(context)
            await query.message.reply_text(
                "Не нашёл этот заезд.",
                reply_markup=edit_select_kb(user_id, offset),
            )
            return

        ride_number = get_ride_number_by_id(user_id, ride_id) or 0
        clear_edit_state(context)

        await query.message.reply_text(
            edit_action_text(ride, ride_number),
            reply_markup=edit_action_kb(ride_id, offset),
        )
        return

    if query.data.startswith("edit_field:"):
        _, ride_id_str, offset_str, field = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        ride = get_ride(user_id, ride_id)
        if not ride:
            clear_edit_state(context)
            await query.message.reply_text(
                "Не нашёл этот заезд.",
                reply_markup=edit_select_kb(user_id, offset),
            )
            return

        context.user_data["pending_edit_ride_id"] = ride_id
        context.user_data["pending_edit_offset"] = offset
        context.user_data["pending_edit_field"] = field

        if field == "date":
            await query.message.reply_text(
                edit_date_prompt_text(ride),
                reply_markup=edit_date_kb(ride_id, offset),
            )
            return

        if field == "km":
            await query.message.reply_text(
                edit_km_prompt_text(ride),
                reply_markup=edit_field_back_kb(ride_id, offset),
            )
            return

        if field == "time":
            await query.message.reply_text(
                edit_time_prompt_text(ride),
                reply_markup=edit_field_back_kb(ride_id, offset),
            )
            return

        if field == "note":
            await query.message.reply_text(
                edit_note_prompt_text(ride),
                reply_markup=edit_note_kb(ride_id, offset),
            )
            return

        clear_edit_state(context)
        await query.message.reply_text(
            "Не понял, что именно менять.",
            reply_markup=edit_action_kb(ride_id, offset),
        )
        return

    if query.data.startswith("edit_date_today:"):
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        changed = save_edited_ride_field(user_id, ride_id, "date", today_str())
        updated_ride = get_ride(user_id, ride_id)
        ride_number = get_ride_number_by_id(user_id, ride_id) or 0
        clear_edit_state(context)

        if not changed or not updated_ride:
            await query.message.reply_text(
                "Не смог обновить дату.",
                reply_markup=rides_kb(offset, rides_count(user_id)),
            )
            return

        await query.message.reply_text(
            "Готово, обновил дату.",
            reply_markup=edit_action_kb(ride_id, offset),
        )
        await query.message.reply_text(
            edit_action_text(updated_ride, ride_number),
            reply_markup=edit_action_kb(ride_id, offset),
        )
        return

    if query.data.startswith("edit_date_yesterday:"):
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        changed = save_edited_ride_field(user_id, ride_id, "date", yesterday_str())
        updated_ride = get_ride(user_id, ride_id)
        ride_number = get_ride_number_by_id(user_id, ride_id) or 0
        clear_edit_state(context)

        if not changed or not updated_ride:
            await query.message.reply_text(
                "Не смог обновить дату.",
                reply_markup=rides_kb(offset, rides_count(user_id)),
            )
            return

        await query.message.reply_text(
            "Готово, обновил дату.",
            reply_markup=edit_action_kb(ride_id, offset),
        )
        await query.message.reply_text(
            edit_action_text(updated_ride, ride_number),
            reply_markup=edit_action_kb(ride_id, offset),
        )
        return

    if query.data.startswith("edit_note_clear:"):
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        changed = save_edited_ride_field(user_id, ride_id, "note", "")
        updated_ride = get_ride(user_id, ride_id)
        ride_number = get_ride_number_by_id(user_id, ride_id) or 0
        clear_edit_state(context)

        if not changed or not updated_ride:
            await query.message.reply_text(
                "Не смог удалить описание.",
                reply_markup=rides_kb(offset, rides_count(user_id)),
            )
            return

        await query.message.reply_text(
            "Описание удалил.",
            reply_markup=edit_action_kb(ride_id, offset),
        )
        await query.message.reply_text(
            edit_action_text(updated_ride, ride_number),
            reply_markup=edit_action_kb(ride_id, offset),
        )
        return

    if query.data.startswith("delete_confirm:"):
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        ride = get_ride(user_id, ride_id)
        if not ride:
            await query.message.reply_text(
                "Не нашёл этот заезд.",
                reply_markup=edit_select_kb(user_id, offset),
            )
            return

        await query.message.reply_text(
            "Удалить этот заезд?",
            reply_markup=delete_confirm_kb(ride_id, offset),
        )
        return

    if query.data.startswith("delete_yes:"):
        cancel_input_states(context)
        _, ride_id_str, offset_str = query.data.split(":")
        ride_id = int(ride_id_str)
        offset = int(offset_str)

        delete_ride(user_id, ride_id)

        total = rides_count(user_id)
        if offset >= total and offset > 0:
            offset = max(0, offset - RIDES_PAGE_SIZE)

        await query.message.reply_text(
            "Заезд удалил.",
            reply_markup=rides_kb(offset, rides_count(user_id)),
        )
        await query.message.reply_text(
            rides_text(user_id, offset),
            reply_markup=rides_kb(offset, rides_count(user_id)),
        )
        return

    if query.data.startswith("service_menu:"):
        cancel_input_states(context)
        offset = int(query.data.split(":")[1])
        await query.message.reply_text(
            service_intro_text(),
            reply_markup=service_kb(offset),
        )
        return

    if query.data == "backup":
        cancel_input_states(context)
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

    if query.data.startswith("reset:"):
        cancel_input_states(context)
        offset = int(query.data.split(":")[1])
        await query.message.reply_text(
            reset_warning_text(),
            reply_markup=reset_kb(offset),
        )
        return

    if query.data.startswith("reset_yes:"):
        cancel_input_states(context)
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