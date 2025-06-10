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
# ----------------
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
user_skips = {}       # chat_id → set of "<section>_<jam>"
user_pages = {}       # chat_id → current page per waktu
group_reminders = {}  # chat_id → {waktu: {enabled, thread_id}}
edit_history = []     # List of tuples (timestamp, user_name, action_description)

# ----------------------
# Schedule & Reminder Times
# ----------------------
SUBMENUS = ["DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG"]
TIMES = {
    "pagi":  ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang": ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam": ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"],
}
PAGE_SIZE = 10

generic_reminder = {
    "pagi":  "🌅 Selamat pagi. Mohon periksa dan lengkapi jadwal melalui perintah /pagi.",
    "siang": "🌞 Selamat siang. Silakan tinjau dan tandai tugas Anda dengan perintah /siang.",
    "malam": "🌙 Selamat malam. Harap pastikan semua tugas telah dicek melalui perintah /malam.",
}

# ----------------------
# Helper: Show Schedule
# ----------------------
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, waktu="pagi", page=0):
    chat_id = update.effective_chat.id
    user_pages[chat_id] = page

    # Pagination
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    subs = SUBMENUS[start:end]

    text = f"*Jadwal {waktu.capitalize()}*"
    rows = []

    for sec in subs:
        first = TIMES[waktu][0]
        key = f"{sec}_{first}"
        sym = "✅" if key in user_skips.get(chat_id, set()) else "❌"
        rows.append([
            InlineKeyboardButton(f"{sec} {first} {sym}",
                                 callback_data=f"toggle_{waktu}_{sec}_{first}_{page}")
        ])
        small = []
        for jam in TIMES[waktu][1:]:
            k2 = f"{sec}_{jam}"
            s2 = "✅" if k2 in user_skips.get(chat_id, set()) else "❌"
            small.append(InlineKeyboardButton(f"{jam} {s2}",
                                             callback_data=f"toggle_{waktu}_{sec}_{jam}_{page}"))
        for i in range(0, len(small), 3):
            rows.append(small[i:i+3])

    # CLEAR
    rows.append([InlineKeyboardButton("♻️ CLEAR ♻️", callback_data=f"clear_{waktu}_{page}")])

    # Navigation
    nav = []
    total = (len(SUBMENUS) - 1) // PAGE_SIZE
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"nav_{waktu}_{page-1}"))
    if page < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"nav_{waktu}_{page+1}"))
    if nav:
        rows.append(nav)

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    else:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown",
                                                     reply_markup=InlineKeyboardMarkup(rows))

# ----------------------
# Callback Handler
# ----------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data.startswith("toggle_"):
        _, w, sec, jam, pg = data.split("_", 4)
        cid = q.message.chat.id
        key = f"{sec}_{jam}"
        skips = user_skips.setdefault(cid, set())
        if key in skips:
            skips.remove(key)
            action = "❌ Unchecked"
        else:
            skips.add(key)
            action = "✅ Checked"
        # record history
        user = update.effective_user
        ts = datetime.datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S")
        edit_history.append((ts, user.full_name, f"{action} {sec} {jam}"))
        if len(edit_history) > 100:
            edit_history.pop(0)
        await show_schedule(update, context, waktu=w, page=int(pg))

    elif data.startswith("nav_"):
        _, w, pg = data.split("_", 2)
        await show_schedule(update, context, waktu=w, page=int(pg))

    elif data.startswith("clear_"):
        _, w, pg = data.split("_", 2)
        cid = q.message.chat.id
        user_skips[cid] = set()
        user_pages[cid] = 0
        await show_schedule(update, context, waktu=w, page=int(pg))

# ----------------------
# Reminder & Notification
# ----------------------
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    w = d["waktu"]; cid = d["chat_id"]; tid = d.get("thread_id")
    now = datetime.datetime.now(timezone).strftime("%H:%M")
    logger.info(f"🔔 Reminder: sesi={w}, chat_id={cid}, time={now}")
    txt = generic_reminder[w]
    await context.bot.send_message(chat_id=cid, message_thread_id=tid,
                                   text=f"{txt}\n🕒 Waktu saat ini: *{now}*",
                                   parse_mode="Markdown")

async def notify_unchecked(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    w = d["waktu"]; jam = d["jam"]; cid = d["chat_id"]; tid = d.get("thread_id")
    skips = user_skips.get(cid, set())
    missing = [s for s in SUBMENUS if f"{s}_{jam}" not in skips]
    if missing:
        msg = (f"⚠️ Jadwal *{w}* jam *{jam}* belum lengkap.\n\n"
               f"Belum dichecklist: {', '.join(missing)}.\n\nMohon dicek segera. 🙏")
        await context.bot.send_message(chat_id=cid, message_thread_id=tid,
                                       text=msg, parse_mode="Markdown")

# ----------------------
# Command Handlers
# ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "👋 Selamat datang di Bot Jadwal!\n"
        "Gunakan /pagi, /siang, /malam untuk lihat jadwal.\n"
        "Gunakan /aktifkan_pagi, /nonaktifkan_pagi (atau siang/malam) "
        "untuk mengelola pengingat grup.\n"
        "/reset untuk reset checklistnya."
    )
    await update.message.reply_text(txt)

