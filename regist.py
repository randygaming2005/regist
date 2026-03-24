import logging
import datetime
import pytz
import os
import asyncio
import re
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)

# ----------------------
# Logging & Config
# ----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

token = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_PATH = f"/{token}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
PORT = int(os.environ.get("PORT", 8000))
timezone = pytz.timezone(os.environ.get("TZ", "Asia/Jakarta"))

# ----------------------
# Schedule & Constants
# ----------------------
SUBMENUS = ["DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG"]
PAGE_SIZE = 10

TIMES = {
    "pagi":  ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang": ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam": ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"],
}

SHIFTS_ORDER = ["pagi", "malam", "siang"]
EPOCH_DATE = datetime.date(2026, 3, 23)

# ----------------------
# Helpers
# ----------------------
def get_shift_info(now: datetime.datetime):
    if now.hour < 7:
        logical_now = now - datetime.timedelta(days=1)
    else:
        logical_now = now
    logical_date = logical_now.date()
    days_diff = (logical_date - EPOCH_DATE).days
    weeks_passed = days_diff // 7
    current_shift = SHIFTS_ORDER[weeks_passed % 3]
    return current_shift, logical_date

def get_target_datetime(jam_str: str, now: datetime.datetime) -> datetime.datetime:
    target_hour, target_minute = map(int, jam_str.split(':'))
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if now.hour == 23 and target_hour < 7:
        target += datetime.timedelta(days=1)
    elif now.hour < 7 and target_hour >= 23:
        target -= datetime.timedelta(days=1)
    return target

# ----------------------
# Core Functions (Topic Aware)
# ----------------------
async def send_schedule_to_chat(bot, chat_id, chat_data, waktu, page=0, message_id=None, thread_id=None):
    """
    Kirim/Edit Jadwal dengan dukungan message_thread_id (Topic).
    """
    chat_data["page"] = page
    subs = SUBMENUS[page*PAGE_SIZE : (page+1)*PAGE_SIZE]
    text = f"📋 *Jadwal Shift {waktu.capitalize()}*\n_Otomatis tercentang saat bukti dikirim._"
    rows = []
    skips = chat_data.get("skips", set())

    for sec in subs:
        first = TIMES[waktu][0]
        sym = "✅" if f"{sec}_{first}" in skips else "❌"
        rows.append([InlineKeyboardButton(f"{sec} {first} {sym}", callback_data="block")])
        small = [InlineKeyboardButton(f"{j} {'✅' if f'{sec}_{j}' in skips else '❌'}", callback_data="block") for j in TIMES[waktu][1:]]
        for i in range(0, len(small), 3):
            rows.append(small[i:i+3])

    if message_id:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        except Exception as e:
            if "not modified" not in str(e).lower():
                msg = await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
                chat_data["schedule_msg_id"] = msg.message_id
                try: await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
                except: pass
    else:
        # Hapus pin lama
        old_msg_id = chat_data.get("schedule_msg_id")
        if old_msg_id:
            try: await bot.unpin_chat_message(chat_id=chat_id, message_id=old_msg_id)
            except: pass

        # Kirim ke topic spesifik
        msg = await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        chat_data["schedule_msg_id"] = msg.message_id
        try: await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
        except: pass

