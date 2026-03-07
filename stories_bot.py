import asyncio
import logging
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID
from database import add_story, search_story
from parser import parse_story
from progress_bar import progress_bar

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# ------------------------------
# CHANNEL SCANNER
# ------------------------------

async def scan_channel(app):

    logger.info("Starting channel scan...")

    try:
        async for message in app.bot.get_chat_history(CHANNEL_ID):

            story = parse_story(message)

            if story:
                add_story(story)

    except Exception as e:
        logger.error(f"Scanner error: {e}")


# ------------------------------
# SEARCH HANDLER
# ------------------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.message.text.lower()

    progress = await update.message.reply_text("Searching...\n" + progress_bar(2))

    result = search_story(query)

    if not result:

        await progress.edit_text(
            "Story not found.\n\nUse Request button."
        )
        return

    text = f"""
✨ **Story Found**

Name : {result['name']}
Type : {result['type']}

Link
{result['link']}
"""

    await progress.edit_text(text, parse_mode="Markdown")


# ------------------------------
# COMMANDS
# ------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Riya Bot v10 Quantum AI running"
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("Scanning channel...")

    await scan_channel(context.application)

    await update.message.reply_text("Scan complete")


# ------------------------------
# MAIN
# ------------------------------

async def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search
        )
    )

    logger.info("Riya Bot v10 Quantum running")

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
