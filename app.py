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

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ ===
TOKEN = "8237149954:AAHTLCBGKzbnR8ATXlrYkK1SIMac6TyA-a8"
GEMINI_KEY = "AIzaSyDTLdI8T5MvgR4EDhYm49OHyY3c3KO17UE"

genai.configure(api_key=GEMINI_KEY)
# Используем стабильное имя модели
ai_model = genai.GenerativeModel('models/gemini-1.5-flash')

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

# === БАЗА ДАННЫХ ===
def get_db():
    return sqlite3.connect("med_bot.db")

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS reminders 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                          name TEXT, time TEXT, stock INTEGER)''')
        conn.commit()
    logger.info("База данных готова.")

# === СОСТОЯНИЯ ===
class MedStates(StatesGroup):
    waiting_name = State()
    waiting_time = State()
    waiting_stock = State()

# === ФУНКЦИИ ===
async def send_reminder(chat_id: int, med_id: int, med_name: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принял", callback_data=f"med_done_{med_id}")
    builder.button(text="⏰ +15 мин", callback_data=f"med_snooze_{med_id}")
    try:
        await bot.send_message(chat_id, f"🚨 **ВРЕМЯ ПРИЕМА!**\n💊 Препарат: {med_name}", reply_markup=builder.as_markup(), parse_mode="Markdown")
    except: pass

# === ОБРАБОТЧИКИ (ВАЖЕН ПОРЯДОК) ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("👋 Привет! Я бот-аптечка.\n📸 Пришли фото или нажми /add")

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, time, stock FROM reminders WHERE user_id = ?", (message.from_user.id,))
        rows = cursor.fetchall()
    if not rows: return await message.answer("Список пуст.")
    res = "📋 Ваши лекарства:\n" + "\n".join([f"🔹 {r[0]} — {r[1]} ({r[2]} шт.)" for r in rows])
    await message.answer(res)

@dp.message(Command("add"))
async def add_start(message: types.Message, state: FSMContext):
    await message.answer("Введите название лекарства:")
    await state.set_state(MedStates.waiting_name)

@dp.message(MedStates.waiting_name)
async def add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите время приема (ЧЧ:ММ):")
    await state.set_state(MedStates.waiting_time)

@dp.message(MedStates.waiting_time)
async def add_time(message: types.Message, state: FSMContext):
    await state.update_data(time=message.text)
    await message.answer("Сколько таблеток осталось?")
    await state.set_state(MedStates.waiting_stock)

@dp.message(MedStates.waiting_stock)
async def add_stock(message: types.Message, state: FSMContext):
    data = await state.get_data()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO reminders (user_id, name, time, stock) VALUES (?, ?, ?, ?)",
                       (message.from_user.id, data['name'], data['time'], int(message.text)))
        med_id = cursor.lastrowid
        conn.commit()
    
    try:
        h, m = map(int, data['time'].split(":"))
        scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[message.chat.id, med_id, data['name']], id=f"job_{med_id}")
        await message.answer(f"✅ Добавлено: {data['name']} в {data['time']}")
    except:
        await message.answer("❌ Ошибка в формате времени. Удалите и добавьте заново.")
    await state.clear()

@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    msg = await message.answer("🔄 Анализирую...")
    try:
        file_info = await bot.get_file(message.photo[-1].file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        # Указываем модель через models/
        response = ai_model.generate_content(["Инструкция лекарства. Напиши название и время ЧЧ:ММ.", {"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}])
        await msg.edit_text(f"🤖 ИИ: {response.text}\n\nВведите название для сохранения:")
        await state.set_state(MedStates.waiting_name)
    except Exception as e:
        await msg.edit_text(f"Ошибка ИИ. Введите название вручную через /add")

# ЗАГЛУШКА - ВСЕГДА В КОНЦЕ
@dp.message()
async def echo_message(message: types.Message):
    await message.answer("Пожалуйста, используйте команды меню или пришлите фото.")

@dp.callback_query(F.data.startswith("med_"))
async def handle_callback(callback: types.CallbackQuery):
    _, action, med_id = callback.data.split("_")
    with get_db() as conn:
        cursor = conn.cursor()
        if action == "done":
            cursor.execute("UPDATE reminders SET stock = stock - 1 WHERE id = ?", (med_id,))
            conn.commit()
            await callback.message.edit_text("✅ Принято!")
        elif action == "snooze":
            scheduler.add_job(send_reminder, "date", run_date=datetime.now() + timedelta(minutes=15), args=[callback.message.chat.id, med_id, "Повтор"])
            await callback.answer("⏳ Напомню через 15 мин")

async def main():
    init_db()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
