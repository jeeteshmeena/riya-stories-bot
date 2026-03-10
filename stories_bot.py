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
from database import (
    get_story,
    load_claims,
    save_claims,
    load_requests,
    save_requests,
)


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

claims_db = load_claims()
cooldown_db = {}
message_owner = {}

_requests_state = load_requests()
request_db = _requests_state.get("requests", {})
request_chat = _requests_state.get("chats", {})

story_index = []
search_index = {}

last_scan_count = 0


# -----------------------
# Helpers
# -----------------------

def clean_story(name):
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def build_search_index(names):
    global search_index
    search_index = {}
    for name in names:
        key = clean_story(name).lower()
        search_index[key] = name


def fast_search(query):
    query_key = clean_story(query).lower()
    if not query_key:
        return []
    name = search_index.get(query_key)
    if not name:
        return []
    return [name]


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
    async def _send():
        try:
            await asyncio.wait_for(
                context.bot.send_message(chat_id=LOG_CHANNEL, text=text),
                timeout=3
            )
        except:
            pass

    asyncio.create_task(_send())


def fast_search_contains(query, limit=10):
    q = clean_story(query).lower()
    if not q:
        return []

    out = []
    for key, original in search_index.items():
        if q in key:
            out.append(original)
            if len(out) >= limit:
                break
    return out


