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
    filters
)

from config import BOT_TOKEN, CHANNEL_ID, COPYRIGHT_CHANNEL
from scanner_client import scan_channel
from search_engine import fuzzy_search
from database import load_db


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def start_server():

    port = int(os.environ.get("PORT", 10000))

    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:

        logger.info(f"Server running {port}")

        httpd.serve_forever()


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "✨ Riya Bot v10\n\nSend story name to search."
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("🔍 Scanning channel...")

    try:

        before = len(load_db())

        result = await scan_channel(CHANNEL_ID)

        after = len(load_db())

        if after == before:

            await msg.edit_text(
                f"✅ Scan Complete\n\n"
                f"No new stories found.\n"
                f"Total stories: {after}"
            )

        else:

            new = after - before

            await msg.edit_text(
                f"✅ Scan Complete\n\n"
                f"New stories added: {new}\n"
                f"Total stories: {after}"
            )

    except Exception as e:

        logger.error(e)

        await msg.edit_text(f"❌ Scan failed\n{e}")


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.message.text.strip()

    if is_conversation(query):
        return

    result = fuzzy_search(query)

    if not result:
        return

    keyboard = [

        [InlineKeyboardButton("📖 Open Story", url=result["link"])],

        [InlineKeyboardButton(
            "⚠️ Copyright",
            url=f"https://t.me/{COPYRIGHT_CHANNEL}"
        )],

        [InlineKeyboardButton(
            "🗑 Delete",
            callback_data="delete"
        )]

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


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if query.data == "delete":

        try:
            await query.message.delete()
        except:
            pass


async def run_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    app.add_handler(CallbackQueryHandler(buttons))

    logger.info("Riya Bot running")

    await app.run_polling()


def main():

    threading.Thread(target=start_server).start()

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
