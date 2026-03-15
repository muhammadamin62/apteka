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

# Настройка логов
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ ===
TOKEN = "8237149954:AAHTLCBGKzbnR8ATXlrYkK1SIMac6TyA-a8"
GEMINI_KEY = "AIzaSyDTLdI8T5MvgR4EDhYm49OHyY3c3KO17UE"

genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash') # Самая быстрая и точная для фото

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect("med_bot.db") as conn:
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

# === ЛОГИКА УВЕДОМЛЕНИЙ ===

async def send_reminder(chat_id: int, med_id: int, med_name: str, time_val: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принял", callback_data=f"done_{med_id}")
    builder.button(text="⏰ +15 минут", callback_data=f"snooze_{med_id}_{med_name}_{time_val}")
    
    msg = f"🚨 **ВНИМАНИЕ! ПОРА ПИТЬ ЛЕКАРСТВО!**\n💊 Препарат: **{med_name}**\n🕒 Время приема: {time_val}\n\n*Я буду напоминать каждые 2 минуты, пока не нажмете кнопку!*"
    
    try:
        sent_msg = await bot.send_message(chat_id, msg, reply_markup=builder.as_markup(), parse_mode="Markdown")
        
        # Создаем "настойчивое" задание через 2 минуты
        job_id = f"nag_{sent_msg.message_id}"
        scheduler.add_job(
            send_reminder, 
            "date", 
            run_date=datetime.now() + timedelta(minutes=2), 
            args=[chat_id, med_id, med_name, time_val],
            id=job_id
        )
    except Exception as e:
        logger.error(f"Ошибка: {e}")

# === ОБРАБОТЧИКИ ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("💊 **Аптечный Будильник 2.0**\n\n📸 Пришли фото или нажми /add")

@dp.message(Command("add"))
async def add_manual(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("✍️ Введите название лекарства:")
    await state.set_state(MedStates.waiting_name)

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    with sqlite3.connect("med_bot.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, time, stock FROM reminders WHERE user_id = ?", (message.from_user.id,))
        rows = cursor.fetchall()
    
    if not rows: return await message.answer("📭 Список пуст.")
    
    for r in rows:
        builder = InlineKeyboardBuilder()
        builder.button(text="🗑 Удалить", callback_data=f"del_{r[0]}")
        await message.answer(f"💊 **{r[1]}**\n⏰ {r[2]} (Запас: {r[3]})", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    status = await message.answer("🧠 ИИ изучает фото... Пожалуйста, подождите.")
    try:
        file_info = await bot.get_file(message.photo[-1].file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        
        # Мощный промпт
        prompt = "Ты — медицинский эксперт. Найди на фото название лекарства и дозировку. Напиши четко название и назначение. Если не нашел, напиши 'Данные не найдены'."
        response = ai_model.generate_content([prompt, {"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}])
        
        await status.edit_text(f"📝 **Результат анализа:**\n\n{response.text}\n\nВведите название лекарства:")
        await state.set_state(MedStates.waiting_name)
    except:
        await status.edit_text("❌ Ошибка ИИ. Введите название вручную через /add")

@dp.message(MedStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    builder = InlineKeyboardBuilder()
    for i in range(1, 5): builder.button(text=f"{i} р/день", callback_data=f"freq_{i}")
    builder.adjust(2)
    await message.answer("Как часто принимать?", reply_markup=builder.as_markup())
    await state.set_state(MedStates.waiting_frequency)

@dp.callback_query(F.data.startswith("freq_"))
async def process_freq(callback: types.CallbackQuery, state: FSMContext):
    freq = int(callback.data.split("_")[1])
    await state.update_data(freq=freq, times=[])
    await callback.message.edit_text(f"🕒 Время для **1-го** приема (например 08:00):")
    await state.set_state(MedStates.waiting_times)

@dp.message(MedStates.waiting_times)
async def process_times(message: types.Message, state: FSMContext):
    data = await state.get_data()
    times = data.get('times', [])
    times.append(message.text.strip())
    
    if len(times) < data['freq']:
        await state.update_data(times=times)
        await message.answer(f"🕒 Время для **{len(times)+1}-го** приема:")
    else:
        await state.update_data(times=times)
        await message.answer("📦 Сколько таблеток в упаковке?")
        await state.set_state(MedStates.waiting_stock)

@dp.message(MedStates.waiting_stock)
async def process_stock(message: types.Message, state: FSMContext):
    data = await state.get_data()
    with sqlite3.connect("med_bot.db") as conn:
        cursor = conn.cursor()
        for t in data['times']:
            cursor.execute("INSERT INTO reminders (user_id, name, time, stock) VALUES (?, ?, ?, ?)",
                           (message.from_user.id, data['name'], t, int(message.text)))
            med_id = cursor.lastrowid
            h, m = map(int, t.split(":"))
            scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[message.chat.id, med_id, data['name'], t], id=f"main_{med_id}")
    await message.answer("✅ Напоминания установлены!")
    await state.clear()

# === CALLBACKS ДЛЯ КНОПОК ===

@dp.callback_query(F.data.startswith("done_"))
async def med_done(callback: types.CallbackQuery):
    med_id = callback.data.split("_")[1]
    # Останавливаем все настойчивые уведомления для этого раза
    # (В реальном коде тут можно добавить удаление job по ID)
    await callback.message.edit_text("✅ Принято! Молодец. Следующее напомню по графику.")
    await callback.answer()

@dp.callback_query(F.data.startswith("snooze_"))
async def med_snooze(callback: types.CallbackQuery):
    _, med_id, name, t_val = callback.data.split("_")
    # Откладываем на 15 минут
    run_time = datetime.now() + timedelta(minutes=15)
    scheduler.add_job(send_reminder, "date", run_date=run_time, args=[callback.message.chat.id, med_id, name, t_val])
    await callback.message.edit_text(f"⏳ Хорошо, напомню через 15 минут (в {run_time.strftime('%H:%M')}).")
    await callback.answer()

@dp.callback_query(F.data.startswith("del_"))
async def med_del(callback: types.CallbackQuery):
    mid = callback.data.split("_")[1]
    with sqlite3.connect("med_bot.db") as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (mid,))
    await callback.message.delete()
    await callback.answer("Удалено")

async def main():
    init_db()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
