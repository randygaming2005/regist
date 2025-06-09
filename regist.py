import logging
import datetime
import pytz
import os
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

# ----------------------
# Logging & Config
# ----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------
# Environment Variables
# ----------------------
token = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_PATH = f"/{token}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
PORT = int(os.environ.get("PORT", 8000))

timezone = pytz.timezone(os.environ.get("TZ", "Asia/Jakarta"))

# ----------------------
# Persistence & State
# ----------------------
persistence = PicklePersistence(filepath="bot_data.pkl")
user_skips = {}        # chat_id -> set of "<section>_<time>"
user_pages = {}        # chat_id -> current page index
group_reminders = {}   # chat_id -> {waktu: {enabled, thread_id}}

# ----------------------
# Schedule & Reminder Data
# ----------------------
SUBMENUS = ["DWT","BG","DWL","NG","TG88","TTGL","KTT","TTGG"]
TIMES = {
    "pagi":  ["08:00","09:00","10:00","11:00","12:00","13:00","14:00"],
    "siang": ["16:00","17:00","18:00","19:00","20:00","21:00","22:00"],
    "malam": ["00:00","01:00","02:00","03:00","04:00","05:00","06:00"],
}
PAGE_SIZE = 4  # number of sections per page

generic_reminder = {
    "pagi":  "🌅 Selamat pagi. Mohon periksa dan lengkapi jadwal melalui /pagi.",
    "siang": "🌞 Selamat siang. Silakan tinjau dan tandai tugas Anda melalui /siang.",
    "malam": "🌙 Selamat malam. Harap pastikan semua tugas dicek via /malam.",
}

