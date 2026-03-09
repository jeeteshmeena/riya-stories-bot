import logging
import os
import threading
import http.server
import socketserver
import asyncio
import re

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
# Databases
# -----------------------

claims_db = {}
cooldown_db = {}
message_owner = {}

request_db = {}
request_chat = {}

story_index = []
search_index = {}

last_scan_count = 0


# -----------------------
# Helpers
# -----------------------

def clean_story(name):
    name = re.sub(r"\(.*?\)", "", name)
    return name.strip()


def build_search_index(names):
    global search_index
    search_index = {}
    for name in names:
        search_index[name.lower()] = name


def fast_search(query):
    query = query.lower()
    results = []
    for key in search_index:
        if query in key:
            results.append(search_index[key])
    return results[:10]


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


async def log(context, text):
    try:
        await context.bot.send_message(
            chat_id=LOG_CHANNEL,
            text=text
        )
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
# /scan premium progress
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global story_index, last_scan_count

    scan_text = (
"🔎 *Riya Database Scan*\n\n"
"*Status:* _Initializing scanner..._\n\n"
"*Progress:* ░░░░░░░░░░ 0%"
)

    msg = await update.message.reply_text(
        text=scan_text,
        parse_mode="Markdown"
    )

    await asyncio.sleep(1)

    await msg.edit_text(
        text="""
🔎 *Riya Database Scan*

*Status:* _Fetching channel messages..._

*Progress:* ▓▓░░░░░░░░ 20%
""",
        parse_mode="Markdown"
    )

    await asyncio.sleep(1)

    await msg.edit_text(
        text="""
🔎 *Riya Database Scan*

*Status:* _Detecting stories..._

*Progress:* ▓▓▓▓░░░░░░ 40%
""",
        parse_mode="Markdown"
    )

    await asyncio.sleep(1)

    try:

        result = await scan_channel(CHANNEL_ID)

        await msg.edit_text(
            text="""
🔎 *Riya Database Scan*

*Status:* _Building search index..._

*Progress:* ▓▓▓▓▓▓▓▓░░ 80%
""",
            parse_mode="Markdown"
        )

        await asyncio.sleep(1)

        story_index = result.get("names", [])

        build_search_index(story_index)

        last_scan_count = result.get("stories", len(story_index))

        await msg.edit_text(
            text=f"""
✅ *Scan Completed*

📚 *Stories Indexed:* {last_scan_count}  
⚡ *Search Engine:* _Optimized_

_Your story database is now fully updated._
""",
            parse_mode="Markdown"
        )

    except Exception as e:

        await msg.edit_text(
            text=f"Scan failed\n{e}"
        )


# -----------------------
# /stories command
# -----------------------

async def stories(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not story_index:
        await update.message.reply_text("No stories indexed yet.")
        return

    text = "<b>Available stories on this channel 👇🏻</b>\n\n"

    for i, name in enumerate(story_index, 1):
        text += f"<i>{i}:- {name}</i>\n"

    await update.message.reply_text(text=text, parse_mode="HTML")


# -----------------------
# request story
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return

    story = " ".join(context.args).lower()

    user = update.effective_user
    mention = user.mention_html()

    try:
        await update.message.delete()
    except:
        pass

    if story not in request_db:
        request_db[story] = set()
        request_chat[story] = update.effective_chat.id

    if user.id in request_db[story]:

        await update.effective_chat.send_message(
            text=f"""
<b>{mention}</b>

<i>You have already requested <b>{story}</b>.  
Please avoid sending duplicate requests.</i>
""",
            parse_mode="HTML"
        )

        return

    request_db[story].add(user.id)

    count = len(request_db[story])

    username = f"@{user.username}" if user.username else "No username"

    await context.bot.send_message(

        chat_id=REQUEST_GROUP,

        text=f"""
Story Request

Name: {story}
User ID: {user.id}
Username: {username}

Total Requests: {count}
"""
    )

    if count == 1:

        text = f"""
<b>{mention}</b>

<b>Your request for <i>{story}</i> has been sent.  
We will try our best to provide this story.  
If we find it, it will be uploaded soon.</b>
"""

    else:

        others = count - 1

        text = f"""
<b>{mention}</b>

<b>You and {others} other users requested <i>{story}</i>.  
We will try our best to provide this story.  
If we find it, it will be uploaded soon.</b>
"""

    await update.effective_chat.send_message(
        text=text,
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

    photo = result.get("photo") or result.get("image")

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

            text=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)

        )

    message_owner[msg.message_id] = user.id

    await asyncio.sleep(1800)

    try:
        await msg.delete()
    except:
        pass


# -----------------------
# buttons
# -----------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    user = query.from_user

    await query.answer()

    if query.data == "delete":

        msg_id = query.message.message_id
        owner = message_owner.get(msg_id)

        try:

            member = await context.bot.get_chat_member(
                query.message.chat.id,
                user.id
            )

            if user.id == owner or member.status in ["administrator", "creator"]:

                await query.message.delete()

        except:
            pass

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
            text="✅ Your copyright claim has been submitted."
        )


# -----------------------
# inline search
# -----------------------

async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.inline_query.query

    if not query:
        return

    results = fast_search(query)

    articles = []

    for story in results:

        articles.append(

            InlineQueryResultArticle(

                id=story,
                title=story,
                input_message_content=InputTextMessageContent(story)

            )

        )

    await update.inline_query.answer(articles, cache_time=5)


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
