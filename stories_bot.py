import asyncio
import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters
)

from telegram import Update
from telegram.ext import ContextTypes

from config import BOT_TOKEN
from search_engine import search
from scanner import scan_channel
from progress_bar import progress_bar


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "✨ Riya Bot v10 Quantum AI running"
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("Scanning channel...")

    await scan_channel(context.bot)

    await msg.edit_text("Scan completed")


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.message.text.lower()

    msg = await update.message.reply_text(
        "Searching...\n" + progress_bar(1)
    )

    result = search(query)

    if not result:

        await msg.edit_text("Story not found")
        return

    text = f"""
✨ Story Found

Name : {result['name']}
Type : {result['type']}

Link
{result['link']}
"""

    await msg.edit_text(text)


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

    logger.info("Riya Bot started")

    await app.initialize()
    await app.start()
    await app.updater.start_polling()


def main():

    try:
        asyncio.run(start_bot())

    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_bot())


if __name__ == "__main__":
    main()
