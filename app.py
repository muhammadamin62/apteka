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

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ ===
# === КОНФИГУРАЦИЯ ===
# Вставляем токены напрямую, чтобы Railway не капризничал
TOKEN = "8237149954:AAHTLCBGKzbnR8ATXlrYkK1SIMac6TyA-a8"
GEMINI_KEY = "AIzaSyDTLdI8T5MvgR4EDhYm49OHyY3c3KO17UE"

# Настройка ИИ
genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash-latest')

# ... (твои импорты и токены) ...

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ВОТ ЭТА СТРОКА ДОЛЖНА БЫТЬ ТУТ:
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent") 

# ... (дальше идет остальной код: init_db, функции и т.д.) ...
# === БАЗА ДАННЫХ ===
def get_db():
    return sqlite3.connect("med_bot.db")

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS reminders 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                          name TEXT, time TEXT, stock INTEGER)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS history 
                         (user_id INTEGER, name TEXT, timestamp TEXT)''')
        conn.commit()
    logger.info("База данных готова.")

# === ЛОГИКА УВЕДОМЛЕНИЙ ===
async def send_reminder(chat_id: int, med_id: int, med_name: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принял", callback_data=f"med_done_{med_id}")
    builder.button(text="⏰ +15 мин", callback_data=f"med_snooze_{med_id}")

    try:
        await bot.send_message(
            chat_id,
            f"🚨 **ВРЕМЯ ПИТЬ ЛЕКАРСТВО!**\n💊 Препарат: **{med_name}**\n\nПодтвердите прием кнопкой:",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")

# === СОСТОЯНИЯ (FSM) ===
class MedStates(StatesGroup):
    waiting_name = State()
    waiting_time = State()
    waiting_stock = State()

# === ОБРАБОТЧИКИ ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я твой ИИ-ассистент.\n\n"
        "📸 **Пришли фото инструкции** или упаковки\n"
        "➕ /add — добавить вручную\n"
        "📋 /list — твой список"
    )

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, time, stock FROM reminders WHERE user_id = ?", (message.from_user.id,))
        rows = cursor.fetchall()

    if not rows:
        return await message.answer("Список пуст. Добавь что-нибудь через /add")

    res = "📋 **Ваши лекарства:**\n"
    for r in rows:
        res += f"🔹 {r[0]} — {r[1]} (Остаток: {r[2]} шт.)\n"
    await message.answer(res, parse_mode="Markdown")

@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    processing_msg = await message.answer("🔄 ИИ изучает фото...")
    
    try:
        file_info = await bot.get_file(message.photo[-1].file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        
        prompt = "Это лекарство. Напиши только название и время приема (ЧЧ:ММ). Например: 'Аспирин 08:00'. Если времени нет, напиши 09:00."
        
        response = ai_model.generate_content([
            prompt, 
            {"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}
        ])
        
        await processing_msg.edit_text(f"🤖 **ИИ распознал:**\n{response.text}\n\nВведите название лекарства (или скопируйте):")
        await state.set_state(MedStates.waiting_name)
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        await processing_msg.edit_text("❌ Не удалось распознать. Введите название вручную через /add")

@dp.message(MedStates.waiting_name)
async def add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите время приема (например, 08:00):")
    await state.set_state(MedStates.waiting_time)

@dp.message(MedStates.waiting_time)
async def add_time(message: types.Message, state: FSMContext):
    time_val = message.text.strip()
    try:
        datetime.strptime(time_val, "%H:%M")
        await state.update_data(time=time_val)
        await message.answer("Сколько таблеток в упаковке?")
        await state.set_state(MedStates.waiting_stock)
    except:
        await message.answer("⚠️ Неверный формат. Нужно ЧЧ:ММ (например, 12:30)")

@dp.message(MedStates.waiting_stock)
async def add_stock(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("🔢 Введите число.")

    data = await state.get_data()
    stock = int(message.text)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO reminders (user_id, name, time, stock) VALUES (?, ?, ?, ?)",
                       (message.from_user.id, data['name'], data['time'], stock))
        med_id = cursor.lastrowid
        conn.commit()

    h, m = map(int, data['time'].split(":"))
    scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[message.chat.id, med_id, data['name']], id=f"job_{med_id}")

    await message.answer(f"✅ Успешно! Буду напоминать о **{data['name']}** в {data['time']}.")
    await state.clear()

@dp.callback_query(F.data.startswith("med_"))
async def handle_callback(callback: types.CallbackQuery):
    _, action, med_id = callback.data.split("_")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, stock FROM reminders WHERE id = ?", (med_id,))
        res = cursor.fetchone()

        if not res: return await callback.answer("Не найдено.")

        if action == "done":
            new_stock = res[1] - 1
            cursor.execute("UPDATE reminders SET stock = ? WHERE id = ?", (new_stock, med_id))
            conn.commit()
            text = f"✅ Принято! Остаток: {new_stock} шт."
            if new_stock <= 3: text += "\n⚠️ Заканчивается!"
            await callback.message.edit_text(text)
        elif action == "snooze":
            run_time = datetime.now() + timedelta(minutes=15)
            scheduler.add_job(send_reminder, "date", run_date=run_time, args=[callback.message.chat.id, med_id, res[0]])
            await callback.answer("⏳ Напомню через 15 минут")

# Заглушка для любого другого текста
@dp.message()
async def echo_message(message: types.Message):
    await message.answer("Я тебя не совсем понял. Используй меню /start или пришли фото лекарства.")

# === ЗАПУСК ===
async def restore_jobs():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, id, name, time FROM reminders")
        for u_id, m_id, name, t_str in cursor.fetchall():
            try:
                h, m = map(int, t_str.split(":"))
                scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[u_id, m_id, name], id=f"job_{m_id}")
            except: continue

async def main():
    init_db()
    await restore_jobs()
    scheduler.start()
    logger.info("БОТ ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
