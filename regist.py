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

# Persistence to store reminders and skip flags
default_persistence = PicklePersistence(filepath="reminder_data.pkl")

user_jobs = {}  # Maps chat_id to list of jobs
timezone = pytz.timezone("Asia/Jakarta")

# Submenus and times
SUBMENUS = [
    "DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG", "MGHK", "AREA",
    "PWNT", "KST", "KINGJR", "VITO", "HOLY", "INDOGG", "DRAGON", "CEME", "IDN", "CITI"
]

TIMES = {
    "pagi": ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang": ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam": ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
}

# Reminder job callback
enasync def reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    section = data.get("section")
    jam = data.get("jam")

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔔 [Reminder] Bagian: {section}, Jam: {jam}"
    )

# /start handler: main time categories
enasync def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("Pagi", callback_data="main_pagi"),
            InlineKeyboardButton("Siang", callback_data="main_siang"),
            InlineKeyboardButton("Malam", callback_data="main_malam"),
        ]
    ]
    await update.message.reply_text("🕒 Pilih kategori waktu pengingat:", reply_markup=InlineKeyboardMarkup(keyboard))

# Helper: build sub-menu for a section showing skip flags
def build_time_buttons(waktu, bagian, context, chat_id):
    skip_map = context.user_data.setdefault('skipped', {})
    jam_list = TIMES[waktu]
    keyboard = []
    for i in range(0, len(jam_list), 3):
        row = []
        for jam in jam_list[i:i+3]:
            key = f"{bagian}_{waktu}_{jam}"
            skipped = skip_map.get(key, False)
            mark = "✅" if skipped else "❌"
            row.append(
                InlineKeyboardButton(f"{mark} {jam}", callback_data=f"toggle_{waktu}_{bagian}_{jam}")
            )
        keyboard.append(row)
    return keyboard

# CallbackQuery handler
enasync def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    # 1) Main category
    if data.startswith("main_"):
        waktu = data.split("_", 1)[1]
        tombol_aktifkan = InlineKeyboardButton(
            f"🔔 Aktifkan {waktu.capitalize()}", callback_data=f"activate_{waktu}"
        )
        tombol_reset = InlineKeyboardButton(
            f"🔄 Reset {waktu.capitalize()}", callback_data=f"reset_{waktu}"
        )
        keyboard = [[tombol_aktifkan], [tombol_reset]]
        for i in range(0, len(SUBMENUS), 4):
            row = [InlineKeyboardButton(name, callback_data=f"sub_{waktu}_{name}")
                   for name in SUBMENUS[i:i+4]]
            keyboard.append(row)
        await query.edit_message_text(
            text=f"⌚ Kategori: *{waktu.capitalize()}*\nPilih bagian, Aktifkan semua, atau Reset:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # 2) Activate all reminders in category
    elif data.startswith("activate_"):
        _, waktu = data.split("_", 1)
        scheduled = 0
        skip_map = context.user_data.setdefault('skipped', {})
        for bagian in SUBMENUS:
            for jam in TIMES[waktu]:
                key = f"{bagian}_{waktu}_{jam}"
                if skip_map.get(key):
                    continue  # skip scheduled ones
                now_local = datetime.datetime.now(timezone)
                h, m = map(int, jam.split(':'))
                target = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
                reminder_time = (target - datetime.timedelta(minutes=5))
                reminder_time = timezone.localize(reminder_time) if reminder_time.tzinfo is None else reminder_time
                utc_time = reminder_time.astimezone(pytz.utc).time()

                name = re.sub(r"\W+", "_", key)
                job_name = f"reminder_{chat_id}_{name}"
                for old in context.job_queue.get_jobs_by_name(job_name):
                    old.schedule_removal()
                context.job_queue.run_daily(reminder, time=utc_time, name=job_name,
                                            data={"chat_id": chat_id, "section": bagian, "jam": jam})
                scheduled += 1
        await query.edit_message_text(
            text=(f"✅ Semua reminder untuk *{waktu.capitalize()}* telah diaktifkan!\n"
                  f"• Total job terjadwal: *{scheduled}*\n"
                  f"_Mengirim 5 menit sebelum setiap jadwal._"),
            parse_mode="Markdown"
        )

    # 3) Reset category
    elif data.startswith("reset_"):
        _, waktu = data.split("_", 1)
        removed = 0
        for bagian in SUBMENUS:
            for jam in TIMES[waktu]:
                key = f"{bagian}_{waktu}_{jam}"
                name = re.sub(r"\W+", "_", key)
                job_name = f"reminder_{chat_id}_{name}"
                for job in context.job_queue.get_jobs_by_name(job_name):
                    job.schedule_removal()
                    removed += 1
        await query.edit_message_text(
            text=(f"🔄 Semua reminder untuk *{waktu.capitalize()}* di-reset!\n"
                  f"• Total job dibatalkan: *{removed}*"),
            parse_mode="Markdown"
        )

    # 4) Choose section -> show times with skip toggles
    elif data.startswith("sub_"):
        _, waktu, bagian = data.split("_", 2)
        keyboard = build_time_buttons(waktu, bagian, context, chat_id)
        await query.edit_message_text(
            text=f"🗂️ Bagian: *{bagian}*\nToggle ❌/✅ untuk setiap jam:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # 5) Toggle skip for specific time
    elif data.startswith("toggle_"):
        _, waktu, bagian, jam = data.split("_", 3)
        key = f"{bagian}_{waktu}_{jam}"
        skip_map = context.user_data.setdefault('skipped', {})
        skip_map[key] = not skip_map.get(key, False)
        # refresh buttons
        keyboard = build_time_buttons(waktu, bagian, context, chat_id)
        await query.edit_message_text(
            text=f"🗂️ Bagian: *{bagian}*\nToggle ❌/✅ untuk setiap jam:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# /reset command handler
async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # remove jobs
    for jobs in list(user_jobs.get(chat_id, [])):
        try:
            jobs.schedule_removal()
        except JobLookupError:
            pass
    user_jobs[chat_id] = []
    # clear skip flags
    context.user_data['skipped'] = {}
    await update.message.reply_text("🔄 Semua reminder dan penanda telah di-reset.")

# Webhook handlers
enasync def handle_root(request):
    return web.Response(text="Bot is running")

enasync def handle_webhook(request):
    app = request.app['application']
    update = await request.json()
    tg_update = Update.de_json(update, app.bot)
    await app.update_queue.put(tg_update)
    return web.Response()

# Main function
enasync def main():
    application = ApplicationBuilder().token(TOKEN).persistence(default_persistence).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_all))
    application.add_handler(CallbackQueryHandler(button_handler))

    async def error_handler(update, context):
        logger.error("Exception: %s", context.error)
        if hasattr(update, 'effective_chat'):
            await context.bot.send_message(update.effective_chat.id, "⚠️ Terjadi kesalahan.")
    application.add_error_handler(error_handler)

    await application.initialize()
    await application.start()
    await application.job_queue.start()

    app = web.Application()
    app['application'] = application
    app.add_routes([web.get("/", handle_root), web.post(WEBHOOK_PATH, handle_webhook)])
    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
