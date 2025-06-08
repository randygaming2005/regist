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

TOKEN = os.environ.get("TOKEN")
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
PORT = int(os.environ.get("PORT", 8000))

# ----------------------
# Persistence & State
# ----------------------
persistence = PicklePersistence(filepath="reminder_data.pkl")
user_skips = {}    # chat_id → set of "<section>_<jam>"
user_pages = {}    # chat_id → current page per category

timezone = pytz.timezone("Asia/Jakarta")

# ----------------------
# Submenus & Times
# ----------------------
SUBMENUS = [
    "DWT", "BG", "DWL", "DST", "KRM", "BRK", "PRW", "RJN", "STP", "PNR", 
    "NMR", "CKL", "KRT", "LKS", "JMP", "TRG", "SJR", "GNG", "MTN", "BDN"
]
TIMES = {
    "pagi":   ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang":  ["15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00"],
    "malam":  ["23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00"]
}

PAGE_SIZE = 5

# ----------------------
# Helper: Show Schedule with Pagination
# ----------------------
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, waktu="pagi", page=0):
    chat_id = update.effective_chat.id
    skips = user_skips.get(chat_id, set())
    user_pages[chat_id] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    current_submenus = SUBMENUS[start:end]

    lines = [f"*\u23f0 Jadwal Kategori: {waktu.capitalize()} (Halaman {page+1}/{(len(SUBMENUS)-1)//PAGE_SIZE+1})*\n"]
    for sec in current_submenus:
        jam_status = []
        for jam in TIMES[waktu]:
            sym = "\u2705" if f"{sec}_{jam}" in skips else "\u274c"
            jam_status.append(f"{jam} {sym}")
        lines.append(f"*{sec}*:\n{'   '.join(jam_status)}")

    buttons = []
    for sec in current_submenus:
        for jam in TIMES[waktu]:
            key = f"{sec}_{jam}"
            sym = "\u2705" if key in skips else "\u274c"
            buttons.append(
                InlineKeyboardButton(
                    f"{sec[:4]} {jam} {sym}",
                    callback_data=f"toggle_{waktu}_{sec}_{jam}_{page}"
                )
            )
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("\u2b05\ufe0f Sebelumnya", callback_data=f"nav_{waktu}_{page-1}"))
    if end < len(SUBMENUS):
        nav.append(InlineKeyboardButton("\u27a1\ufe0f Selanjutnya", callback_data=f"nav_{waktu}_{page+1}"))
    if nav:
        rows.append(nav)

    text = "\n".join(lines)
    if update.message:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )
    else:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )

# ----------------------
# CallbackQuery Handler
# ----------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("toggle_"):
        _, waktu, sec, jam, page = data.split("_", 4)
        page = int(page)
        chat_id = query.message.chat.id
        key = f"{sec}_{jam}"
        skips = user_skips.setdefault(chat_id, set())
        if key in skips:
            skips.remove(key)
        else:
            skips.add(key)
        await show_schedule(update, context, waktu=waktu, page=page)

    elif data.startswith("nav_"):
        _, waktu, page = data.split("_", 2)
        await show_schedule(update, context, waktu=waktu, page=int(page))

# ----------------------
# Commands
# ----------------------
async def cmd_pagi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_schedule(update, context, waktu="pagi", page=0)

async def cmd_siang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_schedule(update, context, waktu="siang", page=0)

async def cmd_malam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_schedule(update, context, waktu="malam", page=0)

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_skips.pop(chat_id, None)
    user_pages.pop(chat_id, None)
    await update.message.reply_text("\ud83d\udd01 Semua tanda tugas telah direset.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "\ud83d\udc4b Selamat datang!\n\n"
        "Gunakan perintah berikut:\n"
        "/pagi - Jadwal Pagi\n"
        "/siang - Jadwal Siang\n"
        "/malam - Jadwal Malam\n"
        "/reset - Reset checklist\n"
    )
    await update.message.reply_text(text)

# ----------------------
# Webhook & App Initialization
# ----------------------
async def handle_root(request):
    return web.Response(text="Bot is running")

async def handle_webhook(request):
    app = request.app['application']
    data = await request.json()
    upd = Update.de_json(data, app.bot)
    await app.update_queue.put(upd)
    return web.Response()

async def main():
    app = ApplicationBuilder().token(TOKEN).persistence(persistence).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pagi", cmd_pagi))
    app.add_handler(CommandHandler("siang", cmd_siang))
    app.add_handler(CommandHandler("malam", cmd_malam))
    app.add_handler(CommandHandler("reset", reset_all))
    app.add_handler(CallbackQueryHandler(button_handler))

    await app.initialize()
    await app.start()
    await app.job_queue.start()

    web_app = web.Application()
    web_app['application'] = app
    web_app.add_routes([
        web.get('/', handle_root),
        web.post(WEBHOOK_PATH, handle_webhook)
    ])

    if WEBHOOK_URL:
        await app.bot.set_webhook(WEBHOOK_URL)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
