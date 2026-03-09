import logging
import os
import threading
import http.server
import socketserver

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

from config import BOT_TOKEN, CHANNEL_ID, COPYRIGHT_CHANNEL, REQUEST_GROUP
from scanner_client import scan_channel
from search_engine import fuzzy_search
from inline_search import search_inline
from request_manager import add_request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------
# Dummy server (Render)
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

    user = update.message.from_user.first_name

    await update.message.reply_text(

f"""
✨ **Welcome {user}**

📚 **Riya Story Finder**

Send story name to search.

Commands:

`/scan` — update database  
`/request story name` — request story

Example:

`Saaya`
`Haweli`
""",

parse_mode="Markdown"

    )


# -----------------------
# /scan
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("🔎 Scanning stories...")

    try:

        result = await scan_channel(CHANNEL_ID)

        await msg.edit_text(

f"""
✅ Scan Complete

Messages scanned: {result['messages']}
Stories indexed: {result['stories']}
"""
        )

    except Exception as e:

        await msg.edit_text(f"Scan failed\n{e}")


# -----------------------
# Request story
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.message.from_user

    if not context.args:

        await update.message.reply_text(
            "Usage:\n`/request story name`",
            parse_mode="Markdown"
        )
        return

    story = " ".join(context.args)

    status = add_request(story, user)

    try:
        await update.message.delete()
    except:
        pass

    if status == "duplicate":

        await update.effective_chat.send_message(
            f"⚠️ {user.mention_html()} this story is already requested.",
            parse_mode="HTML"
        )

        return


    await context.bot.send_message(

        chat_id=REQUEST_GROUP,

        text=f"""
Story Request

Name: {story}

User: {user.mention_html()}
ID: {user.id}
""",

        parse_mode="HTML"

    )


    await update.effective_chat.send_message(

        f"✅ {user.mention_html()} your request for **{story}** has been sent.",

        parse_mode="HTML"

    )


# -----------------------
# Story search
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
            InlineKeyboardButton("📖 Open Story", url=result["link"])
        ],

        [
            InlineKeyboardButton("⚠️ Copyright", url=f"https://t.me/{COPYRIGHT_CHANNEL}")
        ],

        [
            InlineKeyboardButton("🗑 Delete", callback_data="delete")
        ]

    ]

    await update.message.reply_text(

f"""
✨ **Story Found**

📖 {result['name']}

{result['text']}
""",

        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)

    )


# -----------------------
# Inline search
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
# Bot start
# -----------------------

def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("request", request_story))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    app.add_handler(InlineQueryHandler(inline_query))

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