async def cmd_waktu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(timezone)
    await update.message.reply_text(now.strftime("%Y-%m-%d %H:%M:%S %Z"))

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    user_skips.pop(cid, None)
    user_pages.pop(cid, None)
    await update.message.reply_text("🔁 Checklist direset.")

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not edit_history:
        await update.message.reply_text("📭 Belum ada riwayat perubahan checklist.")
        return
    recent = edit_history[-15:]
    text = "*Riwayat Checklist Terakhir:*\n"
    for ts, usr, act in reversed(recent):
        text += f"🕒 {ts}\n👤 {usr}\n➡️ {act}\n\n"
    await update.message.reply_text(text.strip(), parse_mode="Markdown")

async def toggle_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    cmd = update.message.text.lstrip('/').split('@')[0]
    if cmd.endswith(("pagi", "siang", "malam")):
        parts = cmd.split('_')
        w = parts[1] if len(parts) == 2 else None
        if w in TIMES:
            tid = update.message.message_thread_id
            grp = group_reminders.setdefault(cid, {})
            on = parts[0] == "aktifkan"
            grp[w] = {"enabled": on, "thread_id": tid}
            jq = context.job_queue
            # remove old jobs
            for ts in TIMES[w]:
                for name in (f"r_{w}_{ts}_{cid}", f"n_{w}_{ts}_{cid}"):
                    for job in jq.get_jobs_by_name(name):
                        job.schedule_removal()
            # add new jobs
            if on:
                for ts in TIMES[w]:
                    h, m = map(int, ts.split(':'))
                    data = {"waktu": w, "chat_id": cid, "thread_id": tid}
                    jq.run_daily(send_reminder,
                                 time=datetime.time(hour=h, minute=m, tzinfo=timezone),
                                 days=range(7),
                                 name=f"r_{w}_{ts}_{cid}",
                                 data=data)
                    tot = h*60 + m + 20
                    nh, nm = divmod(tot % (24*60), 60)
                    nd = {"waktu": w, "jam": ts, "chat_id": cid, "thread_id": tid}
                    jq.run_daily(notify_unchecked,
                                 time=datetime.time(hour=nh, minute=nm, tzinfo=timezone),
                                 days=range(7),
                                 name=f"n_{w}_{ts}_{cid}",
                                 data=nd)
            await update.message.reply_text(
                f"✅ Pengingat {w} {'diaktifkan' if on else 'dinonaktifkan'} untuk grup."
            )

async def waktu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lstrip('/').split('@')[0]
    if cmd in TIMES:
        await show_schedule(update, context, waktu=cmd, page=0)
    else:
        await update.message.reply_text("Perintah tidak dikenali.")

# ----------------------
# JobQueue Startup
# ----------------------
async def start_jobqueue(app):
    for cid, rem in group_reminders.items():
        for w in TIMES:
            cfg = rem.get(w)
            if not cfg or not cfg.get("enabled"):
                continue
            tid = cfg.get("thread_id")
            for ts in TIMES[w]:
                h, m = map(int, ts.split(':'))
                data = {"waktu": w, "chat_id": cid, "thread_id": tid}
                app.job_queue.run_daily(send_reminder,
                                       time=datetime.time(hour=h, minute=m, tzinfo=timezone),
                                       days=range(7),
                                       name=f"r_{w}_{ts}_{cid}",
                                       data=data)
                tot = h*60 + m + 20
                nh, nm = divmod(tot % (24*60), 60)
                nd = {"waktu": w, "jam": ts, "chat_id": cid, "thread_id": tid}
                app.job_queue.run_daily(notify_unchecked,
                                       time=datetime.time(hour=nh, minute=nm, tzinfo=timezone),
                                       days=range(7),
                                       name=f"n_{w}_{ts}_{cid}",
                                       data=nd)
    await app.job_queue.start()
    logger.info("JobQueue started")

# ----------------------
# Webhook Handlers & Main
# ----------------------
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

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler(
        ["aktifkan_pagi","aktifkan_siang","aktifkan_malam",
         "nonaktifkan_pagi","nonaktifkan_siang","nonaktifkan_malam"],
        toggle_reminder_cmd))
    app.add_handler(CommandHandler(["pagi","siang","malam"], waktu_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("waktu", cmd_waktu))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CallbackQueryHandler(button))

    web_app = web.Application()
    web_app['application'] = app
    web_app.add_routes([
        web.get('/', handle_root),
        web.post(WEBHOOK_PATH, handle_webhook),
    ])

    if WEBHOOK_URL:
        await app.bot.set_webhook(WEBHOOK_URL)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

    await app.initialize()
    await app.start()
    # keep alive
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
