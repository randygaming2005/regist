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

# Urutan Rotasi Mingguan (Sesuai tabel Excel: Pagi -> Malam -> Siang)
SHIFTS_ORDER = ["pagi", "malam", "siang"]

# Tanggal Acuan (Senin, 23 Maret 2026 adalah Shift Pagi)
EPOCH_DATE = datetime.date(2026, 3, 23)

# ----------------------
# Helper: Cek Shift & Waktu
# ----------------------
def get_shift_info(now: datetime.datetime):
    """
    Menghitung shift apa minggu ini dengan ambang batas pukul 07:00 pagi.
    Ini memastikan shift Malam (23:00 - 07:00) tidak terpotong oleh pergantian hari kalender.
    """
    if now.hour < 7:
        logical_now = now - datetime.timedelta(days=1)
    else:
        logical_now = now
        
    logical_date = logical_now.date()
    
    # Hitung selisih minggu dari EPOCH_DATE
    days_diff = (logical_date - EPOCH_DATE).days
    weeks_passed = days_diff // 7
    
    current_shift = SHIFTS_ORDER[weeks_passed % 3]
    return current_shift, logical_date

def get_target_datetime(jam_str: str, now: datetime.datetime) -> datetime.datetime:
    """Mengubah format jam (misal 00:00) menjadi objek datetime yang valid."""
    target_hour, target_minute = map(int, jam_str.split(':'))
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    
    # Penyesuaian khusus: Jika lapor jam 23:5x untuk jadwal 00:00 shift malam (lintas hari ke depan)
    if now.hour == 23 and target_hour < 7:
        target += datetime.timedelta(days=1)
    # Penyesuaian khusus: Jika lapor jam 00:0x untuk jadwal 23:xx (lintas hari ke belakang)
    elif now.hour < 7 and target_hour >= 23:
        target -= datetime.timedelta(days=1)
        
    return target

# ----------------------
# Helper: Tampilan Jadwal (Keyboard)
# ----------------------
async def send_schedule_to_chat(bot, chat_id, chat_data, waktu, page=0, message_id=None):
    chat_data["page"] = page
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    subs = SUBMENUS[start:end]

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
        except:
            pass
    else:
        await bot.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )

