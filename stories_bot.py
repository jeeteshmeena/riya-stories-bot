import logging
import os
import threading
import http.server
import socketserver

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID
from scanner_client import scan_channel
from search_engine import fuzzy_search


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -------------------------
# Dummy server (Render fix)
# -------------------------

def start_server():

    port = int(os.environ.get("PORT", 10000))

    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:

        logger.info(f"Dummy server running on {port}")
        httpd.serve_forever()


# -------------------------
# /start
# -------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "✨ Riya Bot v10 Quantum\n\n"
        "Use /scan to index stories\n"
        "Then send story name"
    )


# -------------------------
# /scan
# -------------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("🔍 Scanning channel...")

    try:

        result = await scan_channel(CHANNEL_ID)

        await msg.edit_text(
            f"✅ Scan finished\n\n"
            f"Messages: {result['messages']}\n"
            f"Stories: {result['stories']}"
        )

    except Exception as e:

        await msg.edit_text(f"❌ Scan failed\n{e}")


# -------------------------
# search
# -------------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.lower().strip()

    # ignore small messages
    if len(text) < 4:
        return

    result = fuzzy_search(text)

    if not result:

        await update.message.reply_text("❌ Story not found")
        return

    await update.message.reply_text(
        f"🔥 Story Found\n\n"
        f"Name: {result['name']}\n\n"
        f"{result['link']}"
    )


# -------------------------
# main bot
# -------------------------

def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    logger.info("Riya Bot running")

    app.run_polling()


# -------------------------
# main
# -------------------------

def main():

    threading.Thread(target=start_server).start()

    start_bot()


if __name__ == "__main__":
    main()