# ----------------------
# Show schedule with pagination and CLEAR
# ----------------------
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, waktu: str = "pagi", page: int = 0):
    chat_id = update.effective_chat.id
    user_pages[chat_id] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    subs = SUBMENUS[start:end]
    total_pages = (len(SUBMENUS) - 1) // PAGE_SIZE

    text = f"*Jadwal {waktu.capitalize()} (Page {page+1}/{total_pages+1})*"
    keyboard = []

    # Section rows
    for sec in subs:
        # main time slot
        first = TIMES[waktu][0]
        key = f"{sec}_{first}"
        sym = '✅' if key in user_skips.get(chat_id, set()) else '❌'
        keyboard.append([
            InlineKeyboardButton(f"{sec} {first} {sym}", callback_data=f"toggle_{waktu}_{sec}_{first}_{page}")
        ])
        # other times in rows of 3
        row = []
        for jam in TIMES[waktu][1:]:
            key2 = f"{sec}_{jam}"
            sym2 = '✅' if key2 in user_skips.get(chat_id, set()) else '❌'
            row.append(InlineKeyboardButton(f"{jam} {sym2}", callback_data=f"toggle_{waktu}_{sec}_{jam}_{page}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    # navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"nav_{waktu}_{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"nav_{waktu}_{page+1}"))
    if nav:
        keyboard.append(nav)

    # clear button
    keyboard.append([
        InlineKeyboardButton("♻️ CLEAR ♻️", callback_data=f"clear_{waktu}_{page}")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

# ----------------------
# Callback query handler
# ----------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action = data[0]
    waktu = data[1]
    cid = query.message.chat.id

    if action == "toggle" and len(data) == 5:
        _, _, sec, jam, pg = data
        key = f"{sec}_{jam}"
        skips = user_skips.setdefault(cid, set())
        if key in skips:
            skips.remove(key)
        else:
            skips.add(key)
        await show_schedule(update, context, waktu, int(pg))

    elif action == "nav" and len(data) == 3:
        _, _, pg = data
        await show_schedule(update, context, waktu, int(pg))

    elif action == "clear" and len(data) == 3:
        user_skips.pop(cid, None)
        await show_schedule(update, context, waktu, int(data[2]))

# ----------------------
# Reminder jobs
# ----------------------
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    w = data["waktu"]
    cid = data["chat_id"]
    thread_id = data.get("thread_id")
    now = datetime.datetime.now(timezone).strftime("%H:%M")

    logger.info(f"🔔 Sending reminder for {w} to chat {cid} at {now}")
    text = generic_reminder[w] + f"\n🕒 {now}"
    await context.bot.send_message(chat_id=cid, message_thread_id=thread_id, text=text, parse_mode="Markdown")

async def notify_unchecked(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    w = data["waktu"]
    jam = data["jam"]
    cid = data["chat_id"]
    thread_id = data.get("thread_id")
    skips = user_skips.get(cid, set())

    missing = [s for s in SUBMENUS if f"{s}_{jam}" not in skips]
    if missing:
        msg = (
            f"⚠️ Jadwal *{w}* jam *{jam}* belum lengkap.\n" +
            f"Belum: {', '.join(missing)}"
        )
        await context.bot.send_message(chat_id=cid, message_thread_id=thread_id, text=msg, parse_mode="Markdown")

# ----------------------
# Command handlers
# ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Selamat datang di Bot Jadwal!\n"
        "Gunakan /pagi, /siang, /malam untuk melihat jadwal.\n"
        "/aktifkan_<waktu> atau /nonaktifkan_<waktu> untuk mengelola reminder.\n"
        "/reset untuk reset checklist."
    )
    await update.message.reply_text(msg)

async def time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(timezone)
    await update.message.reply_text(now.strftime("%Y-%m-%d %H:%M:%S %Z"))

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    user_skips.pop(cid, None)
    user_pages.pop(cid, None)
    await update.message.reply_text("🔁 Checklist direset.")

async def toggle_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    parts = update.message.text.lstrip("/").split("_")
    if len(parts) == 2 and parts[1] in TIMES:
        act, w = parts
        on = (act == "aktifkan")
        thread_id = update.message.message_thread_id
        grp = group_reminders.setdefault(cid, {})
        grp[w] = {"enabled": on, "thread_id": thread_id}

        # remove existing jobs
        for job in context.application.job_queue.jobs():
            if f"_{w}_" in job.name:
                job.schedule_removal()

        # schedule new
        if on:
            for jam in TIMES[w]:
                h, m = map(int, jam.split(':'))
                data = {"waktu": w, "chat_id": cid, "thread_id": thread_id}
                context.application.job_queue.run_daily(
                    send_reminder,
                    time=datetime.time(hour=h, minute=m, tzinfo=timezone),
                    days=tuple(range(7)),
                    name=f"r_{w}_{jam}_{cid}",
                    data=data
                )
                # notify after 20 min
                total = h*60 + m + 20
                nh, nm = divmod(total % (24*60), 60)
                nd = {"waktu": w, "jam": jam, "chat_id": cid, "thread_id": thread_id}
                context.application.job_queue.run_daily(
                    notify_unchecked,
                    time=datetime.time(hour=nh, minute=nm, tzinfo=timezone),
                    days=tuple(range(7)),
                    name=f"n_{w}_{jam}_{cid}",
                    data=nd
                )
        text = f"✅ Reminder {w} {'activated' if on else 'deactivated' }"
    else:
        text = "Perintah tidak dikenali."
    await update.message.reply_text(text)

async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lstrip("/")
    if cmd in TIMES:
        await show_schedule(update, context, cmd, 0)
    else:
        await update.message.reply_text("Perintah tidak dikenali.")

# ----------------------
# Startup & Webhook
# ----------------------
async def start_jobqueue(app):
    for cid, rem in group_reminders.items():
        for w, slots in TIMES.items():
            cfg = rem.get(w)
            if not cfg or not cfg.get("enabled"): continue
            tid = cfg.get("thread_id")
            for jam in slots:
                h, m = map(int, jam.split(':'))
                data = {"waktu": w, "chat_id": cid, "thread_id": tid}
                app.job_queue.run_daily(
                    send_reminder,
                    time=datetime.time(hour=h, minute=m, tzinfo=timezone),
                    days=tuple(range(7)),
                    name=f"r_{w}_{jam}_{cid}",
                    data=data
                )
                total = h*60 + m + 20
                nh, nm = divmod(total % (24*60), 60)
                nd = {"waktu": w, "jam": jam, "chat_id": cid, "thread_id": tid}
                app.job_queue.run_daily(
                    notify_unchecked,
                    time=datetime.time(hour=nh, minute=nm, tzinfo=timezone),
                    days=tuple(range(7)),
                    name=f"n_{w}_{jam}_{cid}",
                    data=nd
                )
    await app.job_queue.start()
    logger.info("JobQueue started")

async def handle_root(request):
    return web.Response(text="Bot running")

async def handle_webhook(request):
    data = await request.json()
    app = request.app['application']
    upd = Update.de_json(data, app.bot)
    await app.update_queue.put(upd)
    return web.Response()

async def main():
    app = ApplicationBuilder()\
        .token(token)\
        .persistence(persistence)\
        .post_init(start_jobqueue)\
        .build()

    # register handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler(["pagi","siang","malam"], schedule_cmd))
    app.add_handler(CommandHandler(["aktifkan_pagi","aktifkan_siang","aktifkan_malam","nonaktifkan_pagi","nonaktifkan_siang","nonaktifkan_malam"], toggle_reminder_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("waktu", time_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))

    # webhook setup
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
    logger.info("Bot started")

    # keep alive
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())