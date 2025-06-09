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
user_skips = {}
user_pages = {}
group_reminders = {}

# ----------------------
# Schedule & Reminder Times
# ----------------------
SUBMENUS = ["DWT","BG","DWL","NG","TG88","TTGL","KTT","TTGG"]
TIMES = {
    "pagi": ["08:00","09:00","10:00","11:00","12:00","13:00","14:00"],
    "siang": ["16:00","17:00","18:00","19:00","20:00","21:00","22:00"],
    "malam": ["00:00","01:00","02:00","03:00","04:00","05:00","06:00"],
}
PAGE_SIZE = 10

generic_reminder = {
    "pagi": "🌅 Selamat pagi. Mohon periksa dan lengkapi jadwal melalui perintah /pagi.",
    "siang": "🌞 Selamat siang. Silakan tinjau dan tandai tugas Anda dengan perintah /siang.",
    "malam": "🌙 Selamat malam. Harap pastikan semua tugas telah dicek melalui perintah /malam.",
}

# ----------------------
# Show Schedule + CLEAR
# ----------------------
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, waktu: str = "pagi", page: int = 0):
    chat_id = update.effective_chat.id
    user_pages[chat_id] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    subs = SUBMENUS[start:end]

    text = f"*Jadwal {waktu.capitalize()}*"
    rows = []

    for sec in subs:
        first = TIMES[waktu][0]
        key = f"{sec}_{first}"
        sym = '✅' if key in user_skips.get(chat_id, set()) else '❌'
        rows.append([InlineKeyboardButton(f"{sec} {first} {sym}", callback_data=f"toggle_{waktu}_{sec}_{first}_{page}")])

        btns = [InlineKeyboardButton(f"{jam} {'✅' if f'{sec}_{jam}' in user_skips.get(chat_id,set()) else '❌'}", callback_data=f"toggle_{waktu}_{sec}_{jam}_{page}") for jam in TIMES[waktu][1:]]
        for i in range(0,len(btns),3): rows.append(btns[i:i+3])

    nav, total = [], (len(SUBMENUS)-1)//PAGE_SIZE
    if page>0: nav.append(InlineKeyboardButton("⬅️",callback_data=f"nav_{waktu}_{page-1}"))
    if page<total: nav.append(InlineKeyboardButton("➡️",callback_data=f"nav_{waktu}_{page+1}"))
    if nav: rows.append(nav)

    rows.append([InlineKeyboardButton("♻️ CLEAR ♻️", callback_data=f"clear_{waktu}_{page}")])
    markup = InlineKeyboardMarkup(rows)

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)

# ----------------------
# Callback Handler
# ----------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    parts = q.data.split("_"); action = parts[0]; cid = q.message.chat.id

    if action == "toggle" and len(parts)==5:
        _,w,sec,j,pg = parts
        key=f"{sec}_{j}"; skips=user_skips.setdefault(cid,set())
        skips.remove(key) if key in skips else skips.add(key)
        await show_schedule(update,context,w,page=int(pg))

    elif action=="nav" and len(parts)==3:
        _,w,pg = parts
        await show_schedule(update,context,w,page=int(pg))

    elif action=="clear" and len(parts)==3:
        _,w,pg = parts; user_skips.pop(cid,None)
        await show_schedule(update,context,w,page=int(pg))

