import logging
import os
import threading
import http.server
import socketserver
import asyncio
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters
)

from config import BOT_TOKEN, COPYRIGHT_CHANNEL, REQUEST_GROUP
from search_engine import fuzzy_search
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
# Clean story title
# -----------------------

def clean_title(name):

    name = re.sub(r"\(.*?\)", "", name)
    name = name.replace("-", "").strip()
    return name


# -----------------------
# Ignore chat words
# -----------------------

IGNORE = [
    "hi","hello","hey","ok",
    "thanks","good morning","good night"
]


def ignore(text):

    text = text.lower().strip()

    if text in IGNORE:
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
✨ Welcome {user}

Send story name to search.
Example:

Saaya
Haweli
""")



# -----------------------
# SEARCH STORY
# -----------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.message.text.strip()

    if ignore(query):
        return

    result = fuzzy_search(query)

    if not result:
        return

    user = update.message.from_user.first_name

    title = clean_title(result["name"])

    keyboard = [

        [
            InlineKeyboardButton(
                "📖 OPEN STORY",
                url=result["link"]
            )
        ],

        [
            InlineKeyboardButton(
                "Got Copyright ?",
                callback_data=f"copyright|{title}"
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
Hey {user} 👋
I found this story 👇

*{title}*

_༎ຶ‿༎ຶ This reply will be deleted automatically in 5 minutes_
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
# COPYRIGHT CLAIM
# -----------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    data = query.data

    user = query.from_user

    if data == "delete":

        try:
            await query.message.delete()
        except:
            pass

        return


    if data.startswith("copyright|"):

        story = data.split("|")[1]

        await context.bot.send_message(

            chat_id=COPYRIGHT_CHANNEL,

f"""
⚠ COPYRIGHT CLAIM

Story: {story}

User: @{user.username if user.username else user.first_name}
ID: {user.id}
""")

        await query.answer("Copyright request sent")


# -----------------------
# REQUEST STORY
# -----------------------

async def request(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.message.from_user

    if not context.args:

        await update.message.reply_text(
            "Usage:\n/request story name"
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

f"{user.mention_html()} please do not request again.",

parse_mode="HTML"

        )

        return


    await context.bot.send_message(

        chat_id=REQUEST_GROUP,

f"""
📚 STORY REQUEST

Name: {story}

User: @{user.username if user.username else user.first_name}
ID: {user.id}
"""
    )

    await update.effective_chat.send_message(

f"{user.mention_html()} your request has been sent.",

parse_mode="HTML"

    )



# -----------------------
# BOT START
# -----------------------

def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("request", request))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    app.add_handler(CallbackQueryHandler(buttons))

    logger.info("Riya Bot running")

    app.run_polling()



# -----------------------
# MAIN
# -----------------------

def main():

    threading.Thread(target=start_server).start()

    start_bot()


if __name__ == "__main__":
    main()
