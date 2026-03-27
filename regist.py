import asyncio
import datetime
import logging
import os
import re

import pytz
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
    Defaults,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TOKEN")
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
PORT = int(os.environ.get("PORT", 8000))
TIMEZONE = pytz.timezone(os.environ.get("TZ", "Asia/Jakarta"))

SUBMENUS = ["DWT","BG","DWL","NG","TG88","TTGL","KTT","TTGG"]

TIMES = {
    "pagi":  ["08:00","09:00","10:00","11:00","12:00","13:00","14:00"],
    "siang": ["16:00","17:00","18:00","19:00","20:00","21:00","22:00"],
    "malam": ["00:00","01:00","02:00","03:00","04:00","05:00","06:00"],
}

RESET_TIMES = {"pagi":"07:00","siang":"15:00","malam":"23:00"}
PREP_TIMES  = {"pagi":"07:50","siang":"15:50","malam":"23:50"}
SUMMARY_TIMES = {"pagi":"14:30","siang":"22:30","malam":"06:30"}

SHIFTS_ORDER = ["pagi","malam","siang"]
EPOCH_DATE = datetime.date(2026,3,23)

SHIFT_SYNC_TIME = datetime.time(hour=7,minute=1)

persistence = PicklePersistence(filepath="bot_data.pkl")

# ================= SHIFT =================
def get_shift_info(now):
    if now.hour < 7:
        now -= datetime.timedelta(days=1)
    days = (now.date() - EPOCH_DATE).days
    return SHIFTS_ORDER[(days//7)%3]

# ================= JOB CLEAN =================
def remove_jobs(job_queue, prefix):
    for j in job_queue.jobs():
        if j.name and j.name.startswith(prefix):
            j.schedule_removal()

# ================= CORE SCHEDULER =================
def schedule_jobs(app, chat_id, thread_id, force=False):
    chat_data = app.chat_data.setdefault(chat_id,{})
    now = datetime.datetime.now(TIMEZONE)
    shift = get_shift_info(now)

    old_shift = chat_data.get("scheduled_shift")

    # RESET STATE IF SHIFT CHANGED
    if old_shift != shift:
        chat_data["skips"] = set()
        chat_data["history"] = []
        chat_data.pop("schedule_msg_id",None)
        chat_data["jobs_initialized"] = False

    # PREVENT DOUBLE JOB
    if not force and chat_data.get("jobs_initialized"):
        return

    remove_jobs(app.job_queue, f"{chat_id}:")

    chat_data["scheduled_shift"] = shift
    chat_data["thread_id"] = thread_id

    # RESET
    h,m = map(int, RESET_TIMES[shift].split(":"))
    app.job_queue.run_daily(job_reset, time=datetime.time(h,m), name=f"{chat_id}:reset", data={"cid":chat_id})

    # PREP
    h,m = map(int, PREP_TIMES[shift].split(":"))
    app.job_queue.run_daily(job_prep, time=datetime.time(h,m), name=f"{chat_id}:prep", data={"cid":chat_id,"tid":thread_id,"shift":shift})

    # SUMMARY
    h,m = map(int, SUMMARY_TIMES[shift].split(":"))
    app.job_queue.run_daily(job_summary, time=datetime.time(h,m), name=f"{chat_id}:sum", data={"cid":chat_id,"tid":thread_id,"shift":shift})

    for jam in TIMES[shift]:
        h,m = map(int,jam.split(":"))

        # start -10 min (tetap sesuai logic kamu)
        sh = h-1 if h>0 else 23
        app.job_queue.run_daily(
            job_start,
            time=datetime.time(sh,50),
            name=f"{chat_id}:start:{jam}",
            data={"cid":chat_id,"tid":thread_id,"jam":jam}
        )

        # warning +20
        wm = m+20
        wh = h + wm//60
        wm %= 60
        wh %= 24

        app.job_queue.run_daily(
            job_warn,
            time=datetime.time(wh,wm),
            name=f"{chat_id}:warn:{jam}",
            data={"cid":chat_id,"tid":thread_id,"jam":jam}
        )

    # SYNC SHIFT
    app.job_queue.run_daily(
        job_sync,
        time=SHIFT_SYNC_TIME,
        name=f"sync:{chat_id}",
        data={"cid":chat_id}
    )

    chat_data["jobs_initialized"] = True
    logger.info(f"Jobs scheduled for {chat_id} shift={shift}")

# ================= JOBS =================
async def job_reset(ctx):
    cd = ctx.application.chat_data[ctx.job.data["cid"]]
    cd["skips"] = set()
    cd["history"] = []

async def job_prep(ctx):
    d = ctx.job.data
    await ctx.bot.send_message(
        chat_id=d["cid"],
        message_thread_id=d["tid"],
        text=f"🚀 SHIFT {d['shift']} DIMULAI"
    )

async def job_start(ctx):
    d = ctx.job.data
    await ctx.bot.send_message(
        chat_id=d["cid"],
        message_thread_id=d["tid"],
        text=f"⏰ Jadwal {d['jam']} dibuka"
    )

async def job_warn(ctx):
    d = ctx.job.data
    cd = ctx.application.chat_data.get(d["cid"],{})
    skips = cd.get("skips",set())
    missing = [s for s in SUBMENUS if f"{s}_{d['jam']}" not in skips]
    if missing:
        await ctx.bot.send_message(
            chat_id=d["cid"],
            message_thread_id=d["tid"],
            text=f"⚠️ Belum: {', '.join(missing)}"
        )

async def job_summary(ctx):
    d = ctx.job.data
    await ctx.bot.send_message(
        chat_id=d["cid"],
        message_thread_id=d["tid"],
        text="📊 Shift selesai"
    )

async def job_sync(ctx):
    cid = ctx.job.data["cid"]
    app = ctx.application
    cd = app.chat_data.get(cid,{})
    if not cd.get("thread_id"):
        return
    schedule_jobs(app,cid,cd["thread_id"],force=True)

# ================= COMMAND =================
async def aktifkan(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    tid = update.message.message_thread_id

    ctx.application.bot_data.setdefault("active",set()).add(cid)

    schedule_jobs(ctx.application,cid,tid,force=True)

    await update.message.reply_text("✅ AKTIF")

# ================= WEB =================
async def root(r):
    return web.Response(text="OK")

async def webhook(r):
    data = await r.json()
    app = r.app["app"]
    await app.update_queue.put(Update.de_json(data,app.bot))
    return web.Response()

# ================= MAIN =================
async def main():
    # ✅ KOMBINASI FIX
    defaults_setting = Defaults(tzinfo=TIMEZONE)

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .persistence(persistence)
        .defaults(defaults_setting)
        .build()
    )

    # ✅ WAJIB untuk JobQueue
    app.job_queue.scheduler.timezone = TIMEZONE

    app.add_handler(CommandHandler("aktifkan",aktifkan))

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
