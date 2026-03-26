import asyncio
import json
import logging
import time
import uuid
import re
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters
)
import database
from database import _data_path

SCHEDULER_DB_FILE = _data_path("scheduler_db.json")
_log = logging.getLogger(__name__)

# Conversation states
SCHED_TARGET  = 1
SCHED_MESSAGE = 2
SCHED_TIME    = 3


def load_schedule_db():
    try:
        with open(SCHEDULER_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_schedule_db(data):
    with open(SCHEDULER_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Background delivery loop ──────────────────────────────────────────────────
async def schedule_loop_task(app):
    """Runs forever in the background; fires due reminders every 15 seconds."""
    while True:
        try:
            now = time.time()
            db  = load_schedule_db()
            done = []
            for task_id, task in list(db.items()):
                if now >= task.get("trigger_at", 0):
                    try:
                        chat_id  = task["chat_id"]
                        text     = task.get("text", "")
                        photo_id = task.get("photo_id")
                        from post_builder import to_small_caps
                        text = to_small_caps(text) if text else ""
                        if photo_id:
                            await app.bot.send_photo(chat_id=chat_id, photo=photo_id,
                                                     caption=text or None, parse_mode="HTML")
                        else:
                            await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                        _log.info(f"[SCHED] Delivered task {task_id} -> {chat_id}")
                    except Exception as exc:
                        _log.error(f"[SCHED] Failed to deliver {task_id}: {exc}")
                    done.append(task_id)
            if done:
                for k in done:
                    db.pop(k, None)
                save_schedule_db(db)
        except Exception as exc:
            _log.error(f"[SCHED] Loop error: {exc}")
        await asyncio.sleep(15)


# ── Admin guard ───────────────────────────────────────────────────────────────
def _is_admin(user_id):
    from stories_bot import is_admin as _adm
    return _adm(user_id)


# ── Conversation steps ────────────────────────────────────────────────────────
async def sched_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry: /schedule command."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("No permission.")
        return ConversationHandler.END

    await update.message.reply_text(
        "<b>📅 Advanced Message Scheduler</b>\n\n"
        "Step 1 of 3 — Send the <b>target group ID or @username</b>\n"
        "Examples: <code>@MyGroup</code> or <code>-1001234567890</code>\n\n"
        "<i>Send /cancel to abort.</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    return SCHED_TARGET


async def sched_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip()
    if not target:
        await update.message.reply_text("Please send a valid group ID or @username.")
        return SCHED_TARGET

    context.user_data["sched_target"] = target
    await update.message.reply_text(
        "Step 2 of 3 — Send the <b>message</b> to schedule.\n"
        "You can send plain text, or an <b>image with a caption</b>.",
        parse_mode="HTML",
    )
    return SCHED_MESSAGE


async def sched_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.photo:
        context.user_data["sched_photo"] = msg.photo[-1].file_id
        context.user_data["sched_text"]  = msg.caption or ""
    else:
        context.user_data["sched_photo"] = None
        context.user_data["sched_text"]  = msg.text or ""

    if not context.user_data.get("sched_text") and not context.user_data.get("sched_photo"):
        await msg.reply_text("Could not read the message. Please try again.")
        return SCHED_MESSAGE

    await msg.reply_text(
        "Step 3 of 3 — When should it be sent?\n\n"
        "Examples:\n"
        "  <code>in 2 hours</code>\n"
        "  <code>in 30 mins</code>\n"
        "  <code>in 1 day</code>\n"
        "  <code>2026-04-10 15:30</code>\n"
        "  <code>15:30</code>  (today, or tomorrow if past)",
        parse_mode="HTML",
    )
    return SCHED_TIME


def _parse_time(raw: str):
    s = raw.lower().strip()
    now = datetime.now()
    if s.startswith("in "):
        s = s[3:].strip()
        m = re.search(r"[\d.]+", s)
        if not m:
            return None
        num = float(m.group())
        if any(w in s for w in ("hour", "hr")):
            return now + timedelta(hours=num)
        if "min" in s:
            return now + timedelta(minutes=num)
        if "day" in s:
            return now + timedelta(days=num)
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            if fmt == "%H:%M":
                dt = datetime.combine(now.date(), dt.time())
                if dt < now:
                    dt += timedelta(days=1)
            return dt
        except ValueError:
            pass
    return None


async def sched_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = _parse_time(update.message.text or "")
    if not dt:
        await update.message.reply_text(
            "Could not understand that time.\n"
            "Try: <code>in 2 hours</code>, <code>in 30 mins</code>, <code>2026-04-10 15:30</code>",
            parse_mode="HTML",
        )
        return SCHED_TIME

    target   = context.user_data.pop("sched_target", "")
    text     = context.user_data.pop("sched_text", "")
    photo    = context.user_data.pop("sched_photo", None)
    task_id  = str(uuid.uuid4())[:8]

    db = load_schedule_db()
    db[task_id] = {
        "chat_id":    target,
        "text":       text,
        "photo_id":   photo,
        "trigger_at": dt.timestamp(),
        "created_at": time.time(),
    }
    save_schedule_db(db)

    human = dt.strftime("%Y-%m-%d %H:%M")
    await update.message.reply_text(
        f"<b>Scheduled!</b>\n\n"
        f"Target: <code>{target}</code>\n"
        f"Send at: {human}\n"
        f"Task ID: <code>{task_id}</code>",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def sched_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("sched_target", None)
    context.user_data.pop("sched_text", None)
    context.user_data.pop("sched_photo", None)
    await update.message.reply_text("Scheduling cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ── Handler registration ──────────────────────────────────────────────────────
def get_scheduler_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("schedule", sched_start)],
        states={
            SCHED_TARGET:  [MessageHandler(filters.TEXT & ~filters.COMMAND, sched_target)],
            SCHED_MESSAGE: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, sched_message)],
            SCHED_TIME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, sched_time)],
        },
        fallbacks=[CommandHandler("cancel", sched_cancel)],
        allow_reentry=True,
    )
