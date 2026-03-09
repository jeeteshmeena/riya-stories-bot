import logging
import os
import threading
import http.server
import socketserver
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    InlineQueryHandler,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID, COPYRIGHT_CHANNEL
from scanner_client import scan_channel
from search_engine import fuzzy_search
from auto_scanner import auto_scan_loop
from inline_search import search_inline
from database import load_db


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------
# Dummy Web Server
# -----------------------

def start_server():

    port = int(os.environ.get("PORT", 10000))

    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:

        logger.info(f"Server running {port}")

        httpd.serve_forever()


# -----------------------
# Ignore conversation
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
# /start
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(

        "✨ **Riya Bot v10**\n\n"
        "Send any story name to search.\n\n"
        "Example:\n"
        "`Haweli`\n"
        "`Saaya`",

        parse_mode="Markdown"
    )


# -----------------------
# /scan
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("🔍 Scanning channel...")

    try:

        old_count = len(load_db())

        result = await scan_channel(CHANNEL_ID)

        new_count = result["stories"]

        if new_count == old_count:

            await msg.edit_text(
                f"✅ Scan Complete\n\n"
                f"No updates found.\n"
                f"Stories remain: {new_count}"
            )

        else:

            await msg.edit_text(
                f"✅ Scan Complete\n\n"
                f"Messages scanned: {result['messages']}\n"
                f"Stories indexed: {new_count}"
            )

    except Exception as e:

        logger.error(e)

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

        keyboard = [

            [
                InlineKeyboardButton(
                    "📩 Request Story",
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

            f"❌ Story not found\n\n"
            f"Name: {query}",

            reply_markup=InlineKeyboardMarkup(keyboard)

        )

        await asyncio.sleep(300)

        try:
            await msg.delete()
        except:
            pass

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

        f"Hey {update.message.from_user.first_name} 👋\n"
        f"I found this story 👇\n\n"
        f"{result['text']}\n\n"
        f"Click below to open",

        reply_markup=InlineKeyboardMarkup(keyboard)

    )

    await asyncio.sleep(300)

    try:
        await msg.delete()
    except:
        pass


# -----------------------
# Inline Search
# -----------------------

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.inline_query.query
    offset = update.inline_query.offset

    if not query:
        return

    results, next_offset = search_inline(query, offset)

    await update.inline_query.answer(
        results,
        next_offset=next_offset,
        cache_time=5
    )


# -----------------------
# Buttons
# -----------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if query.data == "delete":

        try:
            await query.message.delete()
        except:
            pass


# -----------------------
# Error handler
# -----------------------

async def error_handler(update, context):

    logger.error(context.error)


# -----------------------
# Bot Start
# -----------------------

async def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    app.add_handler(InlineQueryHandler(inline_query))

    app.add_handler(CallbackQueryHandler(buttons))

    app.add_error_handler(error_handler)

    logger.info("Riya Bot running")

    asyncio.create_task(auto_scan_loop())

    await app.run_polling()


# -----------------------
# Main
# -----------------------

def main():

    threading.Thread(target=start_server).start()

    asyncio.run(start_bot())


if __name__ == "__main__":

    main()
