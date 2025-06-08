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

# ----------------------
# Persistence & State
# ----------------------
persistence = PicklePersistence(filepath="bot_data.pkl")
user_skips = {}    # chat_id → set of "<section>_<jam>"
user_pages = {}    # chat_id → current page per waktu
# group reminder settings: chat_id → {"pagi": bool, "siang": bool, "malam": bool}
group_reminders = {}

timezone = pytz.timezone(os.environ.get("TZ", "Asia/Jakarta"))

# ----------------------
# Schedule & Reminder Times
# ----------------------
SUBMENUS = [
    "DWT", "BG", "DWL", "DST", "KRM", "BRK", "PRW", "RJN", "STP", "PNR",
    "NMR", "CKL", "KRT", "LKS", "JMP", "TRG", "SJR", "GNG", "MTN", "BDN"
]
TIMES = {
    "pagi":   ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang":  ["15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00"],
    "malam":  ["22:00", "23:00", "00:00", "01:00", "02:00", "03:00", "04:00"]
}
PAGE_SIZE = 10

# friendly reminder messages per waktu
generic_reminder = {
    "pagi":  "🌅 Pengingat pagi: gunakan /pagi untuk cek jadwal dan tandai tugas Anda!",
    "siang": "🌞 Pengingat siang: gunakan /siang untuk cek jadwal dan tandai tugas Anda!",
    "malam": "🌙 Pengingat malam: gunakan /malam untuk cek jadwal dan tandai tugas Anda!",
}

# ----------------------
# Helper: Show Schedule
# ----------------------
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, waktu="pagi", page=0):
    chat_id = update.effective_chat.id
    skips = user_skips.get(chat_id, set())
    user_pages[chat_id] = page

    # Pagination
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    subs = SUBMENUS[start:end]
    total_pages = (len(SUBMENUS) - 1) // PAGE_SIZE + 1

    # Build text
    header = f"*⏰ Jadwal {waktu.capitalize()} (Halaman {page+1}/{total_pages})*"
    lines = [header, ""]
    for sec in subs:
        jam_stat = [f"{j} {'✅' if f'{sec}_{j}' in skips else '❌'}" for j in TIMES[waktu]]
        lines.append(f"*{sec}*: {', '.join(jam_stat)}")
    text = "\n".join(lines)

    # Build keyboard
    rows = []
    for sec in subs:
        # first big button
        fjam = TIMES[waktu][0]
        key = f"{sec}_{fjam}"
        sym = '✅' if key in skips else '❌'
        rows.append([InlineKeyboardButton(f"{sec} {fjam} {sym}", callback_data=f"toggle_{waktu}_{sec}_{fjam}_{page}")])
        # small buttons
        small = []
        for jam in TIMES[waktu][1:]:
            key2 = f"{sec}_{jam}"
            sym2 = '✅' if key2 in skips else '❌'
            small.append(InlineKeyboardButton(f"{jam} {sym2}", callback_data=f"toggle_{waktu}_{sec}_{jam}_{page}"))
        rows.append(small[:3])
        if len(small) > 3:
            rows.append(small[3:])
    # nav
    nav = []
    if page>0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"nav_{waktu}_{page-1}"))
    if end<len(SUBMENUS): nav.append(InlineKeyboardButton("➡️", callback_data=f"nav_{waktu}_{page+1}"))
    if nav: rows.append(nav)

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
        _,w,sec,j,pg = data.split("_",4)
        cid = q.message.chat.id
        key = f"{sec}_{j}"
        skips = user_skips.setdefault(cid,set())
        if key in skips: skips.remove(key)
        else: skips.add(key)
        await show_schedule(update,context,w,page=int(pg))
    elif data.startswith("nav_"):
        _,w,pg = data.split("_",2)
        await show_schedule(update, context, waktu=w, page=int(pg))

# ----------------------
# Reminder Handler
# ----------------------
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    waktu = data["waktu"]
    chat_id = data["chat_id"]
    if group_reminders.get(chat_id,{}).get(waktu):
        await context.bot.send_message(chat_id=chat_id, text=generic_reminder[waktu])

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
    user_skips.pop(cid,None)
    user_pages.pop(cid,None)
    await update.message.reply_text("🔁 Checklist direset.")

async def toggle_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    cmd = update.message.text.lstrip('/').lower()
    parts=cmd.split('_')
    if len(parts)!=2 or parts[1] not in TIMES:
        return
    waktu=parts[1]
    grp=group_reminders.setdefault(cid,{k:False for k in TIMES})
    on = parts[0]=='aktifkan'
    grp[waktu]=on
    await update.message.reply_text(f"✅ Pengingat {waktu} {'diaktifkan' if on else 'dinonaktifkan'} untuk grup.")

# ----------------------
# Setup Jobs & App
# ----------------------
async def start_jobqueue(app):
    # schedule reminder for each waktu at each slot
    for w, slots in TIMES.items():
        for ts in slots:
            h,m = map(int,ts.split(':'))
            app.job_queue.run_daily(
                send_reminder,
                time=datetime.time(hour=h,minute=m,tzinfo=timezone),
                days=(0,1,2,3,4,5,6),
                name=f"r_{w}_{ts}",
                data={"waktu":w, "chat_id":None}
            )
    await app.job_queue.start()
    logger.info("JobQueue started")

async def handle_root(request): return web.Response(text="Bot running")
async def handle_webhook(request):
    data=await request.json()
    app=request.app['application']
    upd=Update.de_json(data,app.bot)
    await app.update_queue.put(upd)
    return web.Response()

async def main():
    app=ApplicationBuilder().token(token).persistence(persistence).post_init(start_jobqueue).build()
    # handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler(["pagi","siang","malam"], lambda u,c: show_schedule(u,c,waktu=u.message.text.lstrip('/'), page=0)))
    app.add_handler(CommandHandler(["aktifkan_pagi","aktifkan_siang","aktifkan_malam",
                                    "nonaktifkan_pagi","nonaktifkan_siang","nonaktifkan_malam"], toggle_reminder_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("waktu", cmd_waktu))
    app.add_handler(CallbackQueryHandler(button))

    web_app=web.Application()
    web_app['application']=app
    web_app.add_routes([web.get('/', handle_root), web.post(WEBHOOK_PATH, handle_webhook)])
    if WEBHOOK_URL: await app.bot.set_webhook(WEBHOOK_URL)
    runner=web.AppRunner(web_app); await runner.setup()
    await web.TCPSite(runner,'0.0.0.0',PORT).start()
    await app.initialize(); await app.start()
    while True: await asyncio.sleep(3600)

if __name__=='__main__': asyncio.run(main())
