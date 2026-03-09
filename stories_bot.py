import logging
import os
import threading
import http.server
import socketserver
import asyncio
import re
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
# Dummy server (Render)
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

        key = name.lower()

        search_index[key] = name


def fast_search(query):

    query = query.lower()

    results = []

    for key in search_index:

        if query in key:

            results.append(search_index[key])

    return results[:10]


def is_user_blocked(user_id):

    if user_id not in cooldown_db:
        return False

    if cooldown_db[user_id] < time.time():
        del cooldown_db[user_id]
        return False

    return True


async def log(context, text):

    try:

        await context.bot.send_message(
            chat_id=LOG_CHANNEL,
            text=text
        )

    except:
        pass


# -----------------------
# /start
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user.mention_html()

    text = f"""
<b>♡ Hey Welcome</b>, {user}

<blockquote>@RiyaBot</blockquote>

Commands: Type / to open command menu and explore story search and request options.

<blockquote><i>Disclaimer 📌
We only index Telegram files. We do not host content.</i></blockquote>

<u>Send your query to begin!</u>

<b>By</b> @MeJeetX
"""

    await update.message.reply_text(
        text=text,
        parse_mode="HTML"
    )

    await log(context, f"User started bot: {update.effective_user.id}")


# -----------------------
# /how
# -----------------------

async def how(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
<b>⚙️ How This Bot Works</b>

<b>• Send a story name</b>
<b>• Bot searches the database</b>
<b>• If available → you get the link</b>
<b>• If not → request it using /request</b>

When the story gets uploaded, you will be notified automatically.
"""

    await update.message.reply_text(text=text, parse_mode="HTML")


# -----------------------
# /help
# -----------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
<b>🆘 Help Center</b>

<i>Use these commands to interact with the bot:</i>

<u>/start</u> → Start the bot
<u>/request</u> → Request a story
<u>/scan</u> → Refresh story database [admins]

<b>You can also simply send a story name to search.</b>
"""

    await update.message.reply_text(text=text, parse_mode="HTML")


# -----------------------
# /about
# -----------------------

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
<b>📌 About Riya</b>

<i>Riya is a smart Telegram story finder that helps users discover stories shared across Telegram story channels.</i>

<b>Fast search • Instant results • Story requests • Auto notifications</b>

<b>Developer:</b> @MeJeetX
<b>Version:</b> Riya v10
"""

    await update.message.reply_text(text=text, parse_mode="HTML")


# -----------------------
# /stories
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
# Scan
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global last_scan_count, story_index

    msg = await update.message.reply_text("Scanning channel...")

    try:

        result = await scan_channel(CHANNEL_ID)

        story_index = result["names"]

        build_search_index(story_index)

        if result["stories"] == last_scan_count:

            await msg.edit_text("No updates found.")

        else:

            last_scan_count = result["stories"]

            await msg.edit_text(
                f"Scan complete.\nStories indexed: {last_scan_count}"
            )

            await notify_requested(context)

    except Exception as e:

        await msg.edit_text(f"Scan failed\n{e}")


# -----------------------
# Notify system
# -----------------------

async def notify_requested(context):

    for story, users in request_db.items():

        result = fuzzy_search(story)

        if not result:
            continue

        link = result["link"]

        tags = []

        for user_id in users:
            tags.append(f'<a href="tg://user?id={user_id}">user</a>')

        mention_text = " ".join(tags)

        chat_id = request_chat.get(story)

        try:

            await context.bot.send_message(

                chat_id=chat_id,

                text=f"""
<b>Hey {mention_text} 👻</b>

<i>Your requested story - {story} now available.</i>

<b>Read here:</b>
{link}
""",

                parse_mode="HTML"

            )

        except:
            pass

        await log(context, f"Story delivered: {story}")

        request_db[story] = set()


# -----------------------
# Inline search
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

                input_message_content=InputTextMessageContent(
                    f"🔎 {story}"
                )

            )

        )

    await update.inline_query.answer(articles, cache_time=5)


# -----------------------
# Request
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

            text=f"<b>{mention}</b>\n\n<i>You already requested <b>{story}</b>. Please avoid duplicate requests.</i>",

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

        text = f"<b>{mention}</b>\n\n<b>Your request for <i>{story}</i> has been sent.</b>"

    else:

        others = count - 1

        text = f"<b>{mention}</b>\n\n<b>You and {others} other users requested <i>{story}</i>.</b>"

    await update.effective_chat.send_message(
        text=text,
        parse_mode="HTML"
    )


# -----------------------
# Search
# -----------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if is_user_blocked(user.id):
        return

    query = update.message.text.strip()

    result = fuzzy_search(query)

    if not result:
        return

    story_name = clean_story(result["name"])

    keyboard = [

        [InlineKeyboardButton("OPEN STORY", url=result["link"])],

        [InlineKeyboardButton("Got Copyright ?", callback_data=f"copyright|{story_name}")],

        [InlineKeyboardButton("Delete", callback_data="delete")]

    ]

    mention = user.mention_html()

    msg = await update.message.reply_text(

        text=f"""
Hey {mention} 👋
I found this story 👇

<b>{story_name}</b>

<i>This reply will be deleted automatically in 30 minutes.</i>
""",

        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)

    )

    message_owner[msg.message_id] = user.id

    await asyncio.sleep(300)

    try:
        await msg.delete()
    except:
        pass


# -----------------------
# Buttons
# -----------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    user = query.from_user

    await query.answer()

    if query.data == "delete":

        msg_id = query.message.message_id
        owner_id = message_owner.get(msg_id)

        is_admin = False

        try:

            member = await context.bot.get_chat_member(
                query.message.chat.id,
                user.id
            )

            if member.status in ["administrator", "creator"]:
                is_admin = True

        except:
            pass

        if user.id == owner_id or is_admin:

            try:
                await query.message.delete()
            except:
                pass

        else:

            await query.answer(
                text="You cannot delete this message",
                show_alert=True
            )

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
# Start bot
# -----------------------

def start_bot():

    global app

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("how", how))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("stories", stories))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("request", request_story))

    app.add_handler(InlineQueryHandler(inline_search))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

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