# ----------------------
# Master Tick (1 Menit)
# ----------------------
async def master_tick(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(timezone)
    time_str = now.strftime("%H:%M")
    current_shift, _ = get_shift_info(now)
    active_groups = context.bot_data.get("active_groups", set())
    
    if not active_groups: return

    # Persiapan / Auto-Reset / Summary
    schedule_times = {"pagi": "07:50", "siang": "15:50", "malam": "23:50"}
    summary_times = {"pagi": "14:30", "siang": "22:30", "malam": "06:30"}

    for cid in active_groups:
        chat_data = context.application.chat_data.setdefault(cid, {})
        thread_id = chat_data.get("target_thread_id") # Topic ID tersimpan

        # Jalankan jadwal otomatis
        if time_str == schedule_times[current_shift]:
            await context.bot.send_message(cid, f"🌅 *PERSIAPAN SHIFT {current_shift.upper()}*", message_thread_id=thread_id, parse_mode="Markdown")
            await send_schedule_to_chat(context.bot, cid, chat_data, current_shift, thread_id=thread_id)

        # Pengingat jam
        if time_str in TIMES[current_shift]:
            await context.bot.send_message(cid, f"🔔 Jam *{time_str}* dimulai!", message_thread_id=thread_id, parse_mode="Markdown")

        # Summary
        if time_str == summary_times[current_shift]:
            skips = chat_data.get("skips", set())
            terlewat = [f"❌ {s}-{j}" for s in SUBMENUS for j in TIMES[current_shift] if f"{s}_{j}" not in skips]
            msg = f"📊 *RINGKASAN SHIFT {current_shift.upper()}*\n\n" + ("🎉 Sempurna!" if not terlewat else "Terlewat:\n" + "\n".join(terlewat))
            await context.bot.send_message(cid, msg, message_thread_id=thread_id, parse_mode="Markdown")

# ----------------------
# Handlers
# ----------------------
async def aktifkan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    # Simpan thread_id (Topic) tempat perintah dikirim
    thread_id = update.effective_message.message_thread_id
    context.chat_data["target_thread_id"] = thread_id
    
    context.bot_data.setdefault("active_groups", set()).add(cid)
    current_shift, _ = get_shift_info(datetime.datetime.now(timezone))
    await update.message.reply_text(f"✅ Bot Aktif di Topic ini!\nShift: *{current_shift.upper()}*", parse_mode="Markdown")

async def jadwal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid not in context.bot_data.get("active_groups", set()): return
    
    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    thread_id = update.effective_message.message_thread_id # Kirim ke topic saat ini
    
    await update.message.reply_text(f"🛠️ *Jadwal dipanggil.*", parse_mode="Markdown")
    await send_schedule_to_chat(context.bot, cid, context.chat_data, current_shift, thread_id=thread_id)

async def auto_check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not (msg.text or msg.caption): return
    text = (msg.text or msg.caption).upper()

    if "TEST DAFTAR" in text:
        # Validasi foto
        if not msg.photo and not msg.document:
            err = await msg.reply_text("❌ Wajib Foto!")
            asyncio.create_task(delete_after(err, 30))
            return

        brand_match = re.search(r'BRAND\s*:\s*([A-Z0-9]+)', text)
        waktu_match = re.search(r'WAKTU\s*:\s*(\d{2}:\d{2})', text)

        if brand_match and waktu_match:
            sec, jam = brand_match.group(1).strip(), waktu_match.group(1).strip()
            if sec not in SUBMENUS: return

            now = datetime.datetime.now(timezone)
            current_shift, _ = get_shift_info(now)
            
            if jam not in TIMES[current_shift]: return

            # Cek jendela waktu
            target = get_target_datetime(jam, now)
            if not (target - datetime.timedelta(minutes=10) <= now <= target + datetime.timedelta(minutes=30)):
                await msg.reply_text(f"⏰ Di luar jendela waktu ({jam})!")
                return

            # Centang
            skips = context.chat_data.setdefault("skips", set())
            key = f"{sec}_{jam}"
            if key not in skips:
                skips.add(key)
                await msg.reply_text(f"✅ {sec} {jam} Diterima!")
                # Update papan yang sedang di-pin (di manapun topicnya)
                await send_schedule_to_chat(context.bot, update.effective_chat.id, context.chat_data, current_shift, message_id=context.chat_data.get("schedule_msg_id"))

async def delete_after(message, seconds):
    await asyncio.sleep(seconds)
    try: await message.delete()
    except: pass

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("❌ Otomatis!", show_alert=True)

# ----------------------
# Main
# ----------------------
async def on_startup(app):
    app.job_queue.run_repeating(master_tick, interval=60, first=5)

async def handle_webhook(request):
    app = request.app['application']
    await app.update_queue.put(Update.de_json(await request.json(), app.bot))
    return web.Response()

async def main():
    persistence = PicklePersistence(filepath="bot_data.pkl")
    app = ApplicationBuilder().token(token).persistence(persistence).post_init(on_startup).build()

    app.add_handler(CommandHandler("aktifkan", aktifkan_cmd))
    app.add_handler(CommandHandler("jadwal", jadwal_cmd))
    app.add_handler(CommandHandler("status", lambda u, c: u.message.reply_text("OK")))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, auto_check_message))

    web_app = web.Application()
    web_app['application'] = app
    web_app.add_routes([web.get('/', lambda r: web.Response(text="Bot OK")), web.post(WEBHOOK_PATH, handle_webhook)])

    if WEBHOOK_URL: await app.bot.set_webhook(WEBHOOK_URL)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

    await app.initialize()
    await app.start()
    while True: await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
