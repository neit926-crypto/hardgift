import os

import telebot
import sqlite3
from datetime import datetime, date
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = 6730775469
WEB_APP_URL = "https://neit926-crypto.github.io/hardgift/"
DB_NAME = "hardgift.db"

bot = telebot.TeleBot(TOKEN)

def db():
    return sqlite3.connect(DB_NAME)

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                registered_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price TEXT NOT NULL,
                description TEXT
            )
        """)

def is_admin(user_id):
    return user_id == ADMIN_ID

def register_user(user):
    with db() as conn:
        conn.execute("""
            INSERT INTO users VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name
        """, (
            user.id, user.username, user.first_name,
            user.last_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

def admin_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🎁 Каталог", callback_data="gift_list"),
        InlineKeyboardButton("➕ Добавить", callback_data="gift_add")
    )
    kb.add(InlineKeyboardButton("🗑 Удалить подарок", callback_data="gift_delete"))
    return kb

@bot.message_handler(commands=["start"])
def start(message):
    register_user(message.from_user)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(
        "🎁 Открыть HARD GIFT",
        web_app=WebAppInfo(url=WEB_APP_URL)
    ))
    bot.send_message(
        message.chat.id,
        f"🎁 Добро пожаловать в HARD GIFT!\n\n"
        f"👤 {message.from_user.first_name}\n"
        f"🆔 {message.from_user.id}\n\n"
        "Твоя коллекция начинается здесь!",
        reply_markup=kb
    )

@bot.message_handler(commands=["me"])
def my_profile(message):
    user = message.from_user
    register_user(user)
    with db() as conn:
        result = conn.execute(
            "SELECT * FROM users WHERE telegram_id=?", (user.id,)
        ).fetchone()
    bot.send_message(
        message.chat.id,
        f"👤 ТВОЙ ПРОФИЛЬ HARD GIFT\n\n"
        f"Имя: {result[2]}\n"
        f"Фамилия: {result[3] or 'Не указана'}\n"
        f"Username: @{result[1] or 'отсутствует'}\n"
        f"Telegram ID: {result[0]}\n"
        f"Регистрация: {result[4]}"
    )

@bot.message_handler(commands=["admin"])
def admin(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Нет доступа.")
        return
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE date(registered_at)=?",
            (date.today().isoformat(),)
        ).fetchone()[0]
    bot.send_message(
        message.chat.id,
        f"🛠 АДМИН-ПАНЕЛЬ HARD GIFT\n\n"
        f"👥 Пользователей: {total}\n"
        f"🆕 Сегодня: {today}\n\nВыбери действие:",
        reply_markup=admin_keyboard()
    )

@bot.callback_query_handler(func=lambda c: c.data in ("gift_list", "gift_add", "gift_delete"))
def gift_actions(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
        return

    bot.answer_callback_query(call.id)

    if call.data == "gift_list":
        with db() as conn:
            gifts = conn.execute(
                "SELECT id, name, price, description FROM gifts ORDER BY id DESC"
            ).fetchall()
        if not gifts:
            bot.send_message(call.message.chat.id, "Каталог пока пуст.")
            return
        text = "🎁 КАТАЛОГ HARD GIFT\n\n"
        for gift in gifts:
            text += f"ID: {gift[0]}\n🎁 {gift[1]}\n💎 Цена: {gift[2]}\n{gift[3] or ''}\n\n"
        bot.send_message(call.message.chat.id, text)

    elif call.data == "gift_add":
        msg = bot.send_message(
            call.message.chat.id,
            "➕ Отправь данные подарка в формате:\n\n"
            "Название | Цена | Описание\n\n"
            "Пример:\n"
            "Золотой мишка | 100 Stars | Красивый подарок"
        )
        bot.register_next_step_handler(msg, save_gift)

    elif call.data == "gift_delete":
        with db() as conn:
            gifts = conn.execute("SELECT id, name FROM gifts ORDER BY id DESC").fetchall()
        if not gifts:
            bot.send_message(call.message.chat.id, "Удалять пока нечего — каталог пуст.")
            return
        kb = InlineKeyboardMarkup()
        for gift_id, name in gifts:
            kb.add(InlineKeyboardButton(
                f"🗑 {name} (ID {gift_id})",
                callback_data=f"gift_del:{gift_id}"
            ))
        bot.send_message(call.message.chat.id, "Выбери подарок для удаления:", reply_markup=kb)

def save_gift(message):
    if not is_admin(message.from_user.id):
        return
    parts = [x.strip() for x in message.text.split("|", 2)]
    if len(parts) != 3 or not parts[0] or not parts[1]:
        msg = bot.send_message(
            message.chat.id,
            "Неверный формат. Отправь так:\nНазвание | Цена | Описание"
        )
        bot.register_next_step_handler(msg, save_gift)
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO gifts (name, price, description) VALUES (?, ?, ?)",
            (parts[0], parts[1], parts[2])
        )
    bot.send_message(message.chat.id, f"✅ Подарок «{parts[0]}» добавлен!")

@bot.callback_query_handler(func=lambda c: c.data.startswith("gift_del:"))
def delete_gift(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
        return
    try:
        gift_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Ошибка")
        return
    with db() as conn:
        cur = conn.execute("DELETE FROM gifts WHERE id=?", (gift_id,))
    bot.answer_callback_query(call.id, "Подарок удалён" if cur.rowcount else "Подарок не найден")
    bot.send_message(call.message.chat.id, "🗑 Готово. Открой /admin для продолжения.")

@bot.message_handler(commands=["help"])
def help_command(message):
    bot.send_message(message.chat.id, "/start — открыть приложение\n/me — профиль\n/admin — админ-панель")

init_db()
print("HARD GIFT запущен!")
bot.infinity_polling()
