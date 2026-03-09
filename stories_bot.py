import logging
import os
import threading
import http.server
import socketserver
import asyncio
import re
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID, COPYRIGHT_CHANNEL, REQUEST_GROUP

from scanner_client import scan_channel
from search_engine import fuzzy_search


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------
# Render dummy server
# -----------------------

def start_server():

    port = int(os.environ.get("PORT", 10000))

    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:

        logger.info(f"Server running {port}")

        httpd.serve_forever()


# -----------------------
# Ignore normal chat
# -----------------------

IGNORE_WORDS = [
    "hi","hello","hey","ok","thanks",
    "good morning","good night"
]


def is_conversation(text):

    text = text.lower().strip()

    if text in IGNORE_WORDS:
        return True

    if len(text) < 3:
        return True

    return False


# -----------------------
# Story name clean
# -----------------------

def clean_story(name):

    name = re.sub(r"\(.*?\)", "", name)

    return name.strip()


# -----------------------
# Claim system storage
# -----------------------

claims_db = {}
cooldown_db = {}


def is_user_blocked(user_id):

    if user_id not in cooldown_db:
        return False

    if cooldown_db[user_id] < time.time():
        del cooldown_db[user_id]
        return False

    return True


# -----------------------
# /cooldown admin command
# -----------------------

async def cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return

    user_id = int(context.args[0])
    minutes = int(context.args[1])

    cooldown_db[user_id] = time.time() + minutes * 60

    await update.message.reply_text(
        f"Cooldown applied to `{user_id}` for {minutes} minutes",
        parse_mode="Markdown"
    )


# -----------------------
# /start
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "✨ Riya Bot\n\nSend story name to search."
    )


# -----------------------
# /scan
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("Scanning channel...")

    try:

        result = await scan_channel(CHANNEL_ID)

        await msg.edit_text(

f"""
Scan Complete

Messages scanned: {result['messages']}
Stories indexed: {result['stories']}
"""
        )

    except Exception as e:

        await msg.edit_text(f"Scan failed\n{e}")


# -----------------------
# Story search
# -----------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.message.from_user

    if is_user_blocked(user.id):
        return

    query = update.message.text.strip()

    if is_conversation(query):
        return

    result = fuzzy_search(query)

    if not result:
        return

    story_name = clean_story(result["name"])

    keyboard = [

        [
            InlineKeyboardButton(
                "OPEN STORY",
                url=result["link"]
            )
        ],

        [
            InlineKeyboardButton(
                "Got Copyright ?",
                callback_data=f"copyright|{story_name}"
            )
        ],

        [
            InlineKeyboardButton(
                "Delete",
                callback_data="delete"
            )
        ]
    ]

    msg = await update.message.reply_text(

f"""
Hey {user.first_name} 👋
I found this story 👇

*{story_name}*

_༎ຶ‿༎ຶ This reply will be deleted automatically in 30 minutes_
""",

        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)

    )

    await asyncio.sleep(300)

    try:
        await msg.delete()
    except:
        pass


# -----------------------
# Request story
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return

    story = " ".join(context.args)

    user = update.message.from_user

    try:
        await update.message.delete()
    except:
        pass

    await context.bot.send_message(
        chat_id=REQUEST_GROUP,
        text=f"Story Request\n\nName: {story}\nUser: {user.id}"
    )

    await update.effective_chat.send_message(
        text=f"{user.first_name}, your request for {story} has been sent."
    )


# -----------------------
# Button handler
# -----------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    user = query.from_user

    await query.answer()

    if query.data == "delete":

        try:
            await query.message.delete()
        except:
            pass

        return


    if query.data.startswith("copyright"):

        story = query.data.split("|")[1]

        # duplicate claim check
        key = f"{user.id}:{story}"

        if key in claims_db:

            await query.answer(
                "You already claimed this story.",
                show_alert=True
            )
            return

        claims_db[key] = True

        # send log
        await context.bot.send_message(

            chat_id=COPYRIGHT_CHANNEL,

            text=f"""
Copyright Claim

Story: {story}

User: @{user.username if user.username else user.first_name}
ID: {user.id}
"""
        )

        # confirmation
        await query.message.reply_text(
            "✅ Your copyright claim has been submitted."
        )


# -----------------------
# Start bot
# -----------------------

def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("request", request_story))
    app.add_handler(CommandHandler("cooldown", cooldown))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    app.add_handler(CallbackQueryHandler(buttons))

    logger.info("Riya Bot running")

    app.run_polling()


# -----------------------
# Main
# -----------------------

def main():

    threading.Thread(target=start_server).start()

    start_bot()


if __name__ == "__main__":

    main()
