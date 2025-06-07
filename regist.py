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
import telegram.error

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
user_jobs = {}    # chat_id → list of Job instances
user_skips = {}   # chat_id → set of "<section>_<jam>" yang ditandai ✅
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

    # skip kalau user sudah centang
    if chat_id in user_skips and f"{section}_{jam}" in user_skips[chat_id]:
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔔 [Reminder] Bagian: {section}, Jam: {jam}"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        InlineKeyboardButton("Pagi", callback_data="main_pagi"),
        InlineKeyboardButton("Siang", callback_data="main_siang"),
        InlineKeyboardButton("Malam", callback_data="main_malam"),
    ]]
    await update.message.reply_text(
        "🕒 Pilih kategori waktu pengingat:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # jawab callback sesegera mungkin
    try:
        await query.answer()
    except telegram.error.BadRequest as e:
        # ignore kalau sudah kadaluarsa
        if "too old" in str(e) or "invalid" in str(e):
            logger.warning("CallbackQuery timeout/invalid: %s", e)
        else:
            raise

    data = query.data
    chat_id = query.message.chat.id

    # 1) Menu utama
    if data.startswith("main_"):
        waktu = data.split("_", 1)[1]
        btn_on = InlineKeyboardButton(f"🔔 Aktifkan {waktu.capitalize()}", callback_data=f"activate_{waktu}")
        btn_reset = InlineKeyboardButton(f"🔁 Reset {waktu.capitalize()}",    callback_data=f"reset_{waktu}")
        keyboard = [[btn_on], [btn_reset]]
        for i in range(0, len(SUBMENUS), 4):
            row = [InlineKeyboardButton(name, callback_data=f"sub_{waktu}_{name}")
                   for name in SUBMENUS[i:i+4]]
            keyboard.append(row)
        await query.edit_message_text(
            text=(
                f"⏰ Kategori: *{waktu.capitalize()}*\n"
                "Pilih bagian, Aktifkan semua, atau Reset:"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # 2) Submenu bagian
    if data.startswith("sub_"):
        _, waktu, bagian = data.split("_", 2)
        marks = user_skips.get(chat_id, set())
        keyboard = []
        for i in range(0, len(TIMES[waktu]), 3):
            row = []
            for jam in TIMES[waktu][i:i+3]:
                key = f"{bagian}_{jam}"
                sym = "✅" if key in marks else "❌"
                row.append(
                    InlineKeyboardButton(
                        f"{jam} {sym}",
                        callback_data=f"toggle_{waktu}_{bagian}_{jam}"
                    )
                )
            keyboard.append(row)
        await query.edit_message_text(
            text=f"🗂 Bagian: *{bagian}*\nKlik untuk toggle ❌/✅:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # 3) Toggle skip flag
    if data.startswith("toggle_"):
        _, waktu, bagian, jam = data.split("_", 3)
        key = f"{bagian}_{jam}"
        marks = user_skips.setdefault(chat_id, set())
        if key in marks:
            marks.remove(key)
        else:
            marks.add(key)

        # rebuild keyboard untuk submenu yang sama
        new_rows = []
        for i in range(0, len(TIMES[waktu]), 3):
            row = []
            for jm in TIMES[waktu][i:i+3]:
                k2 = f"{bagian}_{jm}"
                sym2 = "✅" if k2 in marks else "❌"
                row.append(
                    InlineKeyboardButton(f"{jm} {sym2}", callback_data=f"toggle_{waktu}_{bagian}_{jm}")
                )
            new_rows.append(row)

        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(new_rows)
        )
        return

    # 4) Activate all
    if data.startswith("activate_"):
        waktu = data.split("_", 1)[1]
        scheduled = 0
        for bagian in SUBMENUS:
            for jam in TIMES[waktu]:
                if chat_id in user_skips and f"{bagian}_{jam}" in user_skips[chat_id]:
                    continue
                now = datetime.datetime.now(timezone)
                h, m = map(int, jam.split(":"))
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                remind_at = target - datetime.timedelta(minutes=5)
                if remind_at.tzinfo is None:
                    remind_at = timezone.localize(remind_at)
                utc_time = remind_at.astimezone(pytz.utc).time()
                name = re.sub(r"\W+", "_", f"{bagian}_{waktu}_{jam}")
                job_name = f"reminder_{chat_id}_{name}"
                # hapus job lama
                for old in context.job_queue.get_jobs_by_name(job_name):
                    old.schedule_removal()
                # buat job baru
                context.job_queue.run_daily(
                    reminder,
                    time=utc_time,
                    name=job_name,
                    data={"chat_id": chat_id, "section": bagian, "jam": jam}
                )
                scheduled += 1

        await query.edit_message_text(
            text=(
                f"✅ Reminder *{waktu.capitalize()}* diaktifkan!\n"
                f"Total job: *{scheduled}*\n\n"
                "_✅ tidak diingatkan • ❌ akan diingatkan_"
            ),
            parse_mode="Markdown"
        )
        return

    # 5) Reset per kategori
    if data.startswith("reset_"):
        waktu = data.split("_", 1)[1]
        removed = 0
        for bagian in SUBMENUS:
            for jam in TIMES[waktu]:
                nm = re.sub(r"\W+", "_", f"{bagian}_{waktu}_{jam}")
                job_name = f"reminder_{chat_id}_{nm}"
                for job in context.job_queue.get_jobs_by_name(job_name):
                    job.schedule_removal()
                    removed += 1
        user_jobs.pop(chat_id, None)
        await query.edit_message_text(
            text=(
                f"🔁 Reminder *{waktu.capitalize()}* direset.\n"
                f"Total job dibatalkan: *{removed}*"
            ),
            parse_mode="Markdown"
        )
        return

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in user_jobs.get(chat_id, []):
        job.schedule_removal()
    user_jobs.pop(chat_id, None)
    user_skips.pop(chat_id, None)
    await update.message.reply_text("🔁 Semua reminder dan tanda telah direset.")

async def handle_root(request):
    return web.Response(text="Bot is running")

async def handle_webhook(request):
    application = request.app["application"]
    data = await request.json()
    tg_update = Update.de_json(data, application.bot)
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

    async def error_handler(upd, ctx):
        logger.error("Exception:", exc_info=ctx.error)
        if isinstance(upd, Update) and upd.effective_chat:
            await ctx.bot.send_message(upd.effective_chat.id, "⚠️ Terjadi kesalahan.")

    application.add_error_handler(error_handler)

    await application.initialize()
    await application.start()
    await application.job_queue.start()

    web_app = web.Application()
    web_app["application"] = application
    web_app.add_routes([
        web.get("/", handle_root),
        web.post(WEBHOOK_PATH, handle_webhook),
    ])

    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
    else:
        logger.warning("WEBHOOK_URL_BASE tidak diset, webhook nonaktif!")

    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # keep alive
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