# ----------------------
# Sistem Otomatis: Master Tick (1 Menit)
# ----------------------
async def master_tick(context: ContextTypes.DEFAULT_TYPE):
    """Fungsi ini berdetak setiap 1 menit. Mengatur Reset, Jadwal, Peringatan, & Ringkasan."""
    now = datetime.datetime.now(timezone)
    time_str = now.strftime("%H:%M")
    current_shift, _ = get_shift_info(now)
    
    active_groups = context.bot_data.get("active_groups", set())
    if not active_groups:
        return

    # 1. AUTO-RESET (Tepat saat shift masuk: 07:00, 15:00, 23:00)
    reset_times = {"pagi": "07:00", "siang": "15:00", "malam": "23:00"}
    if time_str == reset_times[current_shift]:
        for cid in active_groups:
            if cid in context.application.chat_data:
                context.application.chat_data[cid]["skips"] = set()
                context.application.chat_data[cid]["history"] = []
        logger.info(f"🔄 Auto-Reset data untuk shift {current_shift} dieksekusi.")

    # 2. KIRIM JADWAL AWAL (10 Menit sebelum jam lapor pertama)
    schedule_times = {"pagi": "07:50", "siang": "15:50", "malam": "23:50"}
    if time_str == schedule_times[current_shift]:
        for cid in active_groups:
            chat_data = context.application.chat_data.setdefault(cid, {})
            await context.bot.send_message(
                cid, 
                f"🌅 *PERSIAPAN SHIFT {current_shift.upper()}*\n\nSilakan mulai mengirimkan laporan. Batas waktu setiap laporan adalah 30 menit dari jadwal.", 
                parse_mode="Markdown"
            )
            await send_schedule_to_chat(context.bot, cid, chat_data, current_shift)

    # 3. PENGINGAT SETIAP JAM & PERINGATAN (Menit ke-00 dan ke-20)
    if time_str in TIMES[current_shift]:
        for cid in active_groups:
            await context.bot.send_message(
                cid, f"🔔 Waktu pelaporan jadwal *{time_str}* dimulai!", parse_mode="Markdown"
            )
            
    # Peringatan 10 menit menuju penutupan (XX:20)
    for jam in TIMES[current_shift]:
        target = get_target_datetime(jam, now)
        warning_time = (target + datetime.timedelta(minutes=20)).strftime("%H:%M")
        if time_str == warning_time:
            for cid in active_groups:
                skips = context.application.chat_data.get(cid, {}).get("skips", set())
                missing = [s for s in SUBMENUS if f"{s}_{jam}" not in skips]
                if missing:
                    await context.bot.send_message(
                        cid, 
                        f"⚠️ *PERINGATAN!* 10 Menit menuju batas akhir laporan *{jam}*.\nBelum lapor: {', '.join(missing)}", 
                        parse_mode="Markdown"
                    )

    # 4. RINGKASAN AKHIR SHIFT (30 Menit setelah jam lapor terakhir)
    summary_times = {"pagi": "14:30", "siang": "22:30", "malam": "06:30"}
    if time_str == summary_times[current_shift]:
        for cid in active_groups:
            skips = context.application.chat_data.get(cid, {}).get("skips", set())
            terlewat = []
            for sec in SUBMENUS:
                for j in TIMES[current_shift]:
                    if f"{sec}_{j}" not in skips: 
                        terlewat.append(f"❌ {sec} - {j}")
            
            if terlewat:
                msg = f"📊 *RINGKASAN AKHIR SHIFT {current_shift.upper()}*\n\nBerikut jadwal yang *TERLEWAT*:\n" + "\n".join(terlewat)
            else:
                msg = f"📊 *RINGKASAN AKHIR SHIFT {current_shift.upper()}*\n\n🎉 Sempurna! Semua laporan diselesaikan."
            
            await context.bot.send_message(cid, msg, parse_mode="Markdown")

# ----------------------
# Auto-Check (Laporan Member)
# ----------------------
async def auto_check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: 
        return
    text = message.text or message.caption
    if not text: 
        return
    text_upper = text.upper()

    if "TEST DAFTAR" in text_upper:
        # Wajib melampirkan foto
        if not message.photo and not message.document:
            msg = await message.reply_text(
                "❌ *Laporan Ditolak!*\nWajib melampirkan foto/screenshot.", 
                parse_mode="Markdown", reply_to_message_id=message.message_id
            )
            asyncio.create_task(delete_after(msg, 60))
            return

        # Ekstrak Brand dan Waktu
        brand_match = re.search(r'BRAND\s*:\s*([A-Z0-9]+)', text_upper)
        waktu_match = re.search(r'WAKTU\s*:\s*(\d{2}:\d{2})', text_upper)

        if not (brand_match and waktu_match):
            contoh = "❌ *Format Salah!*\nGunakan format:\n\nTEST DAFTAR\nBrand : BG\nWaktu : 09:00"
            msg = await message.reply_text(contoh, parse_mode="Markdown", reply_to_message_id=message.message_id)
            asyncio.create_task(delete_after(msg, 60))
            return

        sec = brand_match.group(1).strip()
        jam = waktu_match.group(1).strip()
        
        # Abaikan diam-diam jika brand tidak ada di daftar
        if sec not in SUBMENUS: 
            return 

        now = datetime.datetime.now(timezone)
        current_shift, _ = get_shift_info(now)
        
        if jam not in TIMES[current_shift]:
            msg = await message.reply_text(
                f"❌ Jam `{jam}` tidak ada di jadwal shift *{current_shift}* yang sedang aktif.", 
                parse_mode="Markdown", reply_to_message_id=message.message_id
            )
            asyncio.create_task(delete_after(msg, 60))
            return

        # LOGIKA RENTANG WAKTU (10 Menit Sebelum s/d 30 Menit Sesudah)
        target_time = get_target_datetime(jam, now)
        window_start = target_time - datetime.timedelta(minutes=10)
        window_end = target_time + datetime.timedelta(minutes=30)

        if now < window_start:
            await message.reply_text(
                f"⏳ *Terlalu Cepat!*\nLaporan untuk jadwal {jam} baru bisa dikirim mulai pukul {window_start.strftime('%H:%M')}.", 
                reply_to_message_id=message.message_id
            )
            return
        elif now > window_end:
            await message.reply_text(
                f"⏰ *Terlambat!*\nBatas laporan jadwal {jam} ditutup pukul {window_end.strftime('%H:%M')}.", 
                reply_to_message_id=message.message_id
            )
            return

        # EKSEKUSI CENTANG (Memori Permanen)
        chat_id = update.effective_chat.id
        chat_data = context.chat_data
        skips = chat_data.setdefault("skips", set())
        history = chat_data.setdefault("history", [])
        
        key = f"{sec}_{jam}"
        if key not in skips:
            skips.add(key)
            history.append(f"✅ {sec} {jam} - {update.effective_user.full_name}")
            if len(history) > 100: 
                history.pop(0)

            await message.reply_text(
                f"✅ *Laporan Diterima!*\n*{sec}* jam *{jam}* tercentang.", 
                parse_mode="Markdown", reply_to_message_id=message.message_id
            )
            
            # Segarkan tampilan jadwal secara otomatis
            await send_schedule_to_chat(context.bot, chat_id, chat_data, current_shift)
        else:
            await message.reply_text(
                f"⚠️ Jadwal *{sec}* jam *{jam}* sudah tercentang sebelumnya.", 
                parse_mode="Markdown", reply_to_message_id=message.message_id
            )

