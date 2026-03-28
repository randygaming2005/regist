# =======================
# FINAL STABLE FULL VERSION
# =======================

import logging
import datetime
import pytz
import os
import asyncio
import re
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)

# ----------------------
# Logging & Config
# ----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TOKEN")
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
PORT = int(os.environ.get("PORT", 8000))
TIMEZONE = pytz.timezone(os.environ.get("TZ", "Asia/Jakarta"))

# ----------------------
# Constants
# ----------------------
SUBMENUS = ["DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG"]

TIMES = {
    "pagi":  ["08:00","09:00","10:00","11:00","12:00","13:00","14:00"],
    "siang": ["16:00","17:00","18:00","19:00","20:00","21:00","22:00"],
    "malam": ["00:00","01:00","02:00","03:00","04:00","05:00","06:00"],
}

RESET_TIMES   = {"pagi":"07:00","siang":"15:00","malam":"23:00"}
PREP_TIMES    = {"pagi":"07:50","siang":"15:50","malam":"23:50"}
SUMMARY_TIMES = {"pagi":"14:30","siang":"22:30","malam":"06:30"}

SHIFTS_ORDER = ["pagi","malam","siang"]
EPOCH_DATE = datetime.date(2026,3,23)

# ----------------------
# SHIFT LOGIC
# ----------------------
def get_shift_info(now):
    if now.hour < 7:
        now -= datetime.timedelta(days=1)
    days = (now.date() - EPOCH_DATE).days
    return SHIFTS_ORDER[(days//7)%3]

def get_target_datetime(jam_str, now):
    h,m = map(int, jam_str.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return target

# ----------------------
# SAFE SEND
# ----------------------
async def safe_send(bot, **kwargs):
    try:
        await bot.send_message(**kwargs)
    except Exception as e:
        logger.error(f"Send error: {e}")

# ----------------------
# JOBS
# ----------------------
async def job_reset(ctx):
    cd = ctx.application.chat_data[ctx.job.data["cid"]]
    cd["skips"] = set()
    cd["history"] = []

async def job_prep(ctx):
    d = ctx.job.data
    await safe_send(ctx.bot,
        chat_id=d["cid"],
        message_thread_id=d["tid"],
        text=f"🚀 SHIFT {d['shift']} DIMULAI"
    )

async def job_start(ctx):
    d = ctx.job.data
    await safe_send(ctx.bot,
        chat_id=d["cid"],
        message_thread_id=d["tid"],
        text=f"⏰ Jadwal {d['jam']} dibuka"
    )

async def job_warn(ctx):
    d = ctx.job.data
    cd = ctx.application.chat_data.get(d["cid"],{})
    skips = set(cd.get("skips",[]))
    missing = [s for s in SUBMENUS if f"{s}_{d['jam']}" not in skips]
    if missing:
        await safe_send(ctx.bot,
            chat_id=d["cid"],
            message_thread_id=d["tid"],
            text=f"⚠️ Belum: {', '.join(missing)}"
        )

async def job_summary(ctx):
    d = ctx.job.data
    await safe_send(ctx.bot,
        chat_id=d["cid"],
        message_thread_id=d["tid"],
        text="📊 Shift selesai"
    )

# ----------------------
# SCHEDULER (STABLE)
# ----------------------
def schedule_jobs(job_queue, chat_id, thread_id):
    for j in job_queue.jobs():
        if j.name and j.name.endswith(f"_{chat_id}"):
            j.schedule_removal()

    for shift in TIMES:
        # RESET
        h,m = map(int, RESET_TIMES[shift].split(":"))
        job_queue.run_daily(job_reset,
            time=datetime.time(h,m,tzinfo=TIMEZONE),
            name=f"reset_{shift}_{chat_id}",
            data={"cid":chat_id}
        )

        # PREP
        h,m = map(int, PREP_TIMES[shift].split(":"))
        job_queue.run_daily(job_prep,
            time=datetime.time(h,m,tzinfo=TIMEZONE),
            name=f"prep_{shift}_{chat_id}",
            data={"cid":chat_id,"tid":thread_id,"shift":shift}
        )

        # SUMMARY
        h,m = map(int, SUMMARY_TIMES[shift].split(":"))
        job_queue.run_daily(job_summary,
            time=datetime.time(h,m,tzinfo=TIMEZONE),
            name=f"sum_{shift}_{chat_id}",
            data={"cid":chat_id,"tid":thread_id,"shift":shift}
        )

        for jam in TIMES[shift]:
            h,m = map(int, jam.split(":"))

            job_queue.run_daily(job_start,
                time=datetime.time(h,m,tzinfo=TIMEZONE),
                name=f"start_{jam}_{chat_id}",
                data={"cid":chat_id,"tid":thread_id,"jam":jam}
            )

            wm = (m+20)%60
            wh = (h + (m+20)//60)%24

            job_queue.run_daily(job_warn,
                time=datetime.time(wh,wm,tzinfo=TIMEZONE),
                name=f"warn_{jam}_{chat_id}",
                data={"cid":chat_id,"tid":thread_id,"jam":jam}
            )

# ----------------------
# AUTO CHECK MESSAGE
# ----------------------
async def auto_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return

    text = (msg.text or msg.caption or "").upper()
    if "TEST DAFTAR" not in text: return

    cd = ctx.chat_data
    skips = cd.setdefault("skips", set())

    brand = re.search(r'BRAND\s*:\s*(\w+)', text)
    waktu = re.search(r'WAKTU\s*:\s*(\d{2}:\d{2})', text)

    if not (brand and waktu): return

    key = f"{brand.group(1)}_{waktu.group(1)}"

    if key not in skips:
        skips.add(key)
        await msg.reply_text(f"✅ {key} diterima")
    else:
        await msg.reply_text(f"⚠️ {key} sudah ada")

# ----------------------
# COMMANDS
# ----------------------
async def aktifkan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    tid = update.message.message_thread_id

    ctx.chat_data["thread_id"] = tid
    schedule_jobs(ctx.job_queue, cid, tid)

    await update.message.reply_text("✅ AKTIF")

# ----------------------
# WEBHOOK
# ----------------------
async def webhook(r):
    try:
        data = await r.json()
    except:
        return web.Response(status=400)

    app = r.app["app"]
    await app.update_queue.put(Update.de_json(data,app.bot))
    return web.Response()

async def root(r):
    return web.Response(text="OK")

# ----------------------
# MAIN
# ----------------------
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("aktifkan",aktifkan))
    app.add_handler(MessageHandler(filters.ALL, auto_check))

    web_app = web.Application()
    web_app["app"] = app
    web_app.add_routes([
        web.get("/",root),
        web.post(WEBHOOK_PATH,webhook)
    ])

    if WEBHOOK_URL:
        await app.bot.set_webhook(WEBHOOK_URL)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",PORT).start()

    await app.initialize()
    await app.start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