# ----------------------
# Reminder & Notification
# ----------------------
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    data=context.job.data; w=data["waktu"]; cid=data["chat_id"]; tid=data.get("thread_id")
    now=datetime.datetime.now(timezone).strftime("%H:%M")
    logger.info(f"🔔 Reminder sesi={w}, chat_id={cid}, time={now}")
    try:
        await context.bot.send_message(chat_id=cid, message_thread_id=tid, text=f"{generic_reminder[w]}\n🕒 Waktu saat ini: *{now}*", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Gagal kirim reminder: {e}")

async def notify_unchecked(context: ContextTypes.DEFAULT_TYPE):
    data=context.job.data; w=data["waktu"]; jam=data["jam"]; cid=data["chat_id"]; tid=data.get("thread_id")
    skips=user_skips.get(cid,set()); missing=[s for s in SUBMENUS if f"{s}_{jam}" not in skips]
    if missing:
        msg=f"⚠️ Jadwal *{w}* jam *{jam}* belum lengkap.\nBelum dichecklist: {', '.join(missing)}.\nMohon dicek segera. 🙏"
        await context.bot.send_message(chat_id=cid, message_thread_id=tid, text=msg, parse_mode="Markdown")

# ----------------------
# Command Handlers
# ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt="👋 Selamat datang di Bot Jadwal!\nGunakan /pagi,/siang,/malam untuk lihat jadwal.\n/aktifkan_<waktu>,/nonaktifkan_<waktu> untuk pengingat grup.\n/reset untuk reset checklist."
    await update.message.reply_text(txt)

async def cmd_waktu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(datetime.datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S %Z"))

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid=update.effective_chat.id; user_skips.pop(cid,None); user_pages.pop(cid,None)
    await update.message.reply_text("🔁 Checklist direset.")

async def toggle_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid=update.effective_chat.id; cmd=update.message.text.lstrip('/').split('@')[0]; parts=cmd.split('_')
    if len(parts)==2 and parts[1] in TIMES:
        act,w=parts; on=(act=='aktifkan'); tid=update.message.message_thread_id; grp=group_reminders.setdefault(cid,{})
        grp[w]={'enabled':on,'thread_id':tid}
        # remove existing jobs by name
        for job in context.application.job_queue.jobs():
            if f"_{w}_" in job.name: job.schedule_removal()
        if on:
            for ts in TIMES[w]:
                h,m=map(int,ts.split(':'))
                data={"waktu":w,"chat_id":cid,"thread_id":tid}
                context.application.job_queue.run_daily(send_reminder,time=datetime.time(hour=h,minute=m,tzinfo=timezone),days=tuple(range(7)),name=f"r_{w}_{ts}_{cid}",data=data)
                nm=((h*60+m+20)%(24*60))//60; nn=((h*60+m+20)%(24*60))%60
                nd={"waktu":w,"jam":ts,"chat_id":cid,"thread_id":tid}
                context.application.job_queue.run_daily(notify_unchecked,time=datetime.time(hour=nm,minute=nn,tzinfo=timezone),days=tuple(range(7)),name=f"n_{w}_{ts}_{cid}",data=nd)
        await update.message.reply_text(f"✅ Pengingat {w} {'diaktifkan' if on else 'dinonaktifkan'} untuk grup.")
    else:
        await update.message.reply_text("Perintah tidak dikenali.")

async def waktu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd=update.message.text.lstrip('/').split('@')[0]
    if cmd in TIMES: await show_schedule(update,context,cmd,0)
    else: await update.message.reply_text("Perintah tidak dikenali.")

# ----------------------
# Startup & Webhook
# ----------------------
async def start_jobqueue(app):
    for cid,rem in group_reminders.items():
        for w in TIMES:
            cfg=rem.get(w)
            if not cfg or not cfg.get('enabled'): continue
            tid=cfg.get('thread_id')
            for ts in TIMES[w]:
                h,m=map(int,ts.split(':'))
                d={"waktu":w,"chat_id":cid,"thread_id":tid}
                app.job_queue.run_daily(send_reminder,time=datetime.time(hour=h,minute=m,tzinfo=timezone),days=tuple(range(7)),name=f"r_{w}_{ts}_{cid}",data=d)
                nm=((h*60+m+20)%(24*60))//60; nn=((h*60+m+20)%(24*60))%60
                nd={"waktu":w,"jam":ts,"chat_id":cid,"thread_id":tid}
                app.job_queue.run_daily(notify_unchecked,time=datetime.time(hour=nm,minute=nn,tzinfo=timezone),days=tuple(range(7)),name=f"n_{w}_{ts}_{cid}",data=nd)
    await app.job_queue.start(); logger.info("JobQueue started")

async def handle_root(request): return web.Response(text="Bot running")
async def handle_webhook(request):
    data=await request.json(); app=request.app['application']; upd=Update.de_json(data,app.bot)
    await app.update_queue.put(upd); return web.Response()

async def main():
    app=ApplicationBuilder().token(token).persistence(persistence).post_init(start_jobqueue).build()
    app.add_handler(CommandHandler("start",start_cmd))
    app.add_handler(CommandHandler(["aktifkan_pagi","aktifkan_siang","aktifkan_malam","nonaktifkan_pagi","nonaktifkan_siang","nonaktifkan_malam"],toggle_reminder_cmd))
    app.add_handler(CommandHandler(["pagi","siang","malam"],waktu_cmd))
    app.add_handler(CommandHandler("reset",reset_cmd))
    app.add_handler(CommandHandler("waktu",cmd_waktu))
    app.add_handler(CallbackQueryHandler(button))
    web_app=web.Application(); web_app['application']=app
    web_app.add_routes([web.get('/',handle_root),web.post(WEBHOOK_PATH,handle_webhook)])
    if WEBHOOK_URL: await app.bot.set_webhook(WEBHOOK_URL)
    runner=web.AppRunner(web_app); await runner.setup(); await web.TCPSite(runner,'0.0.0.0',PORT).start()
    await app.initialize(); await app.start();
    while True: await asyncio.sleep(3600)

if __name__=='__main__': asyncio.run(main())
