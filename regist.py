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
user_skips = {}    # chat_id → set of "<section>_<jam>"
user_pages = {}    # chat_id → current page per waktu
group_reminders = {}  # chat_id → {waktu: {enabled, thread_id}}

# ----------------------
# Schedule & Reminder Times
# ----------------------
SUBMENUS = [
    "DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG"
]
TIMES = {
    "pagi":   ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang":  ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam":  ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
}
PAGE_SIZE = 10

generic_reminder = {
    "pagi":  "🌅 Selamat pagi. Mohon periksa dan lengkapi jadwal melalui perintah /pagi.",
    "siang": "🌞 Selamat siang. Silakan tinjau dan tandai tugas Anda dengan perintah /siang.",
    "malam": "🌙 Selamat malam. Harap pastikan semua tugas telah dicek melalui perintah /malam.",
}

# ----------------------
# Helper: Show Schedule with CLEAR Button
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
        first_jam = TIMES[waktu][0]
        key = f"{sec}_{first_jam}"
        sym = '✅' if key in user_skips.get(chat_id, set()) else '❌'
        rows.append([
            InlineKeyboardButton(
                f"{sec} {first_jam} {sym}",
                callback_data=f"toggle_{waktu}_{sec}_{first_jam}_{page}"
            )
        ])
        small_buttons = []
        for jam in TIMES[waktu][1:]:
            key2 = f"{sec}_{jam}"
            sym2 = '✅' if key2 in user_skips.get(chat_id, set()) else '❌'
            small_buttons.append(
                InlineKeyboardButton(
                    f"{jam} {sym2}",
                    callback_data=f"toggle_{waktu}_{sec}_{jam}_{page}"
                )
            )
        for i in range(0, len(small_buttons), 3):
            rows.append(small_buttons[i:i+3])
    # CLEAR button
    rows.append([
        InlineKeyboardButton(
            "♻️ CLEAR ♻️", callback_data=f"clear_{waktu}_{page}"
        )
    ])
    nav = []
    total_pages = (len(SUBMENUS) - 1) // PAGE_SIZE
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"nav_{waktu}_{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"nav_{waktu}_{page+1}"))
    if nav:
        rows.append(nav)

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    else:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

# ----------------------
# Callback Handler
# ----------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data.startswith("toggle_"):
        _, w, sec, j, pg = data.split("_", 4)
        cid = q.message.chat.id
        key = f"{sec}_{j}"
        skips = user_skips.setdefault(cid, set())
        if key in skips:
            skips.remove(key)
        else:
            skips.add(key)
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
    data = context.job.data
    waktu = data["waktu"]
    chat_id = data["chat_id"]
    thread_id = data.get("thread_id")
    now = datetime.datetime.now(timezone).strftime("%H:%M")
    logger.info(f"🔔 Reminder: sesi={waktu}, chat_id={chat_id}, time={now}")
    reminder_text = generic_reminder[waktu]
    await context.bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
        text=f"{reminder_text}\n🕒 Waktu saat ini: *{now}*", parse_mode="Markdown")

async def notify_unchecked(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    waktu = data["waktu"]
    jam = data["jam"]
    chat_id = data["chat_id"]
    thread_id = data.get("thread_id")
    skips = user_skips.get(chat_id, set())
    unchecked = [sec for sec in SUBMENUS if f"{sec}_{jam}" not in skips]
    if unchecked:
        msg = (f"⚠️ Jadwal *{waktu}* jam *{jam}* belum lengkap.\n\n"
               f"Belum dichecklist: {', '.join(unchecked)}.\n\nMohon dicek segera. 🙏")
        await context.bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
            text=msg, parse_mode="Markdown")

# ----------------------
# Command Handlers & Setup
# ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "👋 Selamat datang di Bot Jadwal!\n"
        "Gunakan /pagi, /siang, /malam untuk lihat jadwal.\n"
        "Gunakan /aktifkan_pagi, /nonaktifkan_pagi (atau siang/malam) untuk mengelola pengingat grup.\n"
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

async def toggle_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    cmd = update.message.text.lstrip('/').split('@')[0]
    if cmd.endswith(('pagi','siang','malam')):
        parts = cmd.split('_')
        waktu = parts[1] if len(parts) == 2 else None
        if waktu in TIMES:
            thread_id = update.message.message_thread_id
            grp = group_reminders.setdefault(cid, {})
            on = parts[0] == 'aktifkan'
            grp[waktu] = {'enabled': on, 'thread_id': thread_id}

            # 🔧 Hapus job lama menggunakan get_jobs_by_name
            job_queue = context.job_queue
            for ts in TIMES[waktu]:
                r_name = f"r_{waktu}_{ts}_{cid}"
                n_name = f"n_{waktu}_{ts}_{cid}"
                for name in [r_name, n_name]:
                    for job in job_queue.get_jobs_by_name(name):
                        job.schedule_removal()

            # 🔁 Tambahkan job baru jika diaktifkan
            if on:
                for ts in TIMES[waktu]:
                    h, m = map(int, ts.split(':'))
                    data = {"waktu": waktu, "chat_id": cid, "thread_id": thread_id}
                    context.job_queue.run_daily(
                        send_reminder,
                        time=datetime.time(hour=h, minute=m, tzinfo=timezone),
                        days=range(7),
                        name=f"r_{waktu}_{ts}_{cid}",
                        data=data
                    )
                    tot = h*60 + m + 20
                    nh, nm = divmod(tot % (24 * 60), 60)
                    nd = {"waktu": waktu, "jam": ts, "chat_id": cid, "thread_id": thread_id}
                    context.job_queue.run_daily(
                        notify_unchecked,
                        time=datetime.time(hour=nh, minute=nm, tzinfo=timezone),
                        days=range(7),
                        name=f"n_{waktu}_{ts}_{cid}",
                        data=nd
                    )

            await update.message.reply_text(f"✅ Pengingat {waktu} {'diaktifkan' if on else 'dinonaktifkan'} untuk grup.")
            return
    await update.message.reply_text("Perintah tidak dikenali.")

async def waktu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lstrip('/').split('@')[0]
    if cmd in TIMES:
        await show_schedule(update, context, waktu=cmd, page=0)
    else:
        await update.message.reply_text("Perintah tidak dikenali.")

async def start_jobqueue(app):
    for cid, rem in group_reminders.items():
        for w in TIMES:
            cfg = rem.get(w)
            if not cfg or not cfg.get('enabled'): continue
            tid = cfg.get('thread_id')
            for ts in TIMES[w]:
                h, m = map(int, ts.split(':'))
                d = {"waktu": w, "chat_id": cid, "thread_id": tid}
                app.job_queue.run_daily(send_reminder,
                    time=datetime.time(hour=h, minute=m, tzinfo=timezone), days=range(7),
                    name=f"r_{w}_{ts}_{cid}", data=d)
                tot = h*60 + m + 20; nh, nm = divmod(tot % (24*60), 60)
                nd = {"waktu": w, "jam": ts, "chat_id": cid, "thread_id": tid}
                app.job_queue.run_daily(notify_unchecked,
                    time=datetime.time(hour=nh, minute=nm, tzinfo=timezone), days=range(7),
                    name=f"n_{w}_{ts}_{cid}", data=nd)
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
    app = ApplicationBuilder().token(token).persistence(persistence).post_init(start_jobqueue).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler(["aktifkan_pagi","aktifkan_siang","aktifkan_malam",
                                     "nonaktifkan_pagi","nonaktifkan_siang","nonaktifkan_malam"], toggle_reminder_cmd))
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