# -----------------------
# Welcome message
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    u = update.effective_user
    user = u.mention_html()

    text = f"""
<b>♡ Hey Welcome</b>, {user}

<blockquote>@StoriesFinderBot</blockquote>

Commands: Type / to open the menu and use the options to search, request, or explore stories.

<blockquote><i>Disclaimer 📌
We only index Telegram files. We do not host content.</i></blockquote>

<u>Send your query to begin!</u>

<b>By</b> @MeJeetX
"""

    await update.message.reply_text(
        text=text,
        parse_mode="HTML"
    )

    await log(
        context,
        f"START | user_id={u.id} username={u.username}"
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
<b>📌 About Riya</b>

<i>Riya is a smart Telegram story finder that helps users discover stories shared across Stories channels.</i>

<b>Fast search • Instant results • Story requests • Auto notifications</b>

<b>Developer:</b> @MeJeetX
<b>Version:</b> Riya v10
"""

    msg = await update.message.reply_text(text=text, parse_mode="HTML")

    async def _delete_later():
        await asyncio.sleep(1800)
        try:
            await msg.delete()
        except:
            pass
        try:
            await update.message.delete()
        except:
            pass

    asyncio.create_task(_delete_later())


async def how(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
<b>⚙️ How This Bot Works</b>

<b>• Send a story name</b>
<b>• Bot searches the database</b>
<b>• If available → you get the link</b>
<b>• If not → request it using /request</b>

When the story gets uploaded, you will be notified automatically.
"""

    msg = await update.message.reply_text(text=text, parse_mode="HTML")

    async def _delete_later():
        await asyncio.sleep(1800)
        try:
            await msg.delete()
        except:
            pass
        try:
            await update.message.delete()
        except:
            pass

    asyncio.create_task(_delete_later())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
<b>🆘 Help Center</b>

<i>Use these commands to interact with the bot:</i>

<u>/start</u> → Start the bot
<u>/request</u> → Request a story
<u>/scan</u> → Refresh story database [only for admins]

<b>You can also simply send a story name to search.</b>
"""

    msg = await update.message.reply_text(text=text, parse_mode="HTML")

    async def _delete_later():
        await asyncio.sleep(1800)
        try:
            await msg.delete()
        except:
            pass
        try:
            await update.message.delete()
        except:
            pass

    asyncio.create_task(_delete_later())


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

    cmd_msg = update.message

    msg = await cmd_msg.reply_text(
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

        await log(
            context,
            f"SCAN START | user_id={update.effective_user.id} username={update.effective_user.username}"
        )

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

        await log(
            context,
            f"SCAN DONE | stories={last_scan_count}"
        )

    except Exception as e:

        await msg.edit_text(
            text=f"Scan failed\n{e}"
        )

        await log(
            context,
            f"SCAN ERROR | {e}"
        )

    # delete user's /scan command after short delay, keep progress message
    async def _delete_cmd_later():
        await asyncio.sleep(60)
        try:
            await cmd_msg.delete()
        except:
            pass

    asyncio.create_task(_delete_cmd_later())


# -----------------------
# /stories command
# -----------------------

async def stories(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not story_index:
        await update.message.reply_text("No stories indexed yet.")
        return

    header = "<b>Available stories on this channel 👇🏻</b>\n"

    lines = []
    for i, name in enumerate(story_index, 1):
        title = clean_story(name)
        lines.append(f"{i} {title}")

    text = header + "\n<pre>" + "\n".join(lines) + "</pre>"

    cmd_msg = update.message

    reply = await update.message.reply_text(text=text, parse_mode="HTML")

    await log(
        context,
        f"STORIES | user_id={cmd_msg.from_user.id} username={cmd_msg.from_user.username}"
    )

    async def _delete_later():

        await asyncio.sleep(1800)

        try:
            await reply.delete()
        except:
            pass

        try:
            await cmd_msg.delete()
        except:
            pass

    asyncio.create_task(_delete_later())


# -----------------------
# request story
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:

        warn_text = """
🎬 Please provide the name of Story/Series
📝 Examples:
/request Vashikaran
/request Saaya
"""

        warn_msg = await update.effective_chat.send_message(warn_text)

        async def _delete_later():

            await asyncio.sleep(3600)

            try:
                await warn_msg.delete()
            except:
                pass

            try:
                await update.message.delete()
            except:
                pass

        asyncio.create_task(_delete_later())

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

    # persist requests state
    save_requests({"requests": request_db, "chats": request_chat})

    await log(
        context,
        f"REQUEST | user_id={user.id} username={user.username} story={story}"
    )


# -----------------------
# search
# -----------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query_text = update.message.text.strip()

    # Use only strict in-memory search based on scanned titles.
    fast_results = fast_search(query_text)

    if not fast_results:

        await log(
            context,
            f"SEARCH MISS | user_id={update.effective_user.id} username={update.effective_user.username} query={query_text}"
        )

        return

    # delete user query immediately on hit (as requested)
    try:
        await update.message.delete()
    except:
        pass

    # pick the first match and load full data from DB
    candidate_name = fast_results[0]
    result = get_story(clean_story(candidate_name).lower())

    if not result:
        return

    user = update.effective_user
    mention = user.mention_html()

    story_name = clean_story(result["text"])

    # Prefer pre‑computed story_type from the scanner, fallback to regex
    story_type = result.get("story_type")

    if not story_type:
        caption_text = result.get("caption", "")
        story_type = extract_story_type(caption_text)

    if not story_type:
        story_type = "Can't find"

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

<tg-spoiler>This reply will be deleted automatically in 5 minutes.</tg-spoiler>
"""

    if photo:

        msg = await update.message.reply_photo(

            photo=photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)

        )

    else:

        msg = await update.message.reply_video(

            video="https://files.catbox.moe/0cldq9.mp4",
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)

        )

    message_owner[msg.message_id] = user.id

    # delete the reply later without blocking the handler
    async def _delete_later():

        await asyncio.sleep(300)

        try:
            await msg.delete()
        except:
            pass

        # user message already deleted above

    asyncio.create_task(_delete_later())

    await log(
        context,
        f"SEARCH HIT | user_id={user.id} username={user.username} title={story_name}"
    )


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
        save_claims(claims_db)

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

    results = fast_search_contains(query, limit=10)

    articles = []

    for story in results:

        articles.append(

            InlineQueryResultArticle(

                id=clean_story(story).lower(),
                title=clean_story(story),
                input_message_content=InputTextMessageContent(clean_story(story))

            )

        )

    await update.inline_query.answer(articles, cache_time=5)

    await log(
        context,
        f"INLINE SEARCH | user_id={update.inline_query.from_user.id} username={update.inline_query.from_user.username} query={query} results={len(articles)}"
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
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("how", how))
    app.add_handler(CommandHandler("help", help_cmd))

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
