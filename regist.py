import logging
import datetime
import pytz
import os
import asyncio
import re
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
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

RESET_TIMES =   {"pagi": "07:00", "siang": "15:00", "malam": "23:00"}
PREP_TIMES =    {"pagi": "07:50", "siang": "15:50", "malam": "23:50"}
SUMMARY_TIMES = {"pagi": "14:30", "siang": "22:30", "malam": "06:30"}

SHIFTS_ORDER = ["pagi", "malam", "siang"]
EPOCH_DATE = datetime.date(2026, 3, 23)

# ----------------------
# Helper Functions
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
# Tampilan Jadwal
# ----------------------
async def send_schedule_to_chat(bot, chat_id, chat_data, waktu, message_id=None):
    thread_id = chat_data.get("thread_id") 
    text = f"📋 *Jadwal Shift {waktu.capitalize()}*\n_Otomatis tercentang saat bukti dikirim._"
    rows = []
    skips = chat_data.get("skips", set())

    for sec in SUBMENUS:
        first = TIMES[waktu][0]
        sym = "✅" if f"{sec}_{first}" in skips else "❌"
        # Tombol dengan data spesifik untuk Bypass
        rows.append([InlineKeyboardButton(f"{sec} {first} {sym}", callback_data=f"toggle_{sec}_{first}")])
        
        small = []
        for jam in TIMES[waktu][1:]:
            s2 = "✅" if f"{sec}_{jam}" in skips else "❌"
            # Tombol dengan data spesifik untuk Bypass
            small.append(InlineKeyboardButton(f"{jam} {s2}", callback_data=f"toggle_{sec}_{jam}"))
        
        for i in range(0, len(small), 3):
            rows.append(small[i:i+3])

    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, 
                text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
            )
            return
        except Exception as e:
            if "not modified" not in str(e).lower():
                pass

    try:
        msg = await bot.send_message(
            chat_id=chat_id, message_thread_id=thread_id, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )
        chat_data["schedule_msg_id"] = msg.message_id
    except Exception as e:
        logger.error(f"Gagal kirim pesan jadwal: {e}")

