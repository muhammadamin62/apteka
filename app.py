import os
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import google.generativeai as genai

# Logging sozlamalari
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === KONFIGURATSIYA ===
TOKEN = "8237149954:AAHTLCBGKzbnR8ATXlrYkK1SIMac6TyA-a8"
GEMINI_KEY = "AIzaSyDTLdI8T5MvgR4EDhYm49OHyY3c3KO17UE"

genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

# === BAZA BILAN ISHLASH ===
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

# === ASOSIY TUGMALAR ===
def main_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Dori qo‘shish")
    builder.button(text="📋 Mening dorilarim")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)

# === ESLATMA YUBORISH LOGIKASI ===
async def send_reminder(chat_id: int, med_id: int, med_name: str, time_val: str):
    nag_id = f"nag_{med_id}"
    if scheduler.get_job(nag_id): scheduler.remove_job(nag_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ichdim", callback_data=f"done_{med_id}")
    builder.button(text="⏰ +15 daqiqa", callback_data=f"snooze_{med_id}_{med_name}_{time_val}")
    builder.adjust(2)
    
    msg = f"✨ **VAQT KELDI** ✨\n\n💊 Dori nomi: **{med_name}**\n🕒 Rejadagi vaqt: {time_val}\n\n*Salomatligingizga befarq bo‘lmang!*"
    
    try:
        await bot.send_message(chat_id, msg, reply_markup=builder.as_markup(), parse_mode="Markdown")
        # Har 2 daqiqada eslatib turish
        scheduler.add_job(send_reminder, "date", run_date=datetime.now() + timedelta(minutes=2),
                          args=[chat_id, med_id, med_name, time_val], id=nag_id)
    except: pass

# === BUYRUQLAR VA MATNLAR ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"🌿 **Xush kelibsiz, {message.from_user.first_name}!**\n\n"
        "Men sizga nashachani yoki oxey dorichalarni o‘z vaqtida ichishingizni eslatib turaman. "
        "Osmonga uchish uchun pastdagi tugmalardan birini tanlang.",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@dp.message(F.text == "➕ Dori qo‘shish")
async def add_btn(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🧪 **Nashachani nomini yozing** (yoki qadoq rasmini yuboring):", parse_mode="Markdown")
    await state.set_state(MedStates.waiting_name)

@dp.message(F.text == "📋 Mening dorilarim")
async def list_btn(message: types.Message):
    with sqlite3.connect("med_bot.db") as conn:
        rows = conn.execute("SELECT id, name, time, stock FROM reminders WHERE user_id = ?", (message.from_user.id,)).fetchall()
    
    if not rows:
        return await message.answer("📭 Dorilaringiz ro‘yxati bo‘sh. «Nasha qo‘shish» tugmasini bosing.")
    
    await message.answer("📋 **Sizning jadvalingiz:**", parse_mode="Markdown")
    for r in rows:
        kb = InlineKeyboardBuilder()
        kb.button(text="🔍 Ma’lumot", callback_data=f"info_{r[1]}")
        kb.button(text="🗑 O‘chirish", callback_data=f"del_{r[0]}")
        await message.answer(f"🔹 **{r[1]}**\n⏰ Vaqti: {r[2]} | 📦 Qolgan: {r[3]} ta", reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.message(MedStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    builder = InlineKeyboardBuilder()
    for i in range(1, 5): builder.button(text=f"{i} mahal", callback_data=f"f_{i}")
    builder.adjust(2)
    await message.answer(f"🔢 **{message.text}** Nashachani kuniga necha mahal ichasiz?", reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.set_state(MedStates.waiting_frequency)

@dp.callback_query(F.data.startswith("f_"))
async def process_freq(callback: types.CallbackQuery, state: FSMContext):
    freq = int(callback.data.split("_")[1])
    await state.update_data(freq=freq, times=[])
    await callback.message.edit_text(f"🕒 **1-ichish vaqtini** kiriting (masalan, 08:00):", parse_mode="Markdown")
    await state.set_state(MedStates.waiting_times)

@dp.message(MedStates.waiting_times)
async def process_times(message: types.Message, state: FSMContext):
    data = await state.get_data()
    times = data.get('times', [])
    times.append(message.text.strip())
    
    if len(times) < data['freq']:
        await state.update_data(times=times)
        await message.answer(f"🕒 **{len(times)+1}-ichish vaqtini** kiriting:", parse_mode="Markdown")
    else:
        await state.update_data(times=times)
        await message.answer("📦 Qutida nechta tabletka bor? (sonini yozing)", parse_mode="Markdown")
        await state.set_state(MedStates.waiting_stock)

@dp.message(MedStates.waiting_stock)
async def process_stock(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("🔢 Iltimos, faqat son kiriting.")
    
    data = await state.get_data()
    with sqlite3.connect("med_bot.db") as conn:
        for t in data['times']:
            cursor = conn.execute("INSERT INTO reminders (user_id, name, time, stock) VALUES (?, ?, ?, ?)",
                                 (message.from_user.id, data['name'], t, int(message.text)))
            med_id = cursor.lastrowid
            h, m = map(int, t.split(":"))
            scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[message.chat.id, med_id, data['name'], t], id=f"main_{med_id}")
    
    await message.answer(f"✅ **Muvaffaqiyatli!**\n**{data['name']}** dorisi jadvalga qo‘shildi.", reply_markup=main_menu(), parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data.startswith("info_"))
async def info_btn(callback: types.CallbackQuery):
    med_name = callback.data.split("_")[1]
    await callback.answer("Ma’lumot qidirilmoqda...")
    prompt = f"Lekarstvo {med_name}. Napishi ochen kratko dlya chego ono i kak primenyat na uzbekskom yazike."
    response = ai_model.generate_content(prompt)
    await callback.message.answer(f"ℹ️ **{med_name} haqida:**\n\n{response.text}", parse_mode="Markdown")

@dp.callback_query(F.data.startswith("done_"))
async def med_done(callback: types.CallbackQuery):
    med_id = callback.data.split("_")[1]
    nag_id = f"nag_{med_id}"
    if scheduler.get_job(nag_id): scheduler.remove_job(nag_id)
    with sqlite3.connect("med_bot.db") as conn:
        conn.execute("UPDATE reminders SET stock = stock - 1 WHERE id = ?", (med_id,))
    await callback.message.edit_text("🌈 **Barakalla!** Shifo bo‘lsin. Eslatmalar to‘xtatildi.", parse_mode="Markdown")

@dp.callback_query(F.data.startswith("snooze_"))
async def med_snooze(callback: types.CallbackQuery):
    _, med_id, name, t_val = callback.data.split("_")
    nag_id = f"nag_{med_id}"
    if scheduler.get_job(nag_id): scheduler.remove_job(nag_id)
    new_time = datetime.now() + timedelta(minutes=15)
    scheduler.add_job(send_reminder, "date", run_date=new_time, args=[callback.message.chat.id, med_id, name, t_val], id=f"snz_{med_id}")
    await callback.message.edit_text(f"⏳ Eslatma 15 daqiqaga surildi ({new_time.strftime('%H:%M')} gacha).")

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
