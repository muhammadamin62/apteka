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
ai_model = genai.GenerativeModel('gemini-1.5-flash')

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

# === СОСТОЯНИЯ ===
class MedStates(StatesGroup):
    waiting_name = State()
    waiting_frequency = State()
    waiting_times = State()
    waiting_stock = State()

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
async def send_reminder(chat_id: int, med_id: int, med_name: str, time: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принял", callback_data=f"done_{med_id}")
    try:
        await bot.send_message(chat_id, f"🔔 **ПОРА ПИТЬ!**\n💊 {med_name}\n⏰ Время: {time}", 
                               reply_markup=builder.as_markup(), parse_mode="Markdown")
    except: pass

# === ОБРАБОТЧИКИ ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("💊 **Бот-Аптечка запущен!**\n\n📸 Пришли фото упаковки\n📋 /list — мои лекарства\n➕ /add — добавить вручную")

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, time, stock FROM reminders WHERE user_id = ?", (message.from_user.id,))
        rows = cursor.fetchall()
    
    if not rows:
        return await message.answer("Ваш список пуст.")
    
    await message.answer("📋 **Ваш список лекарств:**")
    for r in rows:
        builder = InlineKeyboardBuilder()
        builder.button(text="🗑 Удалить", callback_data=f"del_{r[0]}")
        await message.answer(f"💊 **{r[1]}**\n⏰ Время: {r[2]}\n📦 Остаток: {r[3]} шт.", 
                             reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    msg = await message.answer("🔄 ИИ анализирует фото...")
    try:
        file_info = await bot.get_file(message.photo[-1].file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        
        prompt = "Это лекарство. Напиши кратко: название и для чего оно. В конце напиши 'Частота: 2 раза' (или 1 или 3 на основе инструкции)."
        response = ai_model.generate_content([prompt, {"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}])
        
        await msg.edit_text(f"🤖 **Информация от ИИ:**\n{response.text}\n\nВведите название лекарства:")
        await state.set_state(MedStates.waiting_name)
    except Exception as e:
        logger.error(e)
        await msg.edit_text("❌ Ошибка ИИ. Введите название вручную:")
        await state.set_state(MedStates.waiting_name)

@dp.message(MedStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    builder = InlineKeyboardBuilder()
    builder.button(text="1 раз в день", callback_data="freq_1")
    builder.button(text="2 раза в день", callback_data="freq_2")
    builder.button(text="3 раза в день", callback_data="freq_3")
    await message.answer("Сколько раз в день нужно принимать?", reply_markup=builder.as_markup())
    await state.set_state(MedStates.waiting_frequency)

@dp.callback_query(F.data.startswith("freq_"))
async def process_freq(callback: types.CallbackQuery, state: FSMContext):
    freq = int(callback.data.split("_")[1])
    await state.update_data(freq=freq, current_step=1, times=[])
    await callback.message.edit_text(f"Укажите время для 1-го приема (например, 08:00):")
    await state.set_state(MedStates.waiting_times)

@dp.message(MedStates.waiting_times)
async def process_times(message: types.Message, state: FSMContext):
    data = await state.get_data()
    times = data.get('times', [])
    times.append(message.text)
    
    if len(times) < data['freq']:
        await state.update_data(times=times)
        await message.answer(f"Укажите время для {len(times)+1}-го приема:")
    else:
        await state.update_data(times=times)
        await message.answer("Сколько всего таблеток в упаковке?")
        await state.set_state(MedStates.waiting_stock)

@dp.message(MedStates.waiting_stock)
async def process_stock(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введите число.")
    
    data = await state.get_data()
    all_times = ", ".join(data['times'])
    
    with get_db() as conn:
        cursor = conn.cursor()
        for t in data['times']:
            cursor.execute("INSERT INTO reminders (user_id, name, time, stock) VALUES (?, ?, ?, ?)",
                           (message.from_user.id, data['name'], t, int(message.text)))
            med_id = cursor.lastrowid
            try:
                h, m = map(int, t.split(":"))
                scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[message.chat.id, med_id, data['name'], t], id=f"job_{med_id}")
            except: continue
        conn.commit()

    await message.answer(f"✅ Готово! {data['name']} добавлено. Время: {all_times}")
    await state.clear()

@dp.callback_query(F.data.startswith("del_"))
async def delete_item(callback: types.CallbackQuery):
    med_id = callback.data.split("_")[1]
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id = ?", (med_id,))
        conn.commit()
    try: scheduler.remove_job(f"job_{med_id}")
    except: pass
    await callback.message.delete()
    await callback.answer("Удалено")

@dp.callback_query(F.data.startswith("done_"))
async def mark_done(callback: types.CallbackQuery):
    med_id = callback.data.split("_")[1]
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE reminders SET stock = stock - 1 WHERE id = ?", (med_id,))
        conn.commit()
    await callback.message.edit_text("✅ Вы выпили лекарство! Остаток уменьшен.")

async def main():
    init_db()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
