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
from collections import deque

# ----------------------
# Logging & Konfigurasi
# ----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------
# Variabel Lingkungan
# ----------------------
token = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_PATH = f"/{token}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
PORT = int(os.environ.get("PORT", 8000))

# zona waktu
timezone = pytz.timezone(os.environ.get("TZ", "Asia/Jakarta"))

# ----------------------
# Persistence & State
# ----------------------
persistence = PicklePersistence(filepath="bot_data.pkl")
# Tidak perlu simpan user_skips global lagi, gunakan context.chat_data
group_reminders = {}  # chat_id → {waktu: {enabled, thread_id}}

# ----------------------
# Jadwal & Waktu Pengingat
# ----------------------
SUBMENUS = ["DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG"]
TIMES = {
    "pagi": ["08:00","09:00","10:00","11:00","12:00","13:00","14:00"],
    "siang": ["16:00","17:00","18:00","19:00","20:00","21:00","22:00"],
    "malam": ["00:00","01:00","02:00","03:00","04:00","05:00","06:00"],
}
PAGE_SIZE = 10

generic_reminder = {
    "pagi": "🌅 Selamat pagi! Cek jadwal dengan /pagi.",
    "siang": "🌞 Selamat siang! Cek jadwal dengan /siang.",
    "malam": "🌙 Selamat malam! Cek jadwal dengan /malam.",
}

# ----------------------
# Tampilkan Jadwal dengan Tombol
# ----------------------
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, waktu="pagi", page=0):
    chat_id = update.effective_chat.id
    context.chat_data.setdefault('pages', {})[waktu] = page
    skips = context.chat_data.setdefault('skips', set())

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    subs = SUBMENUS[start:end]

    text = f"*Jadwal {waktu.capitalize()}*"
    rows = []

    for sec in subs:
        # tombol utama per sektion
        first_jam = TIMES[waktu][0]
        key = f"{sec}_{first_jam}"
        sym = '✅' if key in skips else '❌'
        rows.append([InlineKeyboardButton(f"{sec} {first_jam} {sym}", callback_data=f"toggle_{waktu}_{sec}_{first_jam}_{page}")])
        # tombol kecil per jam
        small = []
        for jam in TIMES[waktu][1:]:
            key2 = f"{sec}_{jam}"
            sym2 = '✅' if key2 in skips else '❌'
            small.append(InlineKeyboardButton(f"{jam} {sym2}", callback_data=f"toggle_{waktu}_{sec}_{jam}_{page}"))
        for i in range(0, len(small), 3): rows.append(small[i:i+3])

    # tombol CLEAR
    rows.append([InlineKeyboardButton("♻️ CLEAR", callback_data=f"clear_{waktu}_{page}")])
    # navigasi halaman
    nav = []
    total = len(SUBMENUS)//PAGE_SIZE
    if page>0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"nav_{waktu}_{page-1}"))
    if page<total: nav.append(InlineKeyboardButton("➡️", callback_data=f"nav_{waktu}_{page+1}"))
    if nav: rows.append(nav)

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    else:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

# ----------------------
# Handler Callback Tombol
# ----------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user = q.from_user.full_name or q.from_user.username
    timestamp = datetime.datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S")
    history = context.chat_data.setdefault('history', deque(maxlen=15))
    skips = context.chat_data.setdefault('skips', set())

    if data.startswith("toggle_"):
        _, w, sec, j, pg = data.split("_",4)
        key = f"{sec}_{j}"
        action = 'menghapus' if key in skips else 'menambahkan'
        if key in skips: skips.remove(key)
        else: skips.add(key)
        history.append(f"{timestamp} - {user} {action} tanda untuk {sec} jam {j}")
        await show_schedule(update, context, waktu=w, page=int(pg))
    elif data.startswith("clear_"):
        _, w, pg = data.split("_",2)
        history.append(f"{timestamp} - {user} mengosongkan semua tanda untuk {w}")
        context.chat_data['skips'] = set()
        context.chat_data['pages'] = {w:0}
        await show_schedule(update, context, waktu=w, page=int(pg))
    elif data.startswith("nav_"):
        _, w, pg = data.split("_",2)
        await show_schedule(update, context, waktu=w, page=int(pg))

# ----------------------
# Pengingat
# ----------------------
async def send_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.job.data
    w = d['waktu']; cid = d['chat_id']; tid = d.get('thread_id')
    now = datetime.datetime.now(timezone).strftime("%H:%M")
    await ctx.bot.send_message(chat_id=cid, message_thread_id=tid,
        text=f"{generic_reminder[w]}\n🕒 {now}", parse_mode="Markdown")

async def notify_unchecked(ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.job.data
    w,j,cid,tid = d['waktu'], d['jam'], d['chat_id'], d.get('thread_id')
    skips = context.chat_data.get('skips', set())
    incomplete = [s for s in SUBMENUS if f"{s}_{j}" not in skips]
    if incomplete:
        await ctx.bot.send_message(chat_id=cid, message_thread_id=tid,
            text=(f"⚠️ Jadwal *{w}* jam *{j}* belum lengkap: {' ,'.join(incomplete)}"), parse_mode="Markdown")

# ----------------------
# Command Handlers
# ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Selamat datang di Bot Jadwal!\n"
        "Gunakan /pagi, /siang, atau /malam untuk lihat jadwal.\n"
        "Tombol akan menandai checklist, dan /history untuk lihat riwayat.\n"
        "/reset untuk reset tanda dan riwayat."        
    )
    await update.message.reply_text(msg)

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hist = context.chat_data.get('history', [])
    if not hist:
        await update.message.reply_text("📜 Belum ada riwayat.")
    else:
        teks = "*Riwayat Edit:*\n" + "\n".join(f"{i+1}. {l}" for i,l in enumerate(hist))
        await update.message.reply_text(teks, parse_mode="Markdown")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.pop('skips', None)
    context.chat_data.pop('history', None)
    context.chat_data.pop('pages', None)
    await update.message.reply_text("🔁 Checklist dan riwayat berhasil direset.")

# ----------------------
# Setup & Main
# ----------------------
async def main():
    app = ApplicationBuilder().token(token).persistence(persistence).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler(["pagi","siang","malam"], show_schedule_wrapper))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CallbackQueryHandler(button))

    async def webhook(request):
        data = await request.json()
        upd = Update.de_json(data, app.bot)
        await app.update_queue.put(upd)
        return web.Response()

    web_app = web.Application()
    web_app['application'] = app
    web_app.add_routes([web.get('/', lambda r: web.Response(text="Bot jalan")),
                        web.post(WEBHOOK_PATH, webhook)])
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    if WEBHOOK_URL: await app.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Bot beroperasi. Webhook: {WEBHOOK_URL}")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

if __name__ == '__main__':
    asyncio.run(main())
