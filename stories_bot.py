import logging
import os
import threading
import http.server
import socketserver
import asyncio
import re
import json
import time

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    InlineQueryHandler,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID, COPYRIGHT_CHANNEL, REQUEST_GROUP, LOG_CHANNEL
from scanner_client import scan_channel
from search_engine import fuzzy_search


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------
# Render dummy server
# -----------------------

def start_server():
    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:
        logger.info(f"Server running {port}")
        httpd.serve_forever()


# -----------------------
# Persistent files
# -----------------------

REQUEST_FILE = "requests.json"
CLAIMS_FILE = "claims.json"
COOLDOWN_FILE = "cooldowns.json"


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# -----------------------
# Databases
# -----------------------

claims_db = load_json(CLAIMS_FILE)
cooldown_db = load_json(COOLDOWN_FILE)
request_db = load_json(REQUEST_FILE)

message_owner = {}

story_index = []
search_index = {}

last_scan_count = 0


# -----------------------
# Helpers
# -----------------------

def clean_story(name):
    name = re.sub(r"\(.*?\)", "", name)
    return name.strip()


def extract_story_type(text):
    if not text:
        return "Can't find"

    match = re.search(
        r"(Story Type|Type|Genre)\s*:-\s*(.+)",
        text,
        re.IGNORECASE
    )

    if match:
        return match.group(2).strip()

    return "Can't find"


def build_search_index(names):

    global search_index

    search_index = {}

    for item in names:

        name = item["name"]

        search_index[name.lower()] = item


def fast_search(query):

    query = query.lower()

    results = []

    for key in search_index:

        if query in key:
            results.append(search_index[key])

    return results[:10]


async def log(context, text):

    try:

        await context.bot.send_message(
            chat_id=LOG_CHANNEL,
            text=text
        )

    except:

        pass


async def auto_delete(msg, seconds):

    await asyncio.sleep(seconds)

    try:
        await msg.delete()
    except:
        pass


# -----------------------
# Welcome message
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user.mention_html()

    text = f"""
<b>✨ Hey Welcome</b>, {user}

<blockquote>@StoriesFinderBot</blockquote>

Commands: Type / to open the command menu and explore available options to search or request stories.

<blockquote><i>Disclaimer 📌
We only index Telegram files. We do not host content.</i></blockquote>

<u>Send your query to begin!</u>

<b>By</b> @MeJeetX
"""

    await update.message.reply_text(
        text=text,
        parse_mode="HTML"
    )


# -----------------------
# /scan
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global story_index, last_scan_count

    msg = await update.message.reply_text(
        "🔎 Starting scan..."
    )

    try:

        result = await scan_channel(CHANNEL_ID)

        stories = result.get("stories_data", [])

        story_index = stories

        build_search_index(stories)

        last_scan_count = len(stories)

        await msg.edit_text(
            f"✅ Scan complete\n\nStories indexed: {last_scan_count}"
        )

    except Exception as e:

        await msg.edit_text(
            f"Scan failed\n{e}"
        )


# -----------------------
# /stories
# -----------------------

async def stories(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not story_index:
        await update.message.reply_text("No stories indexed yet.")
        return

    text = "<b>Available stories 👇</b>\n\n"

    for i, story in enumerate(story_index, 1):

        text += f"<i>{i}. {story['name']}</i>\n"

    await update.message.reply_text(text, parse_mode="HTML")


# -----------------------
# /request
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return

    story = " ".join(context.args).lower()

    user = update.effective_user

    mention = user.mention_html()

    if story not in request_db:
        request_db[story] = []

    if user.id in request_db[story]:

        await update.message.reply_text(
            f"<b>{mention}</b>\n\n<i>You already requested this story.</i>",
            parse_mode="HTML"
        )
        return

    request_db[story].append(user.id)

    save_json(REQUEST_FILE, request_db)

    count = len(request_db[story])

    await context.bot.send_message(
        chat_id=REQUEST_GROUP,
        text=f"Story Request\n\nName: {story}\nUser: {user.id}\nTotal: {count}"
    )

    await update.message.reply_text(
        f"<b>{mention}</b>\n\n<b>Your request for <i>{story}</i> has been sent.</b>",
        parse_mode="HTML"
    )


# -----------------------
# search
# -----------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.message.text.strip()

    result = fuzzy_search(query)

    if not result:
        return

    user = update.effective_user

    mention = user.mention_html()

    story_name = clean_story(result["name"])

    caption_text = result.get("caption", "")

    story_type = extract_story_type(caption_text)

    keyboard = [

        [InlineKeyboardButton("OPEN STORY", url=result["link"])],

        [InlineKeyboardButton(
            "Got Copyright ?",
            callback_data=f"copyright|{story_name}"
        )],

        [InlineKeyboardButton(
            "Delete",
            callback_data="delete"
        )]

    ]

    photo = result.get("photo")

    caption = f"""
Hey {mention} 👋
<b>I found this story</b> 👇

<i>Name:-</i> <b>{story_name}</b>

<b>Story Type:-</b> <i>{story_type}</i>

<tg-spoiler>This reply will be deleted automatically in 30 minutes.</tg-spoiler>
"""

    if photo:

        msg = await update.message.reply_photo(
            photo=photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    else:

        msg = await update.message.reply_text(
            caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    message_owner[msg.message_id] = user.id

    asyncio.create_task(auto_delete(msg, 1800))


# -----------------------
# buttons
# -----------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    user = query.from_user

    await query.answer()

    if query.data == "delete":

        owner = message_owner.get(query.message.message_id)

        if user.id == owner:

            await query.message.delete()

        return

    if query.data.startswith("copyright"):

        story = query.data.split("|")[1]

        key = f"{user.id}:{story}"

        if key in claims_db:

            await query.answer(
                text="You already claimed this story",
                show_alert=True
            )
            return

        claims_db[key] = True

        save_json(CLAIMS_FILE, claims_db)

        await context.bot.send_message(

            chat_id=COPYRIGHT_CHANNEL,

            text=f"""
Copyright Claim

Story: {story}
User: @{user.username if user.username else user.first_name}
ID: {user.id}
"""
        )

        await query.message.reply_text(
            "✅ Your copyright claim has been submitted."
        )


# -----------------------
# Inline search
# -----------------------

async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.inline_query.query

    results = fast_search(query)

    articles = []

    for story in results:

        articles.append(

            InlineQueryResultArticle(

                id=story["name"],

                title=story["name"],

                input_message_content=InputTextMessageContent(
                    story["name"]
                )

            )

        )

    await update.inline_query.answer(
        articles,
        cache_time=5
    )


# -----------------------
# start bot
# -----------------------

def start_bot():

    global app

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("stories", stories))
    app.add_handler(CommandHandler("request", request_story))

    app.add_handler(InlineQueryHandler(inline_search))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    app.add_handler(CallbackQueryHandler(buttons))

    logger.info("Riya Bot running")

    app.run_polling()


# -----------------------
# main
# -----------------------

def main():

    threading.Thread(target=start_server).start()

    start_bot()


if __name__ == "__main__":
    main()
