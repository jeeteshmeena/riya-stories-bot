import logging
import os
import threading
import http.server
import socketserver
import asyncio
import re
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID, COPYRIGHT_CHANNEL, REQUEST_GROUP
from scanner_client import scan_channel
from search_engine import fuzzy_search

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------
# Dummy server
# -----------------------

def start_server():

    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:

        logger.info(f"Server running {port}")
        httpd.serve_forever()


# -----------------------
# Storage
# -----------------------

claims_db = {}
cooldown_db = {}
message_owner = {}
request_db = {}   # story -> set(user_ids)
last_scan_count = 0


# -----------------------
# Helpers
# -----------------------

def clean_story(name):

    name = re.sub(r"\(.*?\)", "", name)
    return name.strip()


def is_user_blocked(user_id):

    if user_id not in cooldown_db:
        return False

    if cooldown_db[user_id] < time.time():
        del cooldown_db[user_id]
        return False

    return True


# -----------------------
# /cooldown
# -----------------------

async def cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return

    user_id = int(context.args[0])
    minutes = int(context.args[1])

    cooldown_db[user_id] = time.time() + minutes * 60

    await update.message.reply_text(
        f"Cooldown applied to `{user_id}` for {minutes} minutes",
        parse_mode="Markdown"
    )


# -----------------------
# /start
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "✨ Riya Bot\n\nSend story name to search."
    )


# -----------------------
# /scan with update detection
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global last_scan_count

    msg = await update.message.reply_text("Scanning channel...")

    try:

        result = await scan_channel(CHANNEL_ID)

        if result["stories"] == last_scan_count:

            await msg.edit_text(
                "Scan complete.\n\nNo updates found. Story database is already up to date."
            )

        else:

            last_scan_count = result["stories"]

            await msg.edit_text(
f"""
Scan Complete

Messages scanned: {result['messages']}
Stories indexed: {result['stories']}

Database updated successfully.
"""
            )

    except Exception as e:

        await msg.edit_text(f"Scan failed\n{e}")


# -----------------------
# Story search
# -----------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.message.from_user

    if is_user_blocked(user.id):
        return

    query = update.message.text.strip()

    result = fuzzy_search(query)

    if not result:
        return

    story_name = clean_story(result["name"])

    keyboard = [

        [InlineKeyboardButton("OPEN STORY", url=result["link"])],

        [InlineKeyboardButton(
            "Got Copyright ?",
            callback_data=f"copyright|{story_name}"
        )],

        [InlineKeyboardButton("Delete", callback_data="delete")]

    ]

    mention = user.mention_html()

    msg = await update.message.reply_text(

f"""
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
# Request story
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return

    story = " ".join(context.args).lower()

    user = update.message.from_user
    mention = user.mention_html()

    try:
        await update.message.delete()
    except:
        pass

    # first request
    if story not in request_db:
        request_db[story] = set()

    # duplicate user request
    if user.id in request_db[story]:

        await update.effective_chat.send_message(

f"""
<b>{mention}</b>

<i>You have already requested <b>{story}</b>.  
Please avoid sending duplicate requests.</i>
""",

            parse_mode="HTML"

        )

        return

    request_db[story].add(user.id)

    count = len(request_db[story])

    # send to request channel
    username = f"@{user.username}" if user.username else "No username"

    await context.bot.send_message(

        chat_id=REQUEST_GROUP,

f"""
Story Request

Name: {story}
User ID: {user.id}
Username: {username}

Total Requests: {count}
"""

    )

    # confirmation message

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
# Button handler
# -----------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    user = query.from_user

    await query.answer()

    # delete
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
                "You cannot delete this message",
                show_alert=True
            )

        return

    # copyright
    if query.data.startswith("copyright"):

        story = query.data.split("|")[1]

        key = f"{user.id}:{story}"

        if key in claims_db:

            await query.answer(
                "You already claimed this story",
                show_alert=True
            )
            return

        claims_db[key] = True

        await context.bot.send_message(

            chat_id=COPYRIGHT_CHANNEL,

f"""
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
# Start bot
# -----------------------

def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("request", request_story))
    app.add_handler(CommandHandler("cooldown", cooldown))

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
