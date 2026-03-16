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
TOKEN = "8237149954:AAHTLCBGKzbnR8ATXlrYkK1SIMac6TyA-a8"
GEMINI_KEY = "AIzaSyDTLdI8T5MvgR4EDhYm49OHyY3c3KO17UE"

genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect("med_bot.db") as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS reminders 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                          name TEXT, time TEXT, stock INTEGER)''')

class MedStates(StatesGroup):
    waiting_name = State()
    waiting_frequency = State()
    waiting_times = State()
    waiting_stock = State()

# === ЛОГИКА ИИ ===
async def get_med_info(med_name: str):
    prompt = f"Ты фармацевт. Найди инфо про '{med_name}'. Напиши: 1. Для чего. 2. Как принимать (кратко). 3. Противопоказания. На русском."
    try:
        response = ai_model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "Инструкция не найдена, но вы можете продолжить настройку."

# === УВЕДОМЛЕНИЯ ===
async def send_reminder(chat_id: int, med_id: int, med_name: str, time_val: str):
    nag_id = f"nag_{med_id}"
    if scheduler.get_job(nag_id): scheduler.remove_job(nag_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принял", callback_data=f"done_{med_id}")
    builder.button(text="⏰ +15 мин", callback_data=f"snooze_{med_id}_{med_name}_{time_val}")
    builder.adjust(1)
    
    msg = f"🔔 **ВРЕМЯ ПРИЕМА!**\n💊 Препарат: **{med_name}**\n🕒 Время: {time_val}"
    
    try:
        await bot.send_message(chat_id, msg, reply_markup=builder.as_markup(), parse_mode="Markdown")
        # Повтор через 2 минуты
        scheduler.add_job(send_reminder, "date", run_date=datetime.now() + timedelta(minutes=2),
                          args=[chat_id, med_id, med_name, time_val], id=nag_id)
    except: pass

# === ОБРАБОТЧИКИ ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("💊 **Аптечный Помощник**\n\nПросто напиши название лекарства или пришли фото упаковки!")

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    with sqlite3.connect("med_bot.db") as conn:
        rows = conn.execute("SELECT id, name, time, stock FROM reminders WHERE user_id = ?", (message.from_user.id,)).fetchall()
    if not rows: return await message.answer("Список пуст.")
    for r in rows:
        kb = InlineKeyboardBuilder()
        kb.button(text="🗑 Удалить", callback_data=f"del_{r[0]}")
        await message.answer(f"💊 {r[1]} | ⏰ {r[2]} | 📦 {r[3]}шт", reply_markup=kb.as_markup())

@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    status = await message.answer("📸 Сканирую фото...")
    try:
        file_info = await bot.get_file(message.photo[-1].file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        prompt = "Напиши только название лекарства с этого фото."
        response = ai_model.generate_content([prompt, {"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}])
        med_name = response.text.strip()
        await start_med_process(message, state, med_name, status)
    except:
        await status.edit_text("❌ Не удалось распознать. Введите название текстом:")
        await state.set_state(MedStates.waiting_name)

# Функция запуска процесса (общая для текста и фото)
async def start_med_process(message: types.Message, state: FSMContext, med_name: str, status_msg=None):
    if status_msg:
        await status_msg.edit_text(f"🔍 Ищу инфо о **{med_name}**...")
    else:
        status_msg = await message.answer(f"🔍 Ищу инфо о **{med_name}**...")
    
    info = await get_med_info(med_name)
    await message.answer(f"📋 **Инфо:**\n{info}")
    
    await state.update_data(name=med_name)
    builder = InlineKeyboardBuilder()
    for i in range(1, 5): builder.button(text=f"{i} р/д", callback_data=f"f_{i}")
    builder.adjust(2)
    await message.answer(f"Сколько раз в день принимать **{med_name}**?", reply_markup=builder.as_markup())
    await state.set_state(MedStates.waiting_frequency)

# Обработчик любого текста (если не в состоянии FSM)
@dp.message(F.text, ~F.text.startswith('/'))
async def handle_just_text(message: types.Message, state: FSMContext):
    # Если пользователь уже что-то вводит, не перехватываем
    current_state = await state.get_state()
    if current_state is None:
        await start_med_process(message, state, message.text)
    elif current_state == MedStates.waiting_name:
        await start_med_process(message, state, message.text)

@dp.callback_query(F.data.startswith("f_"))
async def process_freq(callback: types.CallbackQuery, state: FSMContext):
    freq = int(callback.data.split("_")[1])
    await state.update_data(freq=freq, times=[])
    await callback.message.edit_text(f"🕒 Введите время 1-го приема (например 08:00):")
    await state.set_state(MedStates.waiting_times)

@dp.message(MedStates.waiting_times)
async def process_times(message: types.Message, state: FSMContext):
    data = await state.get_data()
    times = data.get('times', [])
    times.append(message.text.strip())
    
    if len(times) < data['freq']:
        await state.update_data(times=times)
        await message.answer(f"🕒 Время {len(times)+1}-го приема:")
    else:
        await state.update_data(times=times)
        await message.answer("📦 Остаток таблеток (число):")
        await state.set_state(MedStates.waiting_stock)

@dp.message(MedStates.waiting_stock)
async def process_stock(message: types.Message, state: FSMContext):
    data = await state.get_data()
    with sqlite3.connect("med_bot.db") as conn:
        for t in data['times']:
            cursor = conn.execute("INSERT INTO reminders (user_id, name, time, stock) VALUES (?, ?, ?, ?)",
                                 (message.from_user.id, data['name'], t, int(message.text)))
            med_id = cursor.lastrowid
            h, m = map(int, t.split(":"))
            scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[message.chat.id, med_id, data['name'], t], id=f"main_{med_id}")
    await message.answer(f"✅ Готово! Список: /list")
    await state.clear()

@dp.callback_query(F.data.startswith("done_"))
async def med_done(callback: types.CallbackQuery):
    med_id = callback.data.split("_")[1]
    nag_id = f"nag_{med_id}"
    if scheduler.get_job(nag_id): scheduler.remove_job(nag_id)
    with sqlite3.connect("med_bot.db") as conn:
        conn.execute("UPDATE reminders SET stock = stock - 1 WHERE id = ?", (med_id,))
    await callback.message.edit_text("✅ Принято!")
    await callback.answer()

@dp.callback_query(F.data.startswith("snooze_"))
async def med_snooze(callback: types.CallbackQuery):
    _, med_id, name, t_val = callback.data.split("_")
    nag_id = f"nag_{med_id}"
    if scheduler.get_job(nag_id): scheduler.remove_job(nag_id)
    new_time = datetime.now() + timedelta(minutes=15)
    scheduler.add_job(send_reminder, "date", run_date=new_time, args=[callback.message.chat.id, med_id, name, t_val], id=f"snz_{med_id}")
    await callback.message.edit_text(f"⏳ Отложено до {new_time.strftime('%H:%M')}")

@dp.callback_query(F.data.startswith("del_"))
async def med_del(callback: types.CallbackQuery):
    mid = callback.data.split("_")[1]
    with sqlite3.connect("med_bot.db") as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (mid,))
    await callback.message.delete()

async def main():
    init_db()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
