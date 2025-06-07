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
user_chats = set()  # track chats where bot used
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

async def daily_summary(context: ContextTypes.DEFAULT_TYPE):
    yesterday = (datetime.datetime.now(timezone) - datetime.timedelta(days=1)).date()
    for chat_id in user_chats:
        done = []
        undone = []
        total = 0
        for section in SUBMENUS:
            for jam_list in TIMES.values():
                for jam in jam_list:
                    total += 1
                    key = f"{section}_{jam}"
                    if chat_id in user_skips and key in user_skips[chat_id]:
                        done.append(f"{section} {jam}")
                    else:
                        undone.append(f"{section} {jam}")
        percent = (len(done)/total*100) if total else 0
        msg = (
            f"📋 Ringkasan tugas {yesterday}\n"
            f"Selesai (✅): {len(done)}\n"
            f"Belum selesai (❌): {len(undone)}\n"
            f"Ketepatan waktu: {percent:.1f}%\n\n"
            f"Detail selesai: {', '.join(done[:5])}...\n"
            f"Detail belum: {', '.join(undone[:5])}..."
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            logger.error(f"Gagal kirim daily summary ke {chat_id}: {e}")

# ----------------------
# Handlers
# ----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_chats.add(chat_id)
    keyboard = [[
        InlineKeyboardButton("Pagi", callback_data="main_pagi"),
        InlineKeyboardButton("Siang", callback_data="main_siang"),
        InlineKeyboardButton("Malam", callback_data="main_malam"),
    ]]
    await update.message.reply_text(
        "🕒 Mulai bot dan pilih jadwal:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs():
        if str(chat_id) in job.name:
            job.schedule_removal()
    user_skips.pop(chat_id, None)
    await update.message.reply_text("🔁 Semua tugas dan tanda telah direset.")

async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0] not in TIMES:
        await update.message.reply_text("Gunakan: /jadwalaktif <pagi|siang|malam>")
        return
    cat = args[0]
    chat_id = update.effective_chat.id
    marks = user_skips.get(chat_id, set())
    lines = []
    for jam in TIMES[cat]:
        status = "✅" if any(f"{section}_{jam}" in marks for section in SUBMENUS) else "❌"
        lines.append(f"{jam} {status}")
    msg = f"📅 Jadwal {cat}:\n" + "\n".join(lines)
    await update.message.reply_text(msg)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    if data.startswith("main_"):
        waktu = data.split("_", 1)[1]
        btn_on = InlineKeyboardButton(f"🔔 Aktifkan {waktu.capitalize()}", callback_data=f"activate_{waktu}")
        btn_reset = InlineKeyboardButton(f"🔁 Reset {waktu.capitalize()}", callback_data=f"reset_{waktu}")
        keyboard = [[btn_on], [btn_reset]]
        for i in range(0, len(SUBMENUS), 4):
            row = [InlineKeyboardButton(name, callback_data=f"sub_{waktu}_{name}") for name in SUBMENUS[i:i+4]]
            keyboard.append(row)
        return await query.edit_message_text(
            f"⏰ Kategori: *{waktu.capitalize()}*\nPilih bagian…",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    if data.startswith("sub_"):
        _, waktu, bagian = data.split("_", 2)
        marks = user_skips.get(chat_id, set())
        keyboard = []
        for i in range(0, len(TIMES[waktu]), 3):
            row = []
            for jam in TIMES[waktu][i:i+3]:
                sym = "✅" if f"{bagian}_{jam}" in marks else "❌"
                row.append(InlineKeyboardButton(f"{jam} {sym}", callback_data=f"toggle_{waktu}_{bagian}_{jam}"))
            keyboard.append(row)
        return await query.edit_message_text(
            f"🗂 {bagian}: toggle…",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    if data.startswith("toggle_"):
        _, waktu, bagian, jam = data.split("_", 3)
        key = f"{bagian}_{jam}"
        marks = user_skips.setdefault(chat_id, set())
        if key in marks: marks.remove(key)
        else: marks.add(key)
        new_rows = []
        for i in range(0, len(TIMES[waktu]), 3):
            row = []
            for jm in TIMES[waktu][i:i+3]:
                sym = "✅" if f"{bagian}_{jm}" in marks else "❌"
                row.append(InlineKeyboardButton(f"{jm} {sym}", callback_data=f"toggle_{waktu}_{bagian}_{jm}"))
            new_rows.append(row)
        return await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_rows))

    if data.startswith("activate_"):
        waktu = data.split("_", 1)[1]
        jam_set = {jam for bagian in SUBMENUS for jam in TIMES[waktu] if f"{bagian}_{jam}" not in user_skips.get(chat_id, set())}
        now = datetime.datetime.now(timezone)
        for jam in jam_set:
            h, m = map(int, jam.split(':'))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target < now: target += datetime.timedelta(days=1)
            rem = target - datetime.timedelta(minutes=5)
            rem_utc = rem.astimezone(pytz.utc).time()
            grp_j = f"grp_{chat_id}_{jam.replace(':','')}"
            for old in context.job_queue.get_jobs_by_name(grp_j): old.schedule_removal()
            context.job_queue.run_daily(group_reminder, time=rem_utc, name=grp_j, data={"chat_id":chat_id,"jam":jam})
            overdue = target + datetime.timedelta(minutes=20)
            delay = (overdue - now).total_seconds()
            chk_j = f"chk_{chat_id}_{jam.replace(':','')}"
            for old in context.job_queue.get_jobs_by_name(chk_j): old.schedule_removal()
            if delay>0: context.job_queue.run_once(check_overdue, when=delay, name=chk_j, data={"chat_id":chat_id,"jam":jam})
        return await query.edit_message_text(f"✅ Reminder {waktu} diaktifkan untuk {len(jam_set)} jam.")

    if data.startswith("reset_"):
        waktu = data.split("_",1)[1]
        removed=0
        for jam in TIMES[waktu]:
            for jn in (f"grp_{chat_id}_{jam.replace(':','')}",f"chk_{chat_id}_{jam.replace(':','')}"):
                for job in context.job_queue.get_jobs_by_name(jn): job.schedule_removal(); removed+=1
        user_skips.pop(chat_id,None)
        return await query.edit_message_text(f"🔁 Reset {waktu}, {removed} job dihapus.")

# ----------------------
# Webhook & App setup
# ----------------------

async def handle_root(request):
    return web.Response(text="Bot is running")

async def handle_webhook(request):
    app = request.app["application"]
    data = await request.json()
    tg_upd = Update.de_json(data, app.bot)
    await app.update_queue.put(tg_upd)
    return web.Response()

async def main():
    application = ApplicationBuilder().token(TOKEN).persistence(persistence).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_all))
    application.add_handler(CommandHandler("jadwalaktif", show_schedule))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(lambda u,c: logger.error("Error",exc_info=c.error))

    await application.initialize()
    await application.start()
    await application.job_queue.start()
    # schedule daily summary at 06:00
    application.job_queue.run_daily(daily_summary, time=datetime.time(hour=6, minute=0, tzinfo=timezone))

    web_app = web.Application()
    web_app["application"] = application
    web_app.add_routes([web.get("/", handle_root), web.post(WEBHOOK_PATH, handle_webhook)])
    if WEBHOOK_URL: await application.bot.set_webhook(WEBHOOK_URL)

    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    while True: await asyncio.sleep(3600)

if __name__ == "__main__": asyncio.run(main())
