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

# Environment vars
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
user_skips = {}      # chat_id → set of "<section>_<jam>"
user_pages = {}      # chat_id → current page per waktu
group_reminders = {} # chat_id → {waktu: bool}
group_threads = {}   # chat_id → message_thread_id

# ----------------------
# Schedule & Reminder Times
# ----------------------
SUBMENUS = ["DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG"]
TIMES = {
    "pagi":  ["08:00","09:00","10:00","11:00","12:00","13:00","14:00"],
    "siang": ["16:00","17:00","18:00","19:00","20:00","21:00","22:00"],
    "malam": ["00:00","01:00","02:00","03:00","04:00","05:00","06:00"],
}
PAGE_SIZE = 10

generic_reminder = {
    "pagi":  "🌅 Selamat pagi. Mohon periksa dan lengkapi jadwal melalui perintah /pagi.",
    "siang": "🌞 Selamat siang. Silakan tinjau dan tandai tugas Anda dengan perintah /siang.",
    "malam": "🌙 Selamat malam. Harap pastikan semua tugas telah dicek melalui perintah /malam.",
}

# ----------------------
# Helper: Show Schedule with Activate & Reset Buttons
# ----------------------
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, waktu="pagi", page=0):
    chat_id = update.effective_chat.id
    user_pages[chat_id] = page
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    subs = SUBMENUS[start:end]

    text = (
        f"*Jadwal {waktu.capitalize()}*\n\n"
        "Pilih checklist atau gunakan tombol di bawah untuk mengaktifkan kategori atau reset hari ini."
    )
    rows = []
    # Sections with toggles and activate button
    for sec in subs:
        # Time toggles
        line = []
        for jam in TIMES[waktu]:
            key = f"{sec}_{jam}"
            sym = '✅' if key in user_skips.get(chat_id, set()) else '❌'
            line.append(
                InlineKeyboardButton(
                    f"{jam} {sym}",
                    callback_data=f"toggle_{waktu}_{sec}_{jam}_{page}"
                )
            )
        rows.append(line)
        # Activate section button (full width)
        rows.append([
            InlineKeyboardButton(
                f"🔄 Aktifkan {sec}",
                callback_data=f"activate_{waktu}_{sec}_{page}"
            )
        ])

    # Navigation
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"nav_{waktu}_{page-1}")
        )
    if end < len(SUBMENUS):
        nav.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"nav_{waktu}_{page+1}")
        )
    if nav:
        rows.append(nav)

    # Reset all button (full width)
    rows.append([
        InlineKeyboardButton(
            "🔄 Reset Hari Ini",
            callback_data=f"reset_all_{waktu}_{page}"
        )
    ])

    if update.message:
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )
    else:
        await update.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )

# ----------------------
# Callback Handler
# ----------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    cid = q.message.chat.id
    parts = data.split("_")

    if data.startswith("toggle_"):
        _, w, sec, jam, pg = parts
        key = f"{sec}_{jam}"
        skips = user_skips.setdefault(cid, set())
        if key in skips:
            skips.remove(key)
        else:
            skips.add(key)
        await show_schedule(update, context, waktu=w, page=int(pg))

    elif data.startswith("activate_"):
        _, w, sec, pg = parts
        skips = user_skips.setdefault(cid, set())
        for jam in TIMES[w]:
            skips.discard(f"{sec}_{jam}")
        await show_schedule(update, context, waktu=w, page=int(pg))

    elif data.startswith("reset_all_"):
        _, w, pg = parts
        # clear all skips and pause today's reminders for this sesi
        user_skips.pop(cid, None)
        group_reminders.setdefault(cid, {})[w] = False
        await q.edit_message_text(
            f"🔁 Checklist dihapus dan pengingat {w} dipause untuk hari ini. "
            f"Gunakan /aktifkan_{w} untuk mengaktifkan kembali.",
            parse_mode="Markdown"
        )

    elif data.startswith("nav_"):
        _, w, pg = parts
        await show_schedule(update, context, waktu=w, page=int(pg))

# ----------------------
# Reminder: initial & follow-up Notify
# ----------------------
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    waktu = data["waktu"]
    chat_id = data["chat_id"]
    now = datetime.datetime.now(timezone).strftime("%H:%M")
    thread_id = data.get("thread_id")

    if group_reminders.get(chat_id, {}).get(waktu):
        reminder_text = generic_reminder[waktu]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{reminder_text}\n🕒 Waktu saat ini: *{now}*",
            parse_mode="Markdown",
            message_thread_id=thread_id
        )

