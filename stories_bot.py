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

from config import BOT_TOKEN, CHANNEL_ID, COPYRIGHT_CHANNEL, REQUEST_GROUP
from scanner_client import scan_channel
from search_engine import fuzzy_search
from auto_scanner import auto_scan_loop
from inline_search import search_inline
from request_manager import add_request


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------
# Dummy Server
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
✨ **Welcome, {user}!**

📚 **Riya Stories Bot**

Search any story instantly.

**Commands**

`/scan` — scan database  
`/request story name` — request story

📌 _Example_

`/request Haweli`

Send story name to begin 🔍
""",

parse_mode="Markdown"

    )


# -----------------------
# /scan
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("🔎 **Scanning channel...**", parse_mode="Markdown")

    try:

        result = await scan_channel(CHANNEL_ID)

        await msg.edit_text(

f"""
✅ **Scan Complete**

📨 Messages scanned: `{result['messages']}`  
📚 Stories indexed: `{result['stories']}`
""",

parse_mode="Markdown"

        )

    except Exception as e:

        await msg.edit_text(f"❌ Scan failed\n`{e}`", parse_mode="Markdown")


# -----------------------
# Request Command
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.message.from_user

    if not context.args:

        await update.message.reply_text(
            "❌ Usage:\n`/request story name`",
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

            text=f"⚠️ {user.mention_html()} this story is already requested.\nPlease don't request again.",
            parse_mode="HTML"

        )

        return


    await context.bot.send_message(

        chat_id=REQUEST_GROUP,

        text=f"""
📚 **Story Request**

Name: **{story}**

Requested by: {user.mention_html()}
ID: `{user.id}`
""",

        parse_mode="HTML"

    )


    await update.effective_chat.send_message(

        text=f"✅ {user.mention_html()} your request for **{story}** has been sent.",
        parse_mode="HTML"

    )


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

f"""
✨ **Story Found**

📖 **{result['name']}**

_{result['text']}_
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
# Bot Start
# -----------------------

async def start_bot():

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
