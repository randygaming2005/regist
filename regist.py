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
    "pagi": ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang": ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam": ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
}

# ----------------------
# Reminder & Check Functions
# ----------------------

async def group_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Kirim pengingat 5 menit sebelum jam ke grup."""
    data = context.job.data
    chat_id = data["chat_id"]
    jam = data["jam"]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"5 menit lagi jam {jam} silahkan test daftar bagi yang bertugas....."
    )

async def check_overdue(context: ContextTypes.DEFAULT_TYPE):
    """Cek 20 menit setelah jam; DM admin jika belum marked ✅."""
    data = context.job.data
    chat_id = data["chat_id"]
    jam = data["jam"]

    msgs = []
    skips = user_skips.get(chat_id, set())
    for section in SUBMENUS:
        if f"{section}_{jam}" not in skips:
            msgs.append(f"{section} jam {jam} test register sudah melewati 20 menit masih belum di kerjakan")

    if not msgs:
        return

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for admin in admins:
            if not admin.user.is_bot:
                for msg in msgs:
                    await context.bot.send_message(admin.user.id, msg)
    except Exception as e:
        logger.error(f"Gagal mengirim DM overdue: {e}")

async def daily_summary(context: ContextTypes.DEFAULT_TYPE):
    """Kirim ringkasan harian setiap pukul 06:00 WIB."""
    yesterday = (datetime.datetime.now(timezone) - datetime.timedelta(days=1)).date()
    for chat_id in user_chats:
        skips = user_skips.get(chat_id, set())
        done = [f"{sec} {jam}" for sec in SUBMENUS for jam in TIMES["pagi"]+TIMES["siang"]+TIMES["malam"]
                if f"{sec}_{jam}" in skips]
        total = len(SUBMENUS) * sum(len(v) for v in TIMES.values())
        undone = total - len(done)
        pct = len(done) / total * 100 if total else 0
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
# Command Handlers
# ----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /start: Mulai bot dan pilih jadwal """
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
    """ /reset: Reset semua tugas & job """
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs():
        if str(chat_id) in job.name:
            job.schedule_removal()
    user_skips.pop(chat_id, None)
    await update.message.reply_text("🔁 Semua tugas dan tanda telah direset.")

