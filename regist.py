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
user_skips = {}  # Menyimpan jam yang sudah ditandai ✅ oleh user
timezone = pytz.timezone("Asia/Jakarta")

SUBMENUS = [
    "DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG", "MGHK", "AREA",
    "PWNT", "KST", "KINGJR", "VITO", "HOLY", "INDOGG", "DRAGON", "CEME", "IDN", "CITI"
]

TIMES = {
    "pagi": ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang": ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam": ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
}

async def reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    section = data.get("section")
    jam = data.get("jam")

    if chat_id in user_skips and f"{section}_{jam}" in user_skips[chat_id]:
        return  # Jangan kirim jika sudah ditandai ✅

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"\U0001F514 [Reminder] Bagian: {section}, Jam: {jam}"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("Pagi", callback_data="main_pagi"),
            InlineKeyboardButton("Siang", callback_data="main_siang"),
            InlineKeyboardButton("Malam", callback_data="main_malam"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("\U0001F552 Pilih kategori waktu pengingat:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    if data.startswith("main_"):
        waktu = data.split("_")[1]
        tombol_aktifkan = InlineKeyboardButton(f"\U0001F514 Aktifkan {waktu.capitalize()}", callback_data=f"activate_{waktu}")
        tombol_reset = InlineKeyboardButton(f"\U0001F501 Reset {waktu.capitalize()}", callback_data=f"reset_{waktu}")
        keyboard = [[tombol_aktifkan], [tombol_reset]]
        for i in range(0, len(SUBMENUS), 4):
            row = [InlineKeyboardButton(name, callback_data=f"sub_{waktu}_{name}") for name in SUBMENUS[i:i+4]]
            keyboard.append(row)
        await query.edit_message_text(
            text=f"\u231A Kategori: *{waktu.capitalize()}*\nPilih bagian, Aktifkan semua, atau Reset:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("sub_"):
        _, waktu, bagian = data.split("_", maxsplit=2)
        jam_list = TIMES[waktu]
        user_marks = user_skips.get(chat_id, set())
        keyboard = []
        for i in range(0, len(jam_list), 3):
            row = []
            for jam in jam_list[i:i+3]:
                key = f"{bagian}_{jam}"
                status = "✅" if key in user_marks else "❌"
                row.append(InlineKeyboardButton(f"{jam} {status}", callback_data=f"set_{waktu}_{bagian}_{jam}"))
            keyboard.append(row)
        await query.edit_message_text(
            text=f"\U0001F5C2 Bagian: *{bagian}*\nKlik jam untuk toggle (❌ aktif, ✅ tidak diingatkan):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("set_"):
        _, waktu, bagian, jam = data.split("_", maxsplit=3)
        key = f"{bagian}_{jam}"
        user_marks = user_skips.setdefault(chat_id, set())
        if key in user_marks:
            user_marks.remove(key)
        else:
            user_marks.add(key)
        await button_handler(update, context)

    elif data.startswith("activate_"):
        waktu = data.split("_")[1]
        jam_list = TIMES[waktu]
        scheduled_count = 0
        for bagian in SUBMENUS:
            for jam in jam_list:
                if chat_id in user_skips and f"{bagian}_{jam}" in user_skips[chat_id]:
                    continue
                now_local = datetime.datetime.now(timezone)
                jam_int, menit_int = map(int, jam.split(":"))
                target_local = now_local.replace(hour=jam_int, minute=menit_int, second=0, microsecond=0)
                reminder_local = target_local - datetime.timedelta(minutes=5)
                if reminder_local.tzinfo is None:
                    reminder_local = timezone.localize(reminder_local)
                reminder_utc_time = reminder_local.astimezone(pytz.utc).time()
                clean_msg = re.sub(r'\W+', '_', f"{bagian}_{waktu}_{jam}")
                job_name = f"reminder_{chat_id}_{clean_msg}"
                for old_job in context.job_queue.get_jobs_by_name(job_name):
                    try:
                        old_job.schedule_removal()
                    except JobLookupError:
                        pass
                job = context.job_queue.run_daily(
                    reminder,
                    time=reminder_utc_time,
                    name=job_name,
                    data={"chat_id": chat_id, "section": bagian, "jam": jam}
                )
                user_jobs.setdefault(chat_id, []).append(job)
                scheduled_count += 1

        await query.edit_message_text(
            text=(
                f"✅ Reminder *{waktu.capitalize()}* diaktifkan!\n"
                f"Total job: *{scheduled_count}*\n"
                f"\n_✅ artinya tidak akan diingatkan. ❌ artinya akan diingatkan._"
            ),
            parse_mode="Markdown"
        )

    elif data.startswith("reset_"):
        waktu = data.split("_")[1]
        removed = 0
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
        if chat_id in user_jobs:
            user_jobs[chat_id] = [
                job for job in user_jobs[chat_id]
                if not (job.name.startswith(f"reminder_{chat_id}_") and f"_{waktu}_" in job.name)
            ]
        await query.edit_message_text(
            text=(
                f"\U0001F501 Reminder *{waktu.capitalize()}* telah direset.\n"
                f"Total job dibatalkan: *{removed}*"
            ),
            parse_mode="Markdown"
        )

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in user_jobs:
        for job in user_jobs[chat_id]:
            try:
                job.schedule_removal()
            except JobLookupError:
                pass
        user_jobs[chat_id].clear()
    user_skips[chat_id] = set()
    await update.message.reply_text("\U0001F501 Semua reminder telah direset.")

async def handle_root(request):
    return web.Response(text="Bot is running")

async def handle_webhook(request):
    application = request.app["application"]
    update = await request.json()
    from telegram import Update as TgUpdate
    tg_update = TgUpdate.de_json(update, application.bot)
    await application.update_queue.put(tg_update)
    return web.Response()

async def main():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .persistence(persistence)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_all))
    application.add_handler(CallbackQueryHandler(button_handler))

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logging.error("Exception:", exc_info=context.error)
        if isinstance(update, Update) and update.effective_chat:
            try:
                await context.bot.send_message(update.effective_chat.id, text="\u26A0\ufe0f Terjadi kesalahan.")
            except Exception:
                pass

    application.add_error_handler(error_handler)
    await application.initialize()
    await application.start()
    await application.job_queue.start()

    app = web.Application()
    app["application"] = application
    app.add_routes([
        web.get("/", handle_root),
        web.post(WEBHOOK_PATH, handle_webhook),
    ])

    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
    else:
        logging.warning("WEBHOOK_URL_BASE tidak diset, webhook nonaktif!")

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
