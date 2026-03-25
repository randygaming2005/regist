import logging
import datetime
import pytz
import os
import asyncio
import re
import motor.motor_asyncio
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

# Ambil MONGO_URI dari environment variable di Render
MONGO_URI = os.environ.get("MONGO_URI")

# ----------------------
# Helper: MongoDB Sync
# ----------------------
async def download_pickle_from_mongo():
    if not MONGO_URI: 
        logger.warning("MONGO_URI tidak ditemukan, lewati download database.")
        return
    try:
        client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        db = client["regist"] # Nama Database dari gambarmu
        col = db["regist"]    # Nama Collection dari gambarmu
        doc = await col.find_one({"_id": "bot_data_pkl"})
        
        if doc and "file_data" in doc:
            with open("bot_data.pkl", "wb") as f:
                f.write(doc["file_data"])
            logger.info("✅ Berhasil mengunduh bot_data.pkl dari MongoDB Atlas!")
    except Exception as e:
        logger.error(f"Gagal mendownload pickle: {e}")
    finally:
        client.close()

async def upload_pickle_to_mongo():
    if not MONGO_URI: return
    try:
        if os.path.exists("bot_data.pkl"):
            with open("bot_data.pkl", "rb") as f:
                data = f.read()
            client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
            db = client["regist"] # Nama Database
            col = db["regist"]    # Nama Collection
            await col.update_one(
                {"_id": "bot_data_pkl"},
                {"$set": {"file_data": data}},
                upsert=True
            )
            logger.info("✅ Berhasil mem-backup bot_data.pkl ke MongoDB Atlas!")
    except Exception as e:
        logger.error(f"Gagal mengupload pickle: {e}")
    finally:
        client.close()

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
# Helper: Cek Shift & Waktu
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
# Helper: Tampilan Jadwal & Auto-Pin
# ----------------------
async def send_schedule_to_chat(bot, chat_id, chat_data, waktu, page=0, message_id=None):
    chat_data["page"] = page
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    subs = SUBMENUS[start:end]
    thread_id = chat_data.get("thread_id") 

    text = f"📋 *Jadwal Shift {waktu.capitalize()}*\n_Otomatis tercentang saat bukti dikirim._"
    rows = []
    skips = chat_data.get("skips", set())

    for sec in subs:
        first = TIMES[waktu][0]
        sym = "✅" if f"{sec}_{first}" in skips else "❌"
        rows.append([InlineKeyboardButton(f"{sec} {first} {sym}", callback_data="block")])
        
        small = []
        for jam in TIMES[waktu][1:]:
            s2 = "✅" if f"{sec}_{jam}" in skips else "❌"
            small.append(InlineKeyboardButton(f"{jam} {s2}", callback_data="block"))
        
        for i in range(0, len(small), 3):
            rows.append(small[i:i+3])

    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, 
                text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
            )
        except Exception as e:
            error_msg = str(e).lower()
            if "not modified" in error_msg:
                pass 
            else:
                msg = await bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
                )
                chat_data["schedule_msg_id"] = msg.message_id
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
                except:
                    pass
    else:
        old_msg_id = chat_data.get("schedule_msg_id")
        if old_msg_id:
            try:
                await bot.unpin_chat_message(chat_id=chat_id, message_id=old_msg_id)
            except:
                pass

        msg = await bot.send_message(
            chat_id=chat_id, message_thread_id=thread_id, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )
        chat_data["schedule_msg_id"] = msg.message_id
        
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
        except Exception as e:
            logger.error(f"Gagal mem-pin pesan di {chat_id}: {e}")

