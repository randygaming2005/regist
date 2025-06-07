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

# ----------------------
# Logging & Config
# ----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None

# ----------------------
# Persistence & State
# ----------------------
persistence = PicklePersistence(filepath="reminder_data.pkl")
user_skips = {}    # chat_id → set of "<section>_<jam>"
user_chats = set() # track chats where bot digunakan
timezone = pytz.timezone("Asia/Jakarta")

# ----------------------
# Submenus & Times
# ----------------------
SUBMENUS = [
    "DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG", "MGHK", "AREA",
    "PWNT", "KST", "KINGJR", "VITO", "HOLY", "INDOGG", "DRAGON", "CEME", "IDN", "CITI"
]
TIMES = {
    "pagi":   ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang":  ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam":  ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
}

# ----------------------
# Reminder & Check
# ----------------------

async def group_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    await context.bot.send_message(
        chat_id=data["chat_id"],
        text=f"5 menit lagi jam {data['jam']} silahkan test daftar bagi yang bertugas....."
    )

async def check_overdue(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    jam = data["jam"]
    skips = user_skips.get(chat_id, set())
    msgs = [
        f"{sec} jam {jam} test register sudah melewati 20 menit masih belum di kerjakan"
        for sec in SUBMENUS
        if f"{sec}_{jam}" not in skips
    ]
    if not msgs:
        return
    admins = await context.bot.get_chat_administrators(chat_id)
    for adm in admins:
        if not adm.user.is_bot:
            for m in msgs:
                await context.bot.send_message(adm.user.id, m)

async def daily_summary(context: ContextTypes.DEFAULT_TYPE):
    yesterday = (datetime.datetime.now(timezone) - datetime.timedelta(days=1)).date()
    for chat_id in user_chats:
        skips = user_skips.get(chat_id, set())
        total = len(SUBMENUS) * sum(len(v) for v in TIMES.values())
        done = [
            f"{sec} {jam}"
            for sec in SUBMENUS
            for jam in (j for lst in TIMES.values() for j in lst)
            if f"{sec}_{jam}" in skips
        ]
        undone = total - len(done)
        pct = (len(done) / total * 100) if total else 0.0
        msg = (
            f"📋 Ringkasan tugas {yesterday}\n"
            f"Selesai (✅): {len(done)}\n"
            f"Belum selesai (❌): {undone}\n"
            f"Ketepatan waktu: {pct:.1f}%\n\n"
            f"Contoh selesai: {', '.join(done[:5])}\n"
            f"Contoh belum: {', '.join([f for f in done[:5]])}"
        )
        await context.bot.send_message(chat_id=chat_id, text=msg)

# ----------------------
# Commands
# ----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_chats.add(chat_id)
    kb = [[
        InlineKeyboardButton("Pagi", callback_data="main_pagi"),
        InlineKeyboardButton("Siang", callback_data="main_siang"),
        InlineKeyboardButton("Malam", callback_data="main_malam"),
    ]]
    await update.message.reply_text("🕒 Mulai bot dan pilih jadwal:", reply_markup=InlineKeyboardMarkup(kb))

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs():
        if str(chat_id) in job.name:
            job.schedule_removal()
    user_skips.pop(chat_id, None)
    await update.message.reply_text("🔁 Semua tugas dan tanda telah direset.")

async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /jadwalaktif <pagi|siang|malam>:
    Tampilkan tiap sub-menu dengan daftar jam dan status ❌/✅.
    """
    args = context.args
    if not args or args[0] not in TIMES:
        return await update.message.reply_text("Gunakan: /jadwalaktif <pagi|siang|malam>")

    cat = args[0]
    chat_id = update.effective_chat.id
    skips = user_skips.get(chat_id, set())

    lines = [f"*{cat.capitalize()}* — Jadwal & Status:"]
    for sec in SUBMENUS:
        jam_status = []
        for jam in TIMES[cat]:
            sym = "✅" if f"{sec}_{jam}" in skips else "❌"
            jam_status.append(f"{jam}{sym}")
        lines.append(f"• *{sec}*: " + ", ".join(jam_status))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ----------------------
# CallbackQuery Handler
# ----------------------

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    # Kembali ke start
    if data == "go_start":
        return await start(update, context)

    # 1) Menu kategori
    if data.startswith("main_"):
        waktu = data.split("_",1)[1]
        kb = [
            [InlineKeyboardButton(f"🔔 Aktifkan {waktu.capitalize()}", callback_data=f"activate_{waktu}")],
            [InlineKeyboardButton(f"🔁 Reset {waktu.capitalize()}",    callback_data=f"reset_{waktu}")]
        ]
        for i in range(0, len(SUBMENUS), 4):
            kb.append([InlineKeyboardButton(name, callback_data=f"sub_{waktu}_{name}") for name in SUBMENUS[i:i+4]])
        kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="go_start")])
        return await query.edit_message_text(
            f"⏰ Kategori: *{waktu.capitalize()}*\nPilih bagian…",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )

    # 2) Submenu bagian
    if data.startswith("sub_"):
        _, waktu, sec = data.split("_",2)
        marks = user_skips.get(chat_id, set())
        kb = []
        for i in range(0, len(TIMES[waktu]), 3):
            row=[]
            for jam in TIMES[waktu][i:i+3]:
                sym = "✅" if f"{sec}_{jam}" in marks else "❌"
                row.append(InlineKeyboardButton(f"{jam} {sym}", callback_data=f"toggle_{waktu}_{sec}_{jam}"))
            kb.append(row)
        kb.append([InlineKeyboardButton("🔙 Kembali", callback_data=f"main_{waktu}")])
        return await query.edit_message_text(
            f"🗂 Bagian: *{sec}*\nKlik untuk toggle ❌/✅:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )

    # 3) Toggle skip
    if data.startswith("toggle_"):
        _, waktu, sec, jam = data.split("_",3)
        key = f"{sec}_{jam}"
        marks = user_skips.setdefault(chat_id, set())
        if key in marks:
            marks.remove(key)
        else:
            marks.add(key)
        # rebuild same submenu
        kb=[]
        for i in range(0, len(TIMES[waktu]),3):
            row=[]
            for jm in TIMES[waktu][i:i+3]:
                sym = "✅" if f"{sec}_{jm}" in marks else "❌"
                row.append(InlineKeyboardButton(f"{jm} {sym}", callback_data=f"toggle_{waktu}_{sec}_{jm}"))
            kb.append(row)
        kb.append([InlineKeyboardButton("🔙 Kembali", callback_data=f"main_{waktu}")])
        return await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(kb)
        )

    # 4) Activate all
    if data.startswith("activate_"):
        waktu = data.split("_",1)[1]
        skips = user_skips.get(chat_id, set())
        jam_set = {
            jam for sec in SUBMENUS for jam in TIMES[waktu]
            if f"{sec}_{jam}" not in skips
        }
        now = datetime.datetime.now(timezone)
        for jam in jam_set:
            h,m = map(int, jam.split(":"))
            tgt = now.replace(hour=h,minute=m,second=0,microsecond=0)
            if tgt < now: tgt += datetime.timedelta(days=1)
            # 5-min reminder
            rem = tgt - datetime.timedelta(minutes=5)
            rem_utc = rem.astimezone(pytz.utc).time()
            name_g = f"grp_{chat_id}_{jam.replace(':','')}"
            for old in context.job_queue.get_jobs_by_name(name_g): old.schedule_removal()
            context.job_queue.run_daily(group_reminder, time=rem_utc, name=name_g, data={"chat_id":chat_id,"jam":jam})
            # overdue check
            od = tgt + datetime.timedelta(minutes=20)
            delay = (od - now).total_seconds()
            name_c = f"chk_{chat_id}_{jam.replace(':','')}"
            for old in context.job_queue.get_jobs_by_name(name_c): old.schedule_removal()
            if delay>0:
                context.job_queue.run_once(check_overdue, when=delay, name=name_c, data={"chat_id":chat_id,"jam":jam})
        # back button
        kb = [[InlineKeyboardButton("🔙 Kembali", callback_data=f"main_{waktu}")]]
        return await query.edit_message_text(
            f"✅ Reminder *{waktu.capitalize()}* diaktifkan untuk {len(jam_set)} jam.",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )

    # 5) Reset per kategori
    if data.startswith("reset_"):
        waktu = data.split("_",1)[1]
        removed=0
        for jam in TIMES[waktu]:
            for pfx in ("grp","chk"):
                for job in context.job_queue.get_jobs_by_name(f"{pfx}_{chat_id}_{jam.replace(':','')}"):
                    job.schedule_removal(); removed+=1
        user_skips.pop(chat_id, None)
        kb = [[InlineKeyboardButton("🔙 Kembali", callback_data="go_start")]]
        return await query.edit_message_text(
            f"🔁 Reminder *{waktu.capitalize()}* direset.\nTotal job dibatalkan: *{removed}*",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )

# ----------------------
# Webhook & App
# ----------------------

async def handle_root(request):
    return web.Response(text="Bot is running")

async def handle_webhook(request):
    app = request.app["application"]
    data = await request.json()
    upd = Update.de_json(data, app.bot)
    await app.update_queue.put(upd)
    return web.Response()

async def main():
    app = ApplicationBuilder().token(TOKEN).persistence(persistence).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_all))
    app.add_handler(CommandHandler("jadwalaktif", show_schedule))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(lambda u,c: logger.error("Unhandled error", exc_info=c.error))

    await app.initialize(); await app.start(); await app.job_queue.start()

    # Jadwal ringkasan harian 06:00 WIB
    app.job_queue.run_daily(
        daily_summary,
        time=datetime.time(hour=6,minute=0,tzinfo=timezone),
        name="daily_summary"
    )

    web_app = web.Application()
    web_app["application"] = app
    web_app.add_routes([
        web.get("/", handle_root),
        web.post(WEBHOOK_PATH, handle_webhook),
    ])
    if WEBHOOK_URL:
        await app.bot.set_webhook(WEBHOOK_URL)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT",8000))).start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
