import asyncio
import logging
import threading
import http.server
import socketserver
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID
from search_engine import fuzzy_search
from scanner_client import scan_channel
from progress_bar import progress_bar


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# ------------------------------------
# Dummy Web Server (Render ke liye)
# ------------------------------------

def start_dummy_server():

    port = int(os.environ.get("PORT", 10000))

    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:

        logger.info(f"Dummy web server running on port {port}")

        httpd.serve_forever()


# ------------------------------------
# Start Command
# ------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "✨ Riya Bot v10 Quantum AI Online\n\n"
        "Type story name to search."
    )


# ------------------------------------
# Scan Command
# ------------------------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("🔍 Scanning channel...")

    try:

        await scan_channel(CHANNEL_ID)

        await msg.edit_text("✅ Scan completed")

    except Exception as e:

        await msg.edit_text(f"❌ Scan error\n{str(e)}")


# ------------------------------------
# Search Handler
# ------------------------------------

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.message.text.lower().strip()

    progress_msg = await update.message.reply_text(
        "Searching...\n" + progress_bar(2)
    )

    await asyncio.sleep(1)

    result = fuzzy_search(query)

    if not result:

        await progress_msg.edit_text("❌ Story not found")

        return

    text = f"""
✨ Story Found

Name : {result['name']}
Type : {result.get('type','Unknown')}

Link
{result['link']}
"""

    await progress_msg.edit_text(text)


# ------------------------------------
# Error Handler
# ------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):

    logger.error("Exception while handling update:", exc_info=context.error)


# ------------------------------------
# Bot Starter
# ------------------------------------

async def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_handler
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Riya Bot v10 Quantum running")

    await app.initialize()
    await app.start()

    await app.updater.start_polling()


# ------------------------------------
# Main
# ------------------------------------

def main():

    try:

        # Dummy web server thread
        threading.Thread(target=start_dummy_server).start()

        asyncio.run(start_bot())

    except RuntimeError:

        loop = asyncio.new_event_loop()

        asyncio.set_event_loop(loop)

        loop.run_until_complete(start_bot())


if __name__ == "__main__":
    main()