# ----------------------
# Sistem Otomatis (JobQueue) - Teringan untuk Render
# ----------------------
async def job_reset(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["chat_id"]
    shift = context.job.data["shift"]
    chat_data = context.application.chat_data.setdefault(cid, {})
    chat_data["skips"] = set()
    chat_data["history"] = []
    chat_data.pop("schedule_msg_id", None) 
    logger.info(f"🔄 Auto-Reset shift {shift} grup {cid}.")

async def job_persiapan(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    try:
        await context.bot.send_message(
            chat_id=d["chat_id"], message_thread_id=d["thread_id"],
            text=f"🌅 *PERSIAPAN SHIFT {d['shift'].upper()}*\n\nSilakan mulai mengirimkan laporan.", 
            parse_mode="Markdown"
        )
        chat_data = context.application.chat_data.setdefault(d["chat_id"], {})
        await send_schedule_to_chat(context.bot, d["chat_id"], chat_data, d["shift"])
    except Exception as e:
        logger.error(f"Error persiapan: {e}")

async def job_mulai(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    try:
        await context.bot.send_message(
            chat_id=d["chat_id"], message_thread_id=d["thread_id"], 
            text=f"🔔 Waktu pelaporan jadwal *{d['jam']}* dimulai!", parse_mode="Markdown"
        )
    except Exception:
        pass

async def job_peringatan(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    chat_data = context.application.chat_data.get(d["chat_id"], {})
    skips = chat_data.get("skips", set())
    missing = [s for s in SUBMENUS if f"{s}_{d['jam']}" not in skips]
    
    if missing:
        try:
            await context.bot.send_message(
                chat_id=d["chat_id"], message_thread_id=d["thread_id"],
                text=f"⚠️ *PERINGATAN!* 10 Menit menuju batas akhir laporan *{d['jam']}*.\nBelum lapor: {', '.join(missing)}", 
                parse_mode="Markdown"
            )
        except Exception:
            pass

async def job_rekap(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    chat_data = context.application.chat_data.get(d["chat_id"], {})
    skips = chat_data.get("skips", set())
    terlewat = []
    
    for sec in SUBMENUS:
        for j in TIMES[d['shift']]:
            if f"{sec}_{j}" not in skips: 
                terlewat.append(f"❌ {sec} - {j}")
                
    admin_tags = "@cartenz88 @Agha1104 @Gemini_Squad"
    if terlewat:
        msg = (f"📊 <b>RINGKASAN AKHIR SHIFT {d['shift'].upper()}</b>\n\n"
               f"Terdapat jadwal laporan yang <b>TERLEWAT</b>:\n"
               f"{chr(10).join(terlewat)}\n\n"
               f"Halo {admin_tags}, mohon bantuannya untuk menindaklanjuti. 🙏")
    else:
        msg = (f"📊 <b>RINGKASAN AKHIR SHIFT {d['shift'].upper()}</b>\n\n"
               f"Laporan hari ini <b>SEMPURNA!</b> 🎉 Seluruh jadwal telah dilaporkan.\n\n"
               f"Halo {admin_tags}, operasional berjalan lancar tanpa kendala. 🙏")
               
    try:
        await context.bot.send_message(chat_id=d["chat_id"], message_thread_id=d["thread_id"], text=msg, parse_mode="HTML")
    except Exception:
        pass

async def job_rotator(context: ContextTypes.DEFAULT_TYPE):
    """Mengecek dan memperbarui jadwal setiap pagi sebelum shift baru dimulai."""
    d = context.job.data
    chat_id = d["chat_id"]
    thread_id = d["thread_id"]
    
    schedule_group_jobs(context.job_queue, chat_id, thread_id)
    logger.info(f"🔄 Rotasi shift harian diperbarui untuk grup {chat_id}.")

def schedule_group_jobs(job_queue, chat_id, thread_id):
    # Hapus job lama kecuali rotator
    for job in job_queue.jobs():
        if job.name and job.name.endswith(f"_{chat_id}") and not job.name.startswith("rotator_"):
            job.schedule_removal()

    # Cek shift saat ini
    now = datetime.datetime.now(timezone)
    check_time = now + datetime.timedelta(hours=1) if (now.hour == 6 and now.minute >= 45) else now
    current_shift, _ = get_shift_info(check_time)
    
    hours = TIMES[current_shift]

    # Hanya jadwalkan shift yang aktif
    rh, rm = map(int, RESET_TIMES[current_shift].split(':'))
    job_queue.run_daily(job_reset, time=datetime.time(hour=rh, minute=rm, tzinfo=timezone), name=f"reset_{current_shift}_{chat_id}", data={"chat_id": chat_id, "shift": current_shift})

    ph, pm = map(int, PREP_TIMES[current_shift].split(':'))
    job_queue.run_daily(job_persiapan, time=datetime.time(hour=ph, minute=pm, tzinfo=timezone), name=f"prep_{current_shift}_{chat_id}", data={"chat_id": chat_id, "thread_id": thread_id, "shift": current_shift})

    sh, sm = map(int, SUMMARY_TIMES[current_shift].split(':'))
    job_queue.run_daily(job_rekap, time=datetime.time(hour=sh, minute=sm, tzinfo=timezone), name=f"rekap_{current_shift}_{chat_id}", data={"chat_id": chat_id, "thread_id": thread_id, "shift": current_shift})

    for jam in hours:
        h, m = map(int, jam.split(':'))
        job_queue.run_daily(job_mulai, time=datetime.time(hour=h, minute=m, tzinfo=timezone), name=f"start_{jam}_{chat_id}", data={"chat_id": chat_id, "thread_id": thread_id, "jam": jam})
        
        warn_m = m + 20
        warn_h = h
        if warn_m >= 60:
            warn_h = (warn_h + 1) % 24
            warn_m -= 60
        job_queue.run_daily(job_peringatan, time=datetime.time(hour=warn_h, minute=warn_m, tzinfo=timezone), name=f"warn_{jam}_{chat_id}", data={"chat_id": chat_id, "thread_id": thread_id, "jam": jam, "shift": current_shift})

    # Pastikan rotator berjalan setiap pagi jam 06:50 untuk cek pergantian minggu
    rotator_name = f"rotator_{chat_id}"
    if not any(j.name == rotator_name for j in job_queue.jobs()):
        job_queue.run_daily(
            job_rotator, 
            time=datetime.time(hour=6, minute=50, tzinfo=timezone), 
            name=rotator_name, 
            data={"chat_id": chat_id, "thread_id": thread_id}
        )

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
    if thread_id != chat_data.get("thread_id"): return

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
            msg = await message.reply_text(f"⏳ *Terlalu Cepat!*\nLaporan untuk {jam} baru bisa dikirim mulai {window_start.strftime('%H:%M')}.", reply_to_message_id=message.message_id)
            asyncio.create_task(delete_after(msg, 60))
            return
        elif now > window_end:
            msg = await message.reply_text(f"⏰ *Terlambat!*\nBatas laporan {jam} ditutup pukul {window_end.strftime('%H:%M')}.", reply_to_message_id=message.message_id)
            asyncio.create_task(delete_after(msg, 60))
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
    await update.message.reply_text("👋 Bot Jadwal Siap! Gunakan /aktifkan di topic yang diinginkan.")

async def aktifkan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    thread_id = update.message.message_thread_id
    
    active = context.bot_data.setdefault("active_groups", set())
    active.add(cid)
    context.bot_data["active_groups"] = active
    context.chat_data["thread_id"] = thread_id
    
    schedule_group_jobs(context.job_queue, cid, thread_id)
    
    current_shift, _ = get_shift_info(datetime.datetime.now(timezone))
    await update.message.reply_text(f"✅ Sistem Pengingat & Rekap *DIAKTIFKAN* di topic ini.\nShift Saat Ini: *{current_shift.upper()}*", parse_mode="Markdown")
    
    if "schedule_msg_id" not in context.chat_data:
        await send_schedule_to_chat(context.bot, cid, context.chat_data, current_shift)

async def nonaktifkan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    active = context.bot_data.get("active_groups", set())
    if cid in active:
        active.remove(cid)
        context.bot_data["active_groups"] = active
        for job in context.job_queue.jobs():
            if job.name and job.name.endswith(f"_{cid}"):
                job.schedule_removal()
        await update.message.reply_text("⛔ *Bot Dinonaktifkan!*\nSemua pengingat di topic ini dihentikan.", parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    is_active = "Aktif ✅" if cid in context.bot_data.get("active_groups", set()) else "Nonaktif ❌"
    await update.message.reply_text(f"📡 *STATUS BOT*\nStatus Topic ini: {is_active}\nShift: *{current_shift.upper()}*\nWaktu Server: {now.strftime('%H:%M')}", parse_mode="Markdown")

async def jadwal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    active = context.bot_data.get("active_groups", set())
    if cid not in active:
        await update.message.reply_text("⚠️ *Gagal!* Bot belum diaktifkan.", parse_mode="Markdown")
        return

    thread_id = update.message.message_thread_id
    if thread_id != context.chat_data.get("thread_id"): return

    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    
    await update.message.reply_text(f"🛠️ *Jadwal dipanggil manual oleh Admin.*", parse_mode="Markdown")
    await send_schedule_to_chat(context.bot, cid, context.chat_data, current_shift)

async def rekap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    active = context.bot_data.get("active_groups", set())
    if cid not in active:
        await update.message.reply_text("⚠️ *Gagal!* Bot belum diaktifkan.", parse_mode="Markdown")
        return

    thread_id = update.message.message_thread_id
    if thread_id != context.chat_data.get("thread_id"): return

    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    skips = context.chat_data.get("skips", set())
    
    terlewat = []
    for sec in SUBMENUS:
        for j in TIMES[current_shift]:
            if f"{sec}_{j}" not in skips: 
                terlewat.append(f"❌ {sec} - {j}")
                
    if terlewat:
        msg = f"📊 *CEK REKAP SHIFT {current_shift.upper()}*\nBerikut jadwal yang BELUM/TERLEWAT:\n{chr(10).join(terlewat)}"
    else:
        msg = f"📊 *CEK REKAP SHIFT {current_shift.upper()}*\nSejauh ini laporan SEMPURNA! 🎉"
        
    await update.message.reply_text(msg, parse_mode="Markdown")

# ----------------------
# Tombol Callback (Akses Dewa) - SUDAH DIPERBAIKI
# ----------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    
    # Cek apakah user adalah owner (case-insensitive)
    if not user.username or user.username.lower() != "intan_payungggg":
        await query.answer("❌ Centang otomatis. Silakan kirim foto bukti!", show_alert=True)
        return

    data = query.data
    # Pastikan data yang diklik sesuai format "toggle_BRAND_JAM"
    if data.startswith("toggle_"):
        _, sec, jam = data.split("_")
        chat_id = query.message.chat.id
        
        # Perbaikan: Langsung panggil context.chat_data dari grup saat ini
        chat_data = context.chat_data 
        skips = chat_data.setdefault("skips", set())
        
        # Logika Toggle (Klik sekali centang, klik lagi batal centang)
        key = f"{sec}_{jam}"
        if key in skips:
            skips.remove(key)
            status_msg = f"❌ Dibatalkan: {sec} {jam}"
        else:
            skips.add(key)
            status_msg = f"✅ Dicentang manual: {sec} {jam}"
            
        # Tampilkan pop-up kecil ke Intan_Payungggg
        await query.answer(status_msg)
        
        # Render ulang papan jadwal dengan centang terbaru
        current_shift, _ = get_shift_info(datetime.datetime.now(timezone))
        await send_schedule_to_chat(context.bot, chat_id, chat_data, current_shift, message_id=query.message.message_id)
    else:
        await query.answer("❌ Aksi tidak valid.", show_alert=True)

# ----------------------
# Startup & Webhook
# ----------------------
async def on_startup(app: Application):
    active_groups = app.bot_data.get("active_groups", set())
    for cid in active_groups:
        thread_id = app.chat_data.get(cid, {}).get("thread_id")
        schedule_group_jobs(app.job_queue, cid, thread_id)
    logger.info(f"✅ Berhasil memuat ulang jadwal untuk {len(active_groups)} grup aktif.")

async def handle_root(request):
    return web.Response(text="Bot is running smoothly")

async def handle_webhook(request):
    app = request.app['application']
    await app.update_queue.put(Update.de_json(await request.json(), app.bot))
    return web.Response()

async def main():
    persistence = PicklePersistence(filepath="bot_jadwal_data.pickle")
    app = ApplicationBuilder().token(token).persistence(persistence).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("aktifkan", aktifkan_cmd))
    app.add_handler(CommandHandler("nonaktifkan", nonaktifkan_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("jadwal", jadwal_cmd))
    app.add_handler(CommandHandler("rekap", rekap_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, auto_check_message))

    web_app = web.Application()
    web_app['application'] = app
    web_app.add_routes([web.get('/', handle_root), web.post(WEBHOOK_PATH, handle_webhook)])

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
