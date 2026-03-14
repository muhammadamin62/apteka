import os
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import google.generativeai as genai

# === КОНФИГУРАЦИЯ ===
TOKEN = os.getenv("8237149954:AAHTLCBGKzbnR8ATXlrYkK1SIMac6TyA-a8")
GEMINI_KEY = os.getenv("AIzaSyDTLdI8T5MvgR4EDhYm49OHyY3c3KO17UE")

genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")  # Настрой под свой пояс


# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect("med_bot.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS reminders 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                      name TEXT, time TEXT, stock INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS history 
                     (user_id INTEGER, name TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()


# === ЛОГИКА УВЕДОМЛЕНИЙ ===
async def send_reminder(chat_id: int, med_id: int, med_name: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принял", callback_data=f"med_done_{med_id}")
    builder.button(text="⏰ +15 мин", callback_data=f"med_snooze_{med_id}")

    await bot.send_message(
        chat_id,
        f"🚨 **ВРЕМЯ ПИТЬ ЛЕКАРСТВО!**\n💊 Препарат: {med_name}\n\nПожалуйста, подтвердите прием кнопкой ниже.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


# === СОСТОЯНИЯ (FSM) ===
class MedStates(StatesGroup):
    waiting_name = State()
    waiting_time = State()
    waiting_stock = State()


# === ОБРАБОТЧИКИ КОМАНД ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я твой ИИ-ассистент по приему лекарств.\n\n"
        "📸 **Пришли фото инструкции**, чтобы я распознал график\n"
        "➕ /add — добавить вручную\n"
        "📋 /list — твой список"
    )


@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    conn = sqlite3.connect("med_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, time, stock FROM reminders WHERE user_id = ?", (message.from_user.id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return await message.answer("Список пуст.")

    res = "📋 **Ваши лекарства:**\n"
    for r in rows:
        res += f"🔹 {r[0]} — {r[1]} (Остаток: {r[2]} шт.)\n"
    await message.answer(res, parse_mode="Markdown")


# === ИНТЕГРАЦИЯ С ИИ (ФОТО) ===
@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    await message.answer("🔄 ИИ изучает фото...")

    file_info = await bot.get_file(message.photo[-1].file_id)
    photo_bytes = await bot.download_file(file_info.file_path)

    prompt = "Это лекарство. Напиши только название и рекомендуемое время приема (например, 08:00). Если данных нет, предложи стандартное."

    try:
        response = ai_model.generate_content([prompt, {"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}])
        await message.answer(f"🤖 **ИИ советует:**\n{response.text}\n\nВведите название для записи:")
        await state.set_state(MedStates.waiting_name)
    except Exception as e:
        await message.answer("Ошибка ИИ. Введите название вручную через /add")


# === ДОБАВЛЕНИЕ ВРУЧНУЮ ===
@dp.message(Command("add"))
async def add_start(message: types.Message, state: FSMContext):
    await message.answer("Название лекарства:")
    await state.set_state(MedStates.waiting_name)


@dp.message(MedStates.waiting_name)
async def add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Время приема (ЧЧ:ММ):")
    await state.set_state(MedStates.waiting_time)


@dp.message(MedStates.waiting_time)
async def add_time(message: types.Message, state: FSMContext):
    await state.update_data(time=message.text)
    await message.answer("Сколько таблеток в упаковке? (число):")
    await state.set_state(MedStates.waiting_stock)


@dp.message(MedStates.waiting_stock)
async def add_stock(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        stock = int(message.text)
        conn = sqlite3.connect("med_bot.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO reminders (user_id, name, time, stock) VALUES (?, ?, ?, ?)",
                       (message.from_user.id, data['name'], data['time'], stock))
        med_id = cursor.lastrowid
        conn.commit()
        conn.close()

        h, m = map(int, data['time'].split(":"))
        scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[message.chat.id, med_id, data['name']])

        await message.answer(f"✅ Готово! Буду напоминать о {data['name']} в {data['time']}.")
        await state.clear()
    except:
        await message.answer("Ошибка. Введите число (количество таблеток).")


# === ОБРАБОТКА КНОПОК ===
@dp.callback_query(F.data.startswith("med_"))
async def handle_callback(callback: types.CallbackQuery):
    _, action, med_id = callback.data.split("_")

    conn = sqlite3.connect("med_bot.db")
    cursor = conn.cursor()

    if action == "done":
        cursor.execute("UPDATE reminders SET stock = stock - 1 WHERE id = ?", (med_id,))
        cursor.execute("SELECT name, stock FROM reminders WHERE id = ?", (med_id,))
        res = cursor.fetchone()

        cursor.execute("INSERT INTO history VALUES (?, ?, ?)",
                       (callback.from_user.id, res[0], datetime.now().strftime("%H:%M")))
        conn.commit()

        text = f"✅ Принято! Остаток: {res[1]} шт."
        if res[1] <= 3: text += "\n⚠️ Купите новую пачку!"
        await callback.message.edit_text(text)

    elif action == "snooze":
        scheduler.add_job(send_reminder, "date",
                          run_date=datetime.now() + timedelta(minutes=15),
                          args=[callback.message.chat.id, med_id, "Повтор"])
        await callback.message.edit_text("⏳ Напомню через 15 минут.")

    conn.close()


# === ЗАПУСК ===
async def restore_jobs():
    conn = sqlite3.connect("med_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, id, name, time FROM reminders")
    for u_id, m_id, name, t_str in cursor.fetchall():
        h, m = map(int, t_str.split(":"))
        scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[u_id, m_id, name])
    conn.close()


async def main():
    init_db()
    await restore_jobs()
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())