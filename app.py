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

# === УВЕДОМЛЕНИЯ ===
async def send_reminder(chat_id: int, med_id: int, med_name: str, time_val: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принял (Минус 1 шт)", callback_data=f"done_{med_id}")
    try:
        await bot.send_message(
            chat_id, 
            f"⏰ **ВРЕМЯ ПРИЕМА!**\n💊 Препарат: **{med_name}**\n🕒 Установленное время: {time_val}", 
            reply_markup=builder.as_markup(), 
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления: {e}")

# === ОБРАБОТЧИКИ ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear() # Сбрасываем старые зависшие вводы
    await message.answer(
        "👋 Привет! Я твой медицинский ассистент.\n\n"
        "📸 **Отправь фото упаковки**, чтобы я распознал её,\n"
        "➕ Или нажми /add для ручного ввода."
    )

@dp.message(Command("add"))
async def add_manual(message: types.Message, state: FSMContext):
    await state.clear() # Важно: очищаем старое состояние, чтобы /add всегда срабатывал
    await message.answer("✍️ Введите название лекарства:")
    await state.set_state(MedStates.waiting_name)

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, time, stock FROM reminders WHERE user_id = ?", (message.from_user.id,))
        rows = cursor.fetchall()
    
    if not rows:
        return await message.answer("📭 Ваш список пуст. Используйте /add или пришлите фото.")
    
    await message.answer("📋 **Ваши напоминания:**")
    for r in rows:
        builder = InlineKeyboardBuilder()
        builder.button(text="🗑 Удалить", callback_data=f"del_{r[0]}")
        await message.answer(
            f"💊 **{r[1]}**\n⏰ Время: {r[2]}\n📦 Остаток: {r[3]} шт.", 
            reply_markup=builder.as_markup(), 
            parse_mode="Markdown"
        )

@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    await state.clear()
    status_msg = await message.answer("🔍 Читаю текст на упаковке через ИИ...")
    
    try:
        file_info = await bot.get_file(message.photo[-1].file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        
        # Улучшенный промпт для ИИ
        prompt = (
            "Проанализируй фото лекарства. Напиши: 1. Название. 2. Для чего оно. "
            "3. Рекомендуемая частота в день (если есть в тексте). "
            "Если текст неразборчив, напиши только название, которое видишь."
        )
        
        response = ai_model.generate_content([prompt, {"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}])
        
        await status_msg.edit_text(
            f"🤖 **Распознано:**\n\n{response.text}\n\n"
            f"📝 Пожалуйста, введите название лекарства для сохранения:"
        )
        await state.set_state(MedStates.waiting_name)
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        await status_msg.edit_text("❌ Не удалось прочитать фото. Попробуйте при более ярком свете или введите название через /add")

@dp.message(MedStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    builder = InlineKeyboardBuilder()
    builder.button(text="1 раз", callback_data="freq_1")
    builder.button(text="2 раза", callback_data="freq_2")
    builder.button(text="3 раза", callback_data="freq_3")
    builder.button(text="4 раза", callback_data="freq_4") # Добавили 4 раза
    builder.adjust(2) # Кнопки в 2 ряда
    await message.answer("Сколько раз в день будете принимать?", reply_markup=builder.as_markup())
    await state.set_state(MedStates.waiting_frequency)

@dp.callback_query(F.data.startswith("freq_"))
async def process_freq(callback: types.CallbackQuery, state: FSMContext):
    freq = int(callback.data.split("_")[1])
    await state.update_data(freq=freq, times=[])
    await callback.message.edit_text(f"🕒 Укажите время для **1-го** приема (например, 08:00):")
    await state.set_state(MedStates.waiting_times)

@dp.message(MedStates.waiting_times)
async def process_times(message: types.Message, state: FSMContext):
    data = await state.get_data()
    times = data.get('times', [])
    
    # Валидация времени
    try:
        datetime.strptime(message.text.strip(), "%H:%M")
        times.append(message.text.strip())
    except:
        return await message.answer("❌ Неверный формат! Напишите время как 09:30")

    if len(times) < data['freq']:
        await state.update_data(times=times)
        await message.answer(f"🕒 Укажите время для **{len(times)+1}-го** приема:")
    else:
        await state.update_data(times=times)
        await message.answer("📦 Сколько таблеток/доз всего в упаковке? (Введите число)")
        await state.set_state(MedStates.waiting_stock)

@dp.message(MedStates.waiting_stock)
async def process_stock(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("🔢 Пожалуйста, введите только число.")
    
    data = await state.get_data()
    stock_count = int(message.text)
    
    with get_db() as conn:
        cursor = conn.cursor()
        for t in data['times']:
            cursor.execute(
                "INSERT INTO reminders (user_id, name, time, stock) VALUES (?, ?, ?, ?)",
                (message.from_user.id, data['name'], t, stock_count)
            )
            med_id = cursor.lastrowid
            
            # Ставим задачу в планировщик
            h, m = map(int, t.split(":"))
            scheduler.add_job(
                send_reminder, "cron", hour=h, minute=m, 
                args=[message.chat.id, med_id, data['name'], t], 
                id=f"job_{med_id}"
            )
        conn.commit()

    await message.answer(f"✅ **Успешно добавлено!**\n💊 {data['name']}\n🕒 Время: {', '.join(data['times'])}\n📦 Остаток: {stock_count} шт.")
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
    
    await callback.message.edit_text("🗑 Удалено из вашего списка.")
    await callback.answer()

@dp.callback_query(F.data.startswith("done_"))
async def mark_done(callback: types.CallbackQuery):
    med_id = callback.data.split("_")[1]
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE reminders SET stock = stock - 1 WHERE id = ?", (med_id,))
        conn.commit()
        cursor.execute("SELECT stock FROM reminders WHERE id = ?", (med_id,))
        new_stock = cursor.fetchone()[0]
    
    await callback.message.edit_text(f"✅ Принято! Остаток в упаковке: {new_stock} шт.")
    if new_stock <= 3:
        await callback.message.answer("⚠️ Внимание! Лекарство почти закончилось.")

# === ЗАПУСК ===
async def main():
    init_db()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
