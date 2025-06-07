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
import telegram.error

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Bot token & webhook config
TOKEN = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None

# Persistence & state
persistence = PicklePersistence(filepath="reminder_data.pkl")
user_skips = {}   # chat_id → set of "<section>_<jam>"
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

# ----------------------
# Reminder & Check Functions
# ----------------------

async def group_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    jam = data["jam"]
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"5 menit lagi jam {jam} silahkan test daftar bagi yang bertugas....."
        )
    except Exception as e:
        logger.error(f"Gagal kirim reminder ke grup: {e}")

async def check_overdue(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    jam = data["jam"]

    messages = []
    for section in SUBMENUS:
        key = f"{section}_{jam}"
        if chat_id not in user_skips or key not in user_skips[chat_id]:
            messages.append(f"{section} jam {jam} test register sudah melewati 20 menit masih belum di kerjakan")

    if not messages:
        return

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for admin in admins:
            if admin.user.is_bot:
                continue
            for msg in messages:
                await context.bot.send_message(admin.user.id, msg)
    except Exception as e:
        logger.error(f"Gagal kirim DM ke admin: {e}")

async def reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    section = data.get("section")
    jam = data.get("jam")

    # skip if marked done
    if chat_id in user_skips and f"{section}_{jam}" in user_skips[chat_id]:
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔔 [Reminder] Bagian: {section}, Jam: {jam}"
    )

# ----------------------
# Handlers
# ----------------------

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
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    # Main menu
    if data.startswith("main_"):
        waktu = data.split("_", 1)[1]
        btn_on = InlineKeyboardButton(f"🔔 Aktifkan {waktu.capitalize()}", callback_data=f"activate_{waktu}")
        btn_reset = InlineKeyboardButton(f"🔁 Reset {waktu.capitalize()}", callback_data=f"reset_{waktu}")
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

    # Submenu
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

    # Toggle skip flag
    if data.startswith("toggle_"):
        _, waktu, bagian, jam = data.split("_", 3)
        key = f"{bagian}_{jam}"
        marks = user_skips.setdefault(chat_id, set())
        if key in marks:
            marks.remove(key)
        else:
            marks.add(key)

        # update markup
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

    # Activate all for a time category
    if data.startswith("activate_"):
        waktu = data.split("_", 1)[1]
        scheduled = 0
        jam_terjadwal = set()

        for bagian in SUBMENUS:
            for jam in TIMES[waktu]:
                key = f"{bagian}_{jam}"
                if chat_id in user_skips and key in user_skips[chat_id]:
                    continue
                jam_terjadwal.add(jam)

        now = datetime.datetime.now(timezone)
        for jam in jam_terjadwal:
            h, m = map(int, jam.split(":"))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target < now:
                target += datetime.timedelta(days=1)

            # schedule group reminder 5 minutes before
            remind_at = target - datetime.timedelta(minutes=5)
            remind_at_utc = timezone.localize(remind_at).astimezone(pytz.utc).time()
            grp_job_name = f"group_reminder_{chat_id}_{jam.replace(':', '')}"
            for old in context.job_queue.get_jobs_by_name(grp_job_name):
                old.schedule_removal()
            context.job_queue.run_daily(
                group_reminder,
                time=remind_at_utc,
                name=grp_job_name,
                data={"chat_id": chat_id, "jam": jam}
            )

            # schedule overdue check 20 minutes after
            overdue_time = target + datetime.timedelta(minutes=20)
            delay = (overdue_time - now).total_seconds()
            check_name = f"overdue_check_{chat_id}_{jam.replace(':', '')}"
            for old in context.job_queue.get_jobs_by_name(check_name):
                old.schedule_removal()
            if delay > 0:
                context.job_queue.run_once(
                    check_overdue,
                    when=delay,
                    name=check_name,
                    data={"chat_id": chat_id, "jam": jam}
                )

            scheduled += 1

        await query.edit_message_text(
            text=(
                f"✅ Reminder *{waktu.capitalize()}* diaktifkan!\n"
                f"Total jam terjadwal: *{scheduled}*\n\n"
                "_✅ tidak diingatkan • ❌ akan diingatkan_"
            ),
            parse_mode="Markdown"
        )
        return

    # Reset per category
    if data.startswith("reset_"):
        waktu = data.split("_", 1)[1]
        removed = 0
        for bagian in SUBMENUS:
            for jam in TIMES[waktu]:
                nm = re.sub(r"\W+", "_", f"{bagian}_{waktu}_{jam}")
                # group reminder
                grp_job = f"group_reminder_{chat_id}_{jam.replace(':', '')}"
                for job in context.job_queue.get_jobs_by_name(grp_job):
                    job.schedule_removal()
                    removed += 1
                # overdue check
                chk_job = f"overdue_check_{chat_id}_{jam.replace(':', '')}"
                for job in context.job_queue.get_jobs_by_name(chk_job):
                    job.schedule_removal()
                    removed += 1
        user_skips.pop(chat_id, None)
        await query.edit_message_text(
            text=(
                f"🔁 Reminder *{waktu.capitalize()}* direset."\n
                f"Total job dibatalkan: *{removed}*"
            ),
            parse_mode="Markdown"
        )
        return

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # remove all jobs
    for job in context.job_queue.get_jobs():
        if str(chat_id) in job.name:
            job.schedule_removal()
    user_skips.pop(chat_id, None)
    await update.message.reply_text("🔁 Semua reminder dan tanda telah direset.")

# ----------------------
# Webhook & App setup
# ----------------------

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