# ----------------------
# Sistem Otomatis: Master Tick (1 Menit)
# ----------------------
async def master_tick(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(timezone)
    time_str = now.strftime("%H:%M")
    current_shift, _ = get_shift_info(now)
    
    active_groups = context.bot_data.get("active_groups", set())
    if active_groups:
        reset_times = {"pagi": "07:00", "siang": "15:00", "malam": "23:00"}
        if time_str == reset_times[current_shift]:
            for cid in active_groups:
                if cid in context.application.chat_data:
                    context.application.chat_data[cid]["skips"] = set()
                    context.application.chat_data[cid]["history"] = []
                    context.application.chat_data[cid].pop("schedule_msg_id", None) 
            logger.info(f"🔄 Auto-Reset data untuk shift {current_shift} dieksekusi.")

        schedule_times = {"pagi": "07:50", "siang": "15:50", "malam": "23:50"}
        if time_str == schedule_times[current_shift]:
            for cid in active_groups:
                chat_data = context.application.chat_data.setdefault(cid, {})
                thread_id = chat_data.get("thread_id")
                await context.bot.send_message(
                    chat_id=cid, 
                    message_thread_id=thread_id,
                    text=f"🌅 *PERSIAPAN SHIFT {current_shift.upper()}*\n\nSilakan mulai mengirimkan laporan.", 
                    parse_mode="Markdown"
                )
                await send_schedule_to_chat(context.bot, cid, chat_data, current_shift)

        if time_str in TIMES[current_shift]:
            for cid in active_groups:
                chat_data = context.application.chat_data.get(cid, {})
                thread_id = chat_data.get("thread_id")
                await context.bot.send_message(
                    chat_id=cid, message_thread_id=thread_id, text=f"🔔 Waktu pelaporan jadwal *{time_str}* dimulai!", parse_mode="Markdown"
                )
                
        for jam in TIMES[current_shift]:
            target = get_target_datetime(jam, now)
            warning_time = (target + datetime.timedelta(minutes=20)).strftime("%H:%M")
            if time_str == warning_time:
                for cid in active_groups:
                    chat_data = context.application.chat_data.get(cid, {})
                    thread_id = chat_data.get("thread_id")
                    skips = chat_data.get("skips", set())
                    missing = [s for s in SUBMENUS if f"{s}_{jam}" not in skips]
                    if missing:
                        await context.bot.send_message(
                            chat_id=cid, 
                            message_thread_id=thread_id,
                            text=f"⚠️ *PERINGATAN!* 10 Menit menuju batas akhir laporan *{jam}*.\nBelum lapor: {', '.join(missing)}", 
                            parse_mode="Markdown"
                        )

        summary_times = {"pagi": "14:30", "siang": "22:30", "malam": "06:30"}
        if time_str == summary_times[current_shift]:
            for cid in active_groups:
                chat_data = context.application.chat_data.get(cid, {})
                thread_id = chat_data.get("thread_id")
                skips = chat_data.get("skips", set())
                terlewat = []
                for sec in SUBMENUS:
                    for j in TIMES[current_shift]:
                        if f"{sec}_{j}" not in skips: 
                            terlewat.append(f"❌ {sec} - {j}")
                
                if terlewat:
                    msg = f"📊 *RINGKASAN AKHIR SHIFT {current_shift.upper()}*\n\nBerikut jadwal yang *TERLEWAT*:\n" + "\n".join(terlewat)
                else:
                    msg = f"📊 *RINGKASAN AKHIR SHIFT {current_shift.upper()}*\n\n🎉 Sempurna! Semua laporan diselesaikan."
                
                await context.bot.send_message(chat_id=cid, message_thread_id=thread_id, text=msg, parse_mode="Markdown")

    # Pastikan memori lokal tersimpan, lalu tembak ke MongoDB
    await context.application.persistence.flush()
    await upload_pickle_to_mongo()

# ----------------------
# Auto-Check (Laporan Member)
# ----------------------
async def auto_check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return

    chat_id = update.effective_chat.id
    active_groups = context.bot_data.get("active_groups", set())
    if chat_id not in active_groups: return

    chat_data = context.chat_data
    thread_id = message.message_thread_id
    active_thread_id = chat_data.get("thread_id")
    
    if thread_id != active_thread_id: return

    text = message.text or message.caption
    if not text: return
    text_upper = text.upper()

    if "TEST DAFTAR" in text_upper:
        if not message.photo and not message.document:
            msg = await message.reply_text("❌ *Laporan Ditolak!*\nWajib melampirkan foto/screenshot.", parse_mode="Markdown", reply_to_message_id=message.message_id)
            asyncio.create_task(delete_after(msg, 60))
            return

        brand_match = re.search(r'BRAND\s*:\s*([A-Z0-9]+)', text_upper)
        waktu_match = re.search(r'WAKTU\s*:\s*(\d{2}:\d{2})', text_upper)

        if not (brand_match and waktu_match):
            contoh = "❌ *Format Salah!*\nGunakan format:\n\nTEST DAFTAR\nBrand : BG\nWaktu : 09:00"
            msg = await message.reply_text(contoh, parse_mode="Markdown", reply_to_message_id=message.message_id)
            asyncio.create_task(delete_after(msg, 60))
            return

        sec = brand_match.group(1).strip()
        jam = waktu_match.group(1).strip()
        
        if sec not in SUBMENUS: return 

        now = datetime.datetime.now(timezone)
        current_shift, _ = get_shift_info(now)
        
        if jam not in TIMES[current_shift]:
            msg = await message.reply_text(f"❌ Jam `{jam}` tidak ada di jadwal shift *{current_shift}*.", parse_mode="Markdown", reply_to_message_id=message.message_id)
            asyncio.create_task(delete_after(msg, 60))
            return

        target_time = get_target_datetime(jam, now)
        window_start = target_time - datetime.timedelta(minutes=10)
        window_end = target_time + datetime.timedelta(minutes=30)

        if now < window_start:
            await message.reply_text(f"⏳ *Terlalu Cepat!*\nLaporan untuk {jam} baru bisa dikirim mulai {window_start.strftime('%H:%M')}.", reply_to_message_id=message.message_id)
            return
        elif now > window_end:
            await message.reply_text(f"⏰ *Terlambat!*\nBatas laporan {jam} ditutup pukul {window_end.strftime('%H:%M')}.", reply_to_message_id=message.message_id)
            return

        skips = chat_data.setdefault("skips", set())
        history = chat_data.setdefault("history", [])
        
        key = f"{sec}_{jam}"
        if key not in skips:
            skips.add(key)
            history.append(f"✅ {sec} {jam} - {update.effective_user.full_name}")
            if len(history) > 100: history.pop(0)

            await message.reply_text(f"✅ *Laporan Diterima!*\n*{sec}* jam *{jam}* tercentang.", parse_mode="Markdown", reply_to_message_id=message.message_id)
            
            sched_msg_id = chat_data.get("schedule_msg_id")
            await send_schedule_to_chat(context.bot, chat_id, chat_data, current_shift, message_id=sched_msg_id)
        else:
            await message.reply_text(f"⚠️ Jadwal *{sec}* jam *{jam}* sudah tercentang.", parse_mode="Markdown", reply_to_message_id=message.message_id)

async def delete_after(message, seconds):
    await asyncio.sleep(seconds)
    try: await message.delete()
    except: pass

# ----------------------
# Command Handlers
# ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot Jadwal Siap! Gunakan /aktifkan di topic grup ini yang diinginkan.")

async def aktifkan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    thread_id = update.message.message_thread_id
    
    active = context.bot_data.setdefault("active_groups", set())
    active.add(cid)
    context.chat_data["thread_id"] = thread_id
    
    current_shift, _ = get_shift_info(datetime.datetime.now(timezone))
    await update.message.reply_text(f"✅ Bot diaktifkan di topic ini.\nSistem mendeteksi: *SHIFT {current_shift.upper()}*", parse_mode="Markdown")

async def nonaktifkan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    thread_id = update.message.message_thread_id
    active = context.bot_data.get("active_groups", set())
    active_thread_id = context.chat_data.get("thread_id")
    
    if cid in active and thread_id == active_thread_id:
        active.remove(cid)
        context.chat_data.pop("thread_id", None)
        await update.message.reply_text("⛔ *Bot Dinonaktifkan!*\nBot tidak akan memantau topic ini lagi.", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ Bot memang sedang tidak aktif di topic ini.")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    cid = update.effective_chat.id
    thread_id = update.message.message_thread_id
    active_thread_id = context.chat_data.get("thread_id")
    
    is_active = "Aktif ✅" if cid in context.bot_data.get("active_groups", set()) and thread_id == active_thread_id else "Nonaktif ❌"
    
    await update.message.reply_text(f"📡 *STATUS BOT*\nStatus Topic ini: {is_active}\nShift Saat Ini: *{current_shift.upper()}*\nWaktu Server: {now.strftime('%H:%M')}", parse_mode="Markdown")

async def jadwal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    active = context.bot_data.get("active_groups", set())
    if cid not in active:
        await update.message.reply_text("⚠️ *Gagal!* Bot belum diaktifkan.", parse_mode="Markdown")
        return

    thread_id = update.message.message_thread_id
    active_thread_id = context.chat_data.get("thread_id")
    if thread_id != active_thread_id: return

    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    chat_data = context.chat_data
    
    await update.message.reply_text(f"🛠️ *Jadwal dipanggil manual oleh Admin.*", parse_mode="Markdown")
    await send_schedule_to_chat(context.bot, cid, chat_data, current_shift)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("❌ Centang otomatis. Silakan kirim foto bukti sesuai format!", show_alert=True)

# ----------------------
# Startup & Webhook
# ----------------------
async def on_startup(app: ApplicationBuilder):
    app.job_queue.run_repeating(master_tick, interval=60, first=5)
    logger.info("Master Tick Started")

async def handle_webhook(request):
    app = request.app['application']
    await app.update_queue.put(Update.de_json(await request.json(), app.bot))
    return web.Response()

async def main():
    # 1. Download file dari MongoDB sebelum bot jalan
    await download_pickle_from_mongo()
    
    # 2. Inisiasi Bot
    persistence = PicklePersistence(filepath="bot_data.pkl")
    app = ApplicationBuilder().token(token).persistence(persistence).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("aktifkan", aktifkan_cmd))
    app.add_handler(CommandHandler("nonaktifkan", nonaktifkan_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("jadwal", jadwal_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, auto_check_message))

    web_app = web.Application()
    web_app['application'] = app
    web_app.add_routes([
        web.get('/', lambda r: web.Response(text="Bot OK")), 
        web.post(WEBHOOK_PATH, handle_webhook)
    ])

    if WEBHOOK_URL: 
        await app.bot.set_webhook(WEBHOOK_URL)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

    await app.initialize()
    await app.start()
    
    while True: 
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