async def notify_unchecked(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    waktu = data["waktu"]
    jam = data["jam"]
    chat_id = data["chat_id"]
    thread_id = data.get("thread_id")
    skips = user_skips.get(chat_id, set())

    unchecked = [sec for sec in SUBMENUS if f"{sec}_{jam}" not in skips]
    if unchecked:
        msg = (
            f"⚠️ Jadwal {waktu} jam {jam} belum lengkap.\n\n"
            f"Belum dichecklist: {', '.join(unchecked)}.\n\n"
            "Mohon dicek segera. 🙏"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode="Markdown",
            message_thread_id=thread_id
        )

# ----------------------
# Command Handlers
# ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id
    if thread_id:
        group_threads[update.effective_chat.id] = thread_id

    txt = (
        "👋 Selamat datang di Bot Jadwal!\n"
        "Gunakan tombol di menu untuk checklist, activate kategori, atau reset."
    )
    await update.message.reply_text(txt)

async def cmd_waktu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(timezone)
    await update.message.reply_text(now.strftime("%Y-%m-%d %H:%M:%S %Z"))

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    user_skips.pop(cid, None)
    for w in TIMES:
        group_reminders.setdefault(cid, {})[w] = False
    await update.message.reply_text(
        "🔁 Semua checklist direset dan pengingat dipause untuk hari ini."
    )

async def toggle_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    cmd = update.message.text.lstrip('/').split('@')[0]
    thread_id = update.message.message_thread_id
    if thread_id:
        group_threads[cid] = thread_id

    if cmd.endswith(('pagi','siang','malam')):
        w = cmd.split('_')[1]
        grp = group_reminders.setdefault(cid, {k: False for k in TIMES})
        on = cmd.startswith('aktifkan')
        grp[w] = on

        if on:
            for ts in TIMES[w]:
                h, m = map(int, ts.split(':'))
                context.application.job_queue.run_daily(
                    send_reminder,
                    time=datetime.time(hour=h, minute=m, tzinfo=timezone),
                    days=tuple(range(7)),
                    name=f"r_{w}_{ts}_{cid}",
                    data={"waktu": w, "chat_id": cid, "thread_id": thread_id}
                )
                nh, nm = divmod((h*60 + m + 20) % (24*60), 60)
                context.application.job_queue.run_daily(
                    notify_unchecked,
                    time=datetime.time(hour=nh, minute=nm, tzinfo=timezone),
                    days=tuple(range(7)),
                    name=f"n_{w}_{ts}_{cid}",
                    data={"waktu": w, "jam": ts, "chat_id": cid, "thread_id": thread_id}
                )

        await update.message.reply_text(
            f"✅ Pengingat {w} {'diaktifkan' if on else 'dinonaktifkan'}."
        )

async def waktu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lstrip('/').split('@')[0]
    if cmd in TIMES:
        await show_schedule(update, context, waktu=cmd, page=0)
    else:
        await update.message.reply_text("Perintah tidak dikenali.")

# ----------------------
# Setup Jobs & App
# ----------------------
async def start_jobqueue(app):
    for cid, rem in group_reminders.items():
        thread_id = group_threads.get(cid)
        for w, slots in TIMES.items():
            if not rem.get(w):
                continue
            for ts in slots:
                h, m = map(int, ts.split(':'))
                app.job_queue.run_daily(
                    send_reminder,
                    time=datetime.time(hour=h, minute=m, tzinfo=timezone),
                    days=tuple(range(7)),
                    name=f"r_{w}_{ts}_{cid}",
                    data={"waktu": w, "chat_id": cid, "thread_id": thread_id}
                )
                nh, nm = divmod((h*60 + m + 20) % (24*60), 60)
                app.job_queue.run_daily(
                    notify_unchecked,
                    time=datetime.time(hour=nh, minute=nm, tzinfo=timezone),
                    days=tuple(range(7)),
                    name=f"n_{w}_{ts}_{cid}",
                    data={"waktu": w, "jam": ts, "chat_id": cid, "thread_id": thread_id}
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
    app = (
        ApplicationBuilder()
        .token(token)
        .persistence(persistence)
        .post_init(start_jobqueue)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler(
        ["aktifkan_pagi","aktifkan_siang","aktifkan_malam",
         "nonaktifkan_pagi","nonaktifkan_siang","nonaktifkan_malam"],
        toggle_reminder_cmd
    ))
    app.add_handler(CommandHandler(["pagi","siang","malam"], waktu_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("waktu", cmd_waktu))
    app.add_handler(CallbackQueryHandler(button))

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
