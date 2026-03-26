import asyncio
import json
import logging
import time
import uuid
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
import database

# Use the same generic import for configuration
from database import _data_path
SCHEDULER_DB_FILE = _data_path("scheduler_db.json")

_log = logging.getLogger(__name__)

# State constants
SCHED_TARGET = 1
SCHED_MESSAGE = 2
SCHED_TIME = 3

def load_schedule_db():
    try:
        with open(SCHEDULER_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_schedule_db(data):
    with open(SCHEDULER_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

# ----------------------------------------------------------------------------
# The Check Loop
# ----------------------------------------------------------------------------
async def schedule_loop_task(app):
    while True:
        try:
            now = time.time()
            db = load_schedule_db()
            to_delete = []
            
            for task_id, task in list(db.items()):
                if now >= task.get("trigger_at", 0):
                    # Trigger this message!
                    try:
                        chat_id = task["chat_id"]
                        text = task.get("text", "")
                        photo_id = task.get("photo_id")
                        
                        # Convert user text into its own Unicode/font style
                        from post_builder import to_small_caps
                        text = to_small_caps(text)
                        
                        kwargs = {}
                        if photo_id:
                            await app.bot.send_photo(chat_id=chat_id, photo=photo_id, caption=text, parse_mode="HTML")
                        else:
                            await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                        _log.info(f"Scheduled message {task_id} sent successfully to {chat_id}")
                    except Exception as e:
                        _log.error(f"Failed to send scheduled message {task_id}: {e}")
                    
                    to_delete.append(task_id)
            
            if to_delete:
                for k in to_delete:
                    del db[k]
                save_schedule_db(db)
                
        except Exception as e:
            _log.error(f"Scheduler loop error: {e}")
            
        await asyncio.sleep(15)

# ----------------------------------------------------------------------------
# Conversation Callbacks
# ----------------------------------------------------------------------------
def is_admin(user_id):
    from stories_bot import is_admin as is_adm
    return is_adm(user_id)

async def sched_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for the scheduling system."""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
        msg = query.message
    else:
        user_id = update.effective_user.id
        msg = update.message
        
    if not is_admin(user_id):
        return ConversationHandler.END

    text = (
        "<b>📅 Advanced Message Scheduler</b>\n\n"
        "Let's schedule a message.\n"
        "Please send the <b>target group ID or username</b> (e.g. <code>@MyGroup</code> or <code>-1001234567</code>) where you want the reminder sent.\n\n"
        "<i>Or send /cancel to abort.</i>"
    )
    if query:
        await msg.reply_text(text, parse_mode="HTML")
    else:
        await msg.reply_text(text, parse_mode="HTML")
    return SCHED_TARGET

async def sched_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip()
    if not target:
        await update.message.reply_text("Please provide a valid text ID/username.")
        return SCHED_TARGET
        
    context.user_data["sched_target"] = target
    
    text = (
        "✅ Target selected.\n\n"
        "Now, send the <b>message</b> you want to schedule.\n"
        "It can be plain text, or an image with a caption."
    )
    await update.message.reply_text(text, parse_mode="HTML")
    return SCHED_MESSAGE

async def sched_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if msg.photo:
        context.user_data["sched_photo"] = msg.photo[-1].file_id
        context.user_data["sched_text"] = msg.caption or ""
    else:
        context.user_data["sched_photo"] = None
        context.user_data["sched_text"] = msg.text or ""
        
    if not context.user_data["sched_text"] and not context.user_data["sched_photo"]:
        await msg.reply_text("Could not read message. Please try again.")
        return SCHED_MESSAGE

    text = (
        "✅ Message captured.\n\n"
        "Finally, when should this be sent?\n"
        "You can say things like:\n"
        "• <code>in 2 hours</code>\n"
        "• <code>in 30 mins</code>\n"
        "• <code>in 1 day</code>\n"
        "• <code>2026-04-10 15:30</code>"
    )
    await msg.reply_text(text, parse_mode="HTML")
    return SCHED_TIME

def parse_time_str(time_str: str):
    time_str = time_str.lower().strip()
    now = datetime.now()
    
    # Relative formats: "in X hours/mins"
    if time_str.startswith("in "):
        time_str = time_str[3:].strip()
        num = float(re.search(r'\d+', time_str).group())
        if "hour" in time_str or "hr" in time_str or "h" in time_str:
            return now + timedelta(hours=num)
        elif "min" in time_str or "m" in time_str:
            return now + timedelta(minutes=num)
        elif "day" in time_str or "d" in time_str:
            return now + timedelta(days=num)
            
    # Absolute formats: "YYYY-MM-DD HH:MM"
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M")
    except ValueError:
        pass
        
    # Just HH:MM today
    try:
        t = datetime.strptime(time_str, "%H:%M").time()
        dt = datetime.combine(now.date(), t)
        if dt < now: dt += timedelta(days=1)
        return dt
    except ValueError:
        pass
        
    return None

async def sched_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text.strip()
    dt = parse_time_str(time_str)
    
    if not dt:
        await update.message.reply_text(
            "❌ I couldn't understand that time format.\n"
            "Try <code>in 2 hours</code>, <code>in 45 mins</code>, or <code>YYYY-MM-DD HH:MM</code>."
            , parse_mode="HTML"
        )
        return SCHED_TIME
        
    # Schedule it
    target = context.user_data.get("sched_target")
    text   = context.user_data.get("sched_text", "")
    photo  = context.user_data.get("sched_photo")
    unix_time = dt.timestamp()
    
    db = load_schedule_db()
    task_id = str(uuid.uuid4())[:8]
    db[task_id] = {
        "chat_id": target,
        "text": text,
        "photo_id": photo,
        "trigger_at": unix_time,
        "created_at": time.time()
    }
    save_schedule_db(db)
    
    # Cleanup context
    context.user_data.pop("sched_target", None)
    context.user_data.pop("sched_text", None)
    context.user_data.pop("sched_photo", None)
    
    human_time = dt.strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"✅ Awesome! Message scheduled for {human_time} to {target}.")
    return ConversationHandler.END

async def sched_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Schedule cancelled.")
    return ConversationHandler.END

def get_scheduler_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("schedule", sched_start),
            CallbackQueryHandler(sched_start, pattern=r"^cfg\|scheduler")
        ],
        states={
            SCHED_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, sched_target)],
            SCHED_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, sched_message)],
            SCHED_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, sched_time)]
        },
        fallbacks=[CommandHandler("cancel", sched_cancel)]
    )