async def delete_after(message, seconds):
    """Menghapus pesan error dari bot secara otomatis."""
    await asyncio.sleep(seconds)
    try: 
        await message.delete()
    except: 
        pass

# ----------------------
# Command Handlers
# ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot Jadwal Siap! Gunakan /aktifkan di grup ini.")

async def aktifkan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    active = context.bot_data.setdefault("active_groups", set())
    active.add(cid)
    
    current_shift, logical_date = get_shift_info(datetime.datetime.now(timezone))
    await update.message.reply_text(
        f"✅ Bot diaktifkan di grup ini.\nSistem mendeteksi kalender kerja ini adalah: *SHIFT {current_shift.upper()}*", 
        parse_mode="Markdown"
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(timezone)
    current_shift, logical_date = get_shift_info(now)
    cid = update.effective_chat.id
    is_active = "Aktif ✅" if cid in context.bot_data.get("active_groups", set()) else "Nonaktif ❌"
    
    await update.message.reply_text(
        f"📡 *STATUS BOT*\nStatus Grup: {is_active}\nShift Saat Ini: *{current_shift.upper()}*\nWaktu Server: {now.strftime('%H:%M')}", 
        parse_mode="Markdown"
    )

async def jadwal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Panggil paksa papan jadwal jika server baru direstart di tengah jam kerja."""
    cid = update.effective_chat.id
    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    
    chat_data = context.application.chat_data.setdefault(cid, {})
    await update.message.reply_text(f"🛠️ *Jadwal dipanggil manual oleh Admin.*", parse_mode="Markdown")
    await send_schedule_to_chat(context.bot, cid, chat_data, current_shift)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Blokir interaksi tombol manual member."""
    q = update.callback_query
    await q.answer("❌ Centang otomatis. Silakan kirim foto bukti sesuai format!", show_alert=True)

# ----------------------
# Startup & Webhook
# ----------------------
async def on_startup(app: ApplicationBuilder):
    # Daftarkan Master Tick setiap 60 detik
    app.job_queue.run_repeating(master_tick, interval=60, first=5)
    logger.info("Master Tick Started")

async def handle_webhook(request):
    app = request.app['application']
    await app.update_queue.put(Update.de_json(await request.json(), app.bot))
    return web.Response()

async def main():
    app = ApplicationBuilder().token(token).persistence(persistence).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("aktifkan", aktifkan_cmd))
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
    persistence = PicklePersistence(filepath="bot_data.pkl")
    asyncio.run(main())
