import logging
import datetime
import pytz
import os
import re
import asyncio

from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence,
)
from apscheduler.jobstores.base import JobLookupError

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None

persistence = PicklePersistence(filepath="reminder_data.pkl")
user_jobs = {}  # Menyimpan daftar JobQueue per chat_id
timezone = pytz.timezone("Asia/Jakarta")

# -------------------------------------------------
# Daftar 20 submenu (bagian) dan jam per kategori
# -------------------------------------------------
SUBMENUS = [
    "DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG", "MGHK", "AREA",
    "PWNT", "KST", "KINGJR", "VITO", "HOLY", "INDOGG", "DRAGON", "CEME", "IDN", "CITI"
]

TIMES = {
    "pagi": ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang": ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam": ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
}

# -------------------------------------------------
# Fungsi untuk mengirim pesan reminder
# -------------------------------------------------
async def reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    section = data.get("section")
    jam = data.get("jam")
    thread_id = data.get("thread_id")

    await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=f"🔔 [Reminder] Bagian: {section}, Jam: {jam}"
    )

# -------------------------------------------------
# Handler /start: tampilkan 3 tombol utama
# -------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("Pagi", callback_data="main_pagi"),
            InlineKeyboardButton("Siang", callback_data="main_siang"),
            InlineKeyboardButton("Malam", callback_data="main_malam"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🕒 Pilih kategori waktu pengingat:", reply_markup=reply_markup)

# -------------------------------------------------
# CallbackQueryHandler: alur tombol (termasuk “Aktifkan” & “Reset”)
# -------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    # 1) USER MEMILIH “PAGI/SIANG/MALAM” (tombol utama)
    if data.startswith("main_"):
        waktu = data.split("_")[1]  # “pagi” atau “siang” atau “malam”

        # Tombol “Aktifkan” besar di atas
        tombol_aktifkan = InlineKeyboardButton(
            f"🔔 Aktifkan {waktu.capitalize()}",
            callback_data=f"activate_{waktu}"
        )
        # Tombol “Reset” besar di bawah
        tombol_reset = InlineKeyboardButton(
            f"🔄 Reset {waktu.capitalize()}",
            callback_data=f"reset_{waktu}"
        )

        # Tampilkan tombol utama + tombol reset, lalu submenu 20 bagian
        keyboard = [
            [tombol_aktifkan],
            [tombol_reset],
        ]
        for i in range(0, len(SUBMENUS), 4):
            row = [
                InlineKeyboardButton(
                    name, callback_data=f"sub_{waktu}_{name}"
                )
                for name in SUBMENUS[i:i+4]
            ]
            keyboard.append(row)

        await query.edit_message_text(
            text=f"⌚ Kategori: *{waktu.capitalize()}*\nPilih bagian, Aktifkan semua, atau Reset:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # 1b) USER MENEKAN “AKTIFKAN_{waktu}”
    elif data.startswith("activate_"):
        _, waktu = data.split("_", maxsplit=1)
        chat_id = query.message.chat.id
        jam_list = TIMES[waktu]

        scheduled_count = 0
        for bagian in SUBMENUS:
            for jam in jam_list:
                now_local = datetime.datetime.now(timezone)
                jam_int, menit_int = map(int, jam.split(":"))
                target_local = now_local.replace(hour=jam_int, minute=menit_int, second=0, microsecond=0)
                reminder_local = target_local - datetime.timedelta(minutes=5)
                if reminder_local.tzinfo is None:
                    reminder_local = timezone.localize(reminder_local)
                reminder_utc_time = reminder_local.astimezone(pytz.utc).time()

                clean_msg = re.sub(r'\W+', '_', f"{bagian}_{waktu}_{jam}")
                job_name = f"reminder_{chat_id}_{clean_msg}"

                # Hapus job lama jika ada
                for old_job in context.job_queue.get_jobs_by_name(job_name):
                    try:
                        old_job.schedule_removal()
                    except JobLookupError:
                        pass

                # Jadwalkan job baru
                job = context.job_queue.run_daily(
                    reminder,
                    time=reminder_utc_time,
                    name=job_name,
                    data={"chat_id": chat_id, "section": bagian, "jam": jam, "thread_id": None}
                )
                user_jobs.setdefault(chat_id, []).append(job)
                scheduled_count += 1

        await query.edit_message_text(
            text=(
                f"✅ Semua reminder untuk *{waktu.capitalize()}* telah diaktifkan!\n"
                f"• Total job terjadwal: *{scheduled_count}* (20 bagian × {len(jam_list)} jam)\n\n"
                f"_Reminder akan dikirim 5 menit sebelum setiap jam di jadwal {waktu}._"
            ),
            parse_mode="Markdown"
        )

    # 1c) USER MENEKAN “RESET_{waktu}”
    elif data.startswith("reset_"):
        _, waktu = data.split("_", maxsplit=1)
        chat_id = query.message.chat.id
        removed = 0

        # Hapus semua job di kategori waktu ini
        for bagian in SUBMENUS:
            for jam in TIMES[waktu]:
                clean_msg = re.sub(r'\W+', '_', f"{bagian}_{waktu}_{jam}")
                job_name = f"reminder_{chat_id}_{clean_msg}"

                for job in context.job_queue.get_jobs_by_name(job_name):
                    try:
                        job.schedule_removal()
                        removed += 1
                    except JobLookupError:
                        pass

        # Perbarui dict user_jobs agar mencerminkan penghapusan
        if chat_id in user_jobs:
            user_jobs[chat_id] = [
                job for job in user_jobs[chat_id]
                if not (job.name.startswith(f"reminder_{chat_id}_") and f"_{waktu}_" in job.name)
            ]

        await query.edit_message_text(
            text=(
                f"🔄 Semua reminder untuk *{waktu.capitalize()}* telah di-reset!\n"
                f"• Total job dibatalkan: *{removed}*\n\n"
                f"_Anda bisa mengaktifkan ulang kapan saja melalui menu._"
            ),
            parse_mode="Markdown"
        )

    # 2) USER MEMILIH “SUBMENU BAGIAN” (DWT/BG/.../CITI)
    elif data.startswith("sub_"):
        _, waktu, bagian = data.split("_", maxsplit=2)
        jam_list = TIMES[waktu]

        keyboard = []
        for i in range(0, len(jam_list), 3):
            row = [
                InlineKeyboardButton(
                    jam, callback_data=f"set_{waktu}_{bagian}_{jam}"
                )
                for jam in jam_list[i:i+3]
            ]
            keyboard.append(row)

        await query.edit_message_text(
            text=f"🗂️ Bagian: *{bagian}*\nPilih jam pengingat ({waktu.capitalize()}):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # 3) USER MEMILIH “JAM PENGINGAT” untuk satu bagian
    elif data.startswith("set_"):
        _, waktu, bagian, jam = data.split("_", maxsplit=3)
        chat_id = query.message.chat.id

        now_local = datetime.datetime.now(timezone)
        jam_int, menit_int = map(int, jam.split(":"))
        target_local = now_local.replace(hour=jam_int, minute=menit_int, second=0, microsecond=0)
        reminder_local = target_local - datetime.timedelta(minutes=5)
        if reminder_local.tzinfo is None:
            reminder_local = timezone.localize(reminder_local)
        reminder_utc_time = reminder_local.astimezone(pytz.utc).time()

        clean_msg = re.sub(r'\W+', '_', f"{bagian}_{waktu}_{jam}")
        job_name = f"reminder_{chat_id}_{clean_msg}"

        # Hapus job lama jika ada
        for old_job in context.job_queue.get_jobs_by_name(job_name):
            try:
                old_job.schedule_removal()
            except JobLookupError:
                pass

        # Jadwalkan job baru
        job = context.job_queue.run_daily(
            reminder,
            time=reminder_utc_time,
            name=job_name,
            data={"chat_id": chat_id, "section": bagian, "jam": jam, "thread_id": None}
        )
        user_jobs.setdefault(chat_id, []).append(job)

        await query.edit_message_text(
            text=(
                f"✅ Reminder diset!\n"
                f"• Kategori: *{waktu.capitalize()}*\n"
                f"• Bagian: *{bagian}*\n"
                f"• Jam: *{jam}*\n\n"
                f"_Reminder akan dikirim 5 menit sebelum waktu tersebut._"
            ),
            parse_mode="Markdown"
        )

# -------------------------------------------------
# Command /reset (reset semua reminder & data user)
# -------------------------------------------------
async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in user_jobs:
        for job in user_jobs[chat_id]:
            try:
                job.schedule_removal()
            except JobLookupError:
                pass
        user_jobs[chat_id].clear()

    # Bersihkan data di persistence
    if "active_sections" in context.bot_data and chat_id in context.bot_data["active_sections"]:
        context.bot_data["active_sections"][chat_id].clear()
    if "completed_tasks" in context.bot_data and chat_id in context.bot_data["completed_tasks"]:
        context.bot_data["completed_tasks"][chat_id].clear()

    await update.message.reply_text("🔄 Semua reminder telah di-reset.")

# -------------------------------------------------
# Handler root untuk cek bot running
# -------------------------------------------------
async def handle_root(request):
    return web.Response(text="Bot is running")

# -------------------------------------------------
# Handler webhook Telegram
# -------------------------------------------------
async def handle_webhook(request):
    application = request.app["application"]
    update = await request.json()
    from telegram import Update as TgUpdate
    tg_update = TgUpdate.de_json(update, application.bot)
    await application.update_queue.put(tg_update)
    return web.Response()

# -------------------------------------------------
# Fungsi utama: inisialisasi, tambah handler, jalankan webhook
# -------------------------------------------------
async def main():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .persistence(persistence)
        .build()
    )

    # Tambahkan handler perintah dan callback
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_all))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Error handler (opsional)
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logging.error("❗ Exception occurred:", exc_info=context.error)
        if isinstance(update, Update) and update.effective_chat:
            try:
                await context.bot.send_message(
                    update.effective_chat.id, text="⚠️ Terjadi kesalahan. Coba lagi nanti."
                )
            except Exception:
                pass

    application.add_error_handler(error_handler)

    # Mulai bot + job_queue
    await application.initialize()
    await application.start()
    await application.job_queue.start()

    # Siapkan aiohttp untuk webhook
    app = web.Application()
    app["application"] = application
    app.add_routes([
        web.get("/", handle_root),
        web.post(WEBHOOK_PATH, handle_webhook),
    ])

    # Set webhook jika URL tersedia
    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
    else:
        logging.warning(
            "⚠️ WEBHOOK_URL_BASE environment variable tidak diset, webhook tidak aktif!"
        )

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # Loop agar proses tidak langsung berakhir
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