async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /jadwalaktif <pagi|siang|malam>:
    Tampilkan daftar sub-menu beserta jam-jam yang masih aktif (❌).
    """
    args = context.args
    if not args or args[0] not in TIMES:
        return await update.message.reply_text("Gunakan: /jadwalaktif <pagi|siang|malam>")

    cat = args[0]
    chat_id = update.effective_chat.id
    skips = user_skips.get(chat_id, set())

    aktif = {}
    for section in SUBMENUS:
        jam_list = [jam for jam in TIMES[cat] if f"{section}_{jam}" not in skips]
        if jam_list:
            aktif[section] = jam_list

    if not aktif:
        return await update.message.reply_text(
            f"✅ Tidak ada jadwal aktif di kategori *{cat}*.",
            parse_mode="Markdown"
        )

    lines = [f"*{cat.capitalize()}* — Jadwal aktif:"]
    for section, jams in aktif.items():
        lines.append(f"• *{section}*: {', '.join(jams)}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ----------------------
# CallbackQuery Handler
# ----------------------

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    # 1) Main menu
    if data.startswith("main_"):
        waktu = data.split("_", 1)[1]
        btn_on  = InlineKeyboardButton(f"🔔 Aktifkan {waktu.capitalize()}", callback_data=f"activate_{waktu}")
        btn_rst = InlineKeyboardButton(f"🔁 Reset {waktu.capitalize()}",    callback_data=f"reset_{waktu}")
        keyboard = [[btn_on], [btn_rst]]
        for i in range(0, len(SUBMENUS), 4):
            row = [InlineKeyboardButton(name, callback_data=f"sub_{waktu}_{name}")
                   for name in SUBMENUS[i:i+4]]
            keyboard.append(row)
        return await query.edit_message_text(
            f"⏰ Kategori: *{waktu.capitalize()}*\nPilih bagian, Aktifkan atau Reset:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # 2) Submenu toggle
    if data.startswith("sub_"):
        _, waktu, section = data.split("_", 2)
        marks = user_skips.get(chat_id, set())
        keyboard = []
        for i in range(0, len(TIMES[waktu]), 3):
            row = []
            for jam in TIMES[waktu][i:i+3]:
                sym = "✅" if f"{section}_{jam}" in marks else "❌"
                row.append(InlineKeyboardButton(f"{jam} {sym}", callback_data=f"toggle_{waktu}_{section}_{jam}"))
            keyboard.append(row)
        return await query.edit_message_text(
            f"🗂 Bagian: *{section}*\nKlik untuk toggle ❌/✅:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # 3) Toggle skip flag
    if data.startswith("toggle_"):
        _, waktu, section, jam = data.split("_", 3)
        key = f"{section}_{jam}"
        marks = user_skips.setdefault(chat_id, set())
        if key in marks:
            marks.remove(key)
        else:
            marks.add(key)

        # rebuild submenu keyboard
        new_rows = []
        for i in range(0, len(TIMES[waktu]), 3):
            row = []
            for jm in TIMES[waktu][i:i+3]:
                sym2 = "✅" if f"{section}_{jm}" in marks else "❌"
                row.append(InlineKeyboardButton(f"{jm} {sym2}", callback_data=f"toggle_{waktu}_{section}_{jm}"))
            new_rows.append(row)

        return await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(new_rows)
        )

    # 4) Activate all
    if data.startswith("activate_"):
        waktu = data.split("_", 1)[1]
        skips = user_skips.get(chat_id, set())
        jam_set = {
            jam for section in SUBMENUS
            for jam in TIMES[waktu]
            if f"{section}_{jam}" not in skips
        }

        now = datetime.datetime.now(timezone)
        for jam in jam_set:
            h, m = map(int, jam.split(":"))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target < now:
                target += datetime.timedelta(days=1)

            # 4.1) schedule group_reminder 5 menit sebelum
            rem_time = target - datetime.timedelta(minutes=5)
            rem_utc = rem_time.astimezone(pytz.utc).time()
            name_gr = f"grp_{chat_id}_{jam.replace(':','')}"
            for old in context.job_queue.get_jobs_by_name(name_gr):
                old.schedule_removal()
            context.job_queue.run_daily(
                group_reminder,
                time=rem_utc,
                name=name_gr,
                data={"chat_id": chat_id, "jam": jam}
            )

            # 4.2) schedule check_overdue 20 menit setelah
            overdue_time = target + datetime.timedelta(minutes=20)
            delay = (overdue_time - now).total_seconds()
            name_co = f"chk_{chat_id}_{jam.replace(':','')}"
            for old in context.job_queue.get_jobs_by_name(name_co):
                old.schedule_removal()
            if delay > 0:
                context.job_queue.run_once(
                    check_overdue,
                    when=delay,
                    name=name_co,
                    data={"chat_id": chat_id, "jam": jam}
                )

        return await query.edit_message_text(
            f"✅ Reminder *{waktu.capitalize()}* diaktifkan untuk {len(jam_set)} jam.",
            parse_mode="Markdown"
        )

    # 5) Reset per kategori
    if data.startswith("reset_"):
        waktu = data.split("_", 1)[1]
        removed = 0
        for jam in TIMES[waktu]:
            for prefix in ("grp", "chk"):
                job_name = f"{prefix}_{chat_id}_{jam.replace(':','')}"
                for job in context.job_queue.get_jobs_by_name(job_name):
                    job.schedule_removal()
                    removed += 1
        # optional: clear skips for that category
        user_skips.pop(chat_id, None)
        return await query.edit_message_text(
            f"🔁 Reminder *{waktu.capitalize()}* direset.\n"
            f"Total job dibatalkan: *{removed}*",
            parse_mode="Markdown"
        )

# ----------------------
# Webhook & App Setup
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
    app.add_error_handler(lambda u, c: logger.error("Error handling update", exc_info=c.error))

    # start application & job queue
    await app.initialize()
    await app.start()
    await app.job_queue.start()

    # schedule daily summary at 06:00 WIB
    app.job_queue.run_daily(
        daily_summary,
        time=datetime.time(hour=6, minute=0, tzinfo=timezone),
        name="daily_summary"
    )

    # webhook server
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
    port = int(os.environ.get("PORT", 8000))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    # keep alive
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
