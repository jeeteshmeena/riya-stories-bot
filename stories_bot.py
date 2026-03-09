import logging
import os
import threading
import http.server
import socketserver
import json
import asyncio

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID, REQUEST_GROUP, COPYRIGHT_CHANNEL
from scanner_client import scan_channel
from search_engine import fuzzy_search


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUEST_DB = "requests.json"


# -----------------------
# Dummy Web Server
# -----------------------

def start_server():

    port = int(os.environ.get("PORT", 10000))

    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:

        logger.info(f"Dummy server running on port {port}")

        httpd.serve_forever()


# -----------------------
# Request Database
# -----------------------

def load_requests():

    if os.path.exists(REQUEST_DB):

        with open(REQUEST_DB, "r") as f:

            return json.load(f)

    return {}


def save_requests(data):

    with open(REQUEST_DB, "w") as f:

        json.dump(data, f, indent=2)


# -----------------------
# Ignore normal chat words
# -----------------------

IGNORE_WORDS = [
    "hi",
    "hey",
    "hello",
    "good morning",
    "good night",
    "good evening",
    "ok",
    "okay",
    "thanks"
]


def is_conversation(text):

    text = text.lower().strip()

    if text in IGNORE_WORDS:
        return True

    if len(text) < 4:
        return True

    return False


# -----------------------
# /start
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "✨ Riya Bot v10\n\nSend story name to search."
    )


# -----------------------
# /scan
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("🔍 Scanning channel...")

    try:

        result = await scan_channel(CHANNEL_ID)

        await msg.edit_text(
            f"✅ Scan Complete\n\n"
            f"Stories Indexed: {result['stories']}"
        )

    except Exception as e:

        await msg.edit_text(f"❌ Scan failed\n{e}")


# -----------------------
# Story Search
# -----------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.message.text.strip()

    if is_conversation(query):
        return

    result = fuzzy_search(query)

    if not result:
        return

    keyboard = [

        [
            InlineKeyboardButton(
                "📖 Open Story",
                url=result["link"]
            )
        ],

        [
            InlineKeyboardButton(
                "⚠️ Copyright",
                url=f"https://t.me/{COPYRIGHT_CHANNEL}"
            )
        ],

        [
            InlineKeyboardButton(
                "🗑 Delete",
                callback_data="delete"
            )
        ]

    ]

    msg = await update.message.reply_text(

        f"👋 Hey {update.message.from_user.first_name}\n"
        f"I found this story 👇\n\n"
        f"📚 {result['text']}\n\n"
        f"🔗 Click below to open",

        reply_markup=InlineKeyboardMarkup(keyboard)

    )

    # auto delete after 5 minutes
    await asyncio.sleep(300)

    try:

        await msg.delete()

    except:

        pass


# -----------------------
# Button handler
# -----------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    data = query.data

    if data == "delete":

        await query.message.delete()


# -----------------------
# Bot Start
# -----------------------

def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

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
