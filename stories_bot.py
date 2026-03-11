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
    ChosenInlineResultHandler,
    filters
)

from config import BOT_TOKEN, CHANNEL_ID, COPYRIGHT_CHANNEL, REQUEST_GROUP, LOG_CHANNEL, ADMIN_ID, OWNER_ID, AUTO_SCAN, API_ID, API_HASH, SESSION_STRING
from scanner_client import scan_channel
from database import (
    load_db, save_db, add_story, get_story, remove_stories_not_in,
    load_claims, save_claims, load_requests, save_requests,
    load_search_index, save_search_index, load_story_index, save_story_index,
    load_scan_state, save_scan_state, load_bot_stats, save_bot_stats
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Keep references to background tasks to support graceful shutdown
background_tasks = set()


def spawn_task(coro):
    """Create tracked background task to avoid pending-task destruction on shutdown."""
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task

# -----------------------
# Render dummy server
# -----------------------

def start_server():
    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler

    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            logger.info(f"Server running {port}")
            httpd.serve_forever()
    except Exception as e:
        logger.warning("Dummy HTTP server failed to start on port %s: %s", port, e)


# -----------------------
# Databases
# -----------------------

claims_db = load_claims()
cooldown_db = {}
message_owner = {}

_requests_state = load_requests()
request_db = _requests_state.get("requests", {})
request_chat = _requests_state.get("chats", {})

# Load persisted indexes so search works immediately after restart
story_index = load_story_index()
search_index = load_search_index()

last_scan_count = len(story_index)

# Rate limit: min seconds between searches per user
SEARCH_COOLDOWN = 2

# Pagination
STORIES_PER_PAGE = 25

# Scan state management
scan_in_progress = False
scan_cancel_requested = False
scan_user = None

# Bot statistics tracking
bot_stats = load_bot_stats()


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
        if key:
            search_index[key] = name
    save_search_index(search_index)


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
        return None

    match = re.search(
        r"(Story Type|Type|Genre)\s*:-\s*(.+)",
        text,
        re.IGNORECASE
    )

    if match:
        return match.group(2).strip()

    return None


def is_admin(user_id):
    if not user_id:
        return False
    return user_id == OWNER_ID or user_id == ADMIN_ID or (OWNER_ID and user_id == OWNER_ID)


async def log(context, text):
    if not LOG_CHANNEL:
        return

    async def _send():
        try:
            await asyncio.wait_for(
                context.bot.send_message(chat_id=LOG_CHANNEL, text=text),
                timeout=3
            )
        except Exception as e:
            logger.warning("Log send failed: %s", e)

    spawn_task(_send())


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


def init_search_index():
    """Bootstrap search index from DB if empty (e.g. after first deploy)."""
    global story_index, search_index, last_scan_count
    
    # Load persisted indexes first
    search_index = load_search_index()
    story_index = load_story_index()
    
    # If search index is empty, try to build from DB
    if not search_index:
        db = load_db()
        if not db:
            logger.info("No existing database found")
            return
        names = []
        for s in db.values():
            n = s.get("text") or s.get("name", "")
            if n and n not in names:
                names.append(n)
        if names:
            story_index = names
            build_search_index(story_index)
            save_story_index(story_index)
            last_scan_count = len(story_index)
            logger.info(f"Built search index from database: {len(names)} stories")
    else:
        # Ensure story_index is loaded from persisted data
        if not story_index:
            # Rebuild story_index from search_index values
            story_index = list(set(search_index.values()))
            save_story_index(story_index)
        
        last_scan_count = len(story_index)
        logger.info(f"Loaded persisted search index: {len(search_index)} entries, {len(story_index)} stories")


async def health_check_loop():
    """Periodic health check to keep the bot responsive and detect issues."""
    while True:
        try:
            await asyncio.sleep(300)  # Check every 5 minutes
            logger.info("Health check: Bot is running normally")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Health check error: {e}")


async def auto_scan_loop(bot=None):
    """Periodically rescan channel and refresh search index (runs in background)."""
    global story_index, last_scan_count
    if not CHANNEL_ID or (str(AUTO_SCAN).lower() != "true"):
        return
    while True:
        try:
            await asyncio.sleep(600)  # wait 10 min before first run
            logger.info("Auto scan started...")
            result = await scan_channel(CHANNEL_ID, bot=bot, log_channel=LOG_CHANNEL)
            names = result.get("names", [])
            # Only update indexes if we got a successful result - never wipe on failure
            if names:
                story_index = names
                build_search_index(story_index)
                save_story_index(story_index)
                last_scan_count = result.get("stories", len(story_index))
                logger.info("Auto scan done | stories=%d", last_scan_count)
            else:
                logger.warning("Auto scan returned no stories, keeping existing index")
        except asyncio.CancelledError:
            logger.info("Auto scan loop cancelled")
            break
        except Exception as e:
            logger.error("Auto scan error: %s", e)
            # Don't let the error crash the loop - continue after a shorter delay
            await asyncio.sleep(60)  # wait 1 min before retrying after error


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

    spawn_task(_delete_later())


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

    spawn_task(_delete_later())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
<b>🆘 Help Center</b>

<i>Use these commands to interact with the bot:</i>

<u>/start</u> → Start the bot
<u>/request</u> → Request a story
<u>/scan</u> → Refresh story database [only for admins]
<u>/info</u> → Story details
<u>/stats</u> → Bot statistics [admins]

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

    spawn_task(_delete_later())


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show story details: /info <title>"""
    if not context.args:
        await update.message.reply_text("Usage: /info <story name>")
        return
    query = " ".join(context.args).strip()
    results = fast_search(query) or fast_search_contains(query, limit=1)
    if not results:
        await update.message.reply_text("Story not found.")
        return
    result = get_story(clean_story(results[0]).lower())
    if not result:
        await update.message.reply_text("Story not found.")
        return
    name = clean_story(result.get("text", result.get("name", "")))
    link = result.get("link", "")
    stype = result.get("story_type") or extract_story_type(result.get("caption", ""))
    type_line = f"\n<b>Story Type:</b> {stype}" if stype else ""
    text = f"""<b>📖 {name}</b>{type_line}

<b>Link:</b> {link}"""
    await update.message.reply_text(text, parse_mode="HTML")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show comprehensive bot status with statistics."""
    global bot_stats
    
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ This command is for admins only.")
        return
    
    current_time = time.time()
    start_time = bot_stats.get("start_time", current_time)
    
    # Calculate uptime
    uptime_seconds = current_time - start_time
    uptime_days = int(uptime_seconds // 86400)
    uptime_hours = int((uptime_seconds % 86400) // 3600)
    uptime_minutes = int((uptime_seconds % 3600) // 60)
    
    # Calculate downtime
    downtime_start = bot_stats.get("downtime_start")
    total_downtime = bot_stats.get("total_downtime", 0)
    
    if downtime_start:
        current_downtime = current_time - downtime_start
        total_downtime += current_downtime
        downtime_days = int(current_downtime // 86400)
        downtime_hours = int((current_downtime % 86400) // 3600)
        downtime_minutes = int((current_downtime % 3600) // 60)
        downtime_str = f"{downtime_days}d {downtime_hours}h {downtime_minutes}m (ongoing)"
        bot_status = "🔴 <b>DEAD</b>"
    else:
        total_downtime_seconds = total_downtime
        downtime_days = int(total_downtime_seconds // 86400)
        downtime_hours = int((total_downtime_seconds % 86400) // 3600)
        downtime_minutes = int((total_downtime_seconds % 3600) // 60)
        downtime_str = f"{downtime_days}d {downtime_hours}h {downtime_minutes}m"
        bot_status = "🟢 <b>LIVE</b>"
    
    # Get current date/time in IST
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    current_datetime = ist_now.strftime("%d %B %Y, %I:%M:%S %p IST")
    last_updated = ist_now.strftime('%Y-%m-%d %H:%M:%S IST')
    
    # Get database stats
    db = load_db()
    total_stories = len(db)
    total_requests = sum(len(v) for v in request_db.values()) if isinstance(request_db, dict) else 0
    unique_requests = len(request_db)
    total_copyright_claims = len(claims_db)
    
    # Format message with all requested formatting
    status_text = f"""
<b>🤖 <u>Riya Bot Status Report</u></b>

📊 <i><b>Bot Status:</b></i> {bot_status}

⏱️ <b><i>Total Uptime:</i></b> <tg-spoiler>{uptime_days}d {uptime_hours}h {uptime_minutes}m</tg-spoiler>

📉 <b><i>Total Downtime:</i></b> <tg-spoiler>{downtime_str}</tg-spoiler>

📈 <b><i>Messages Sent:</i></b> <tg-spoiler>{bot_stats.get('total_messages_sent', 0):,}</tg-spoiler>

🔍 <b><i>Requests Received:</i></b> <tg-spoiler>{total_requests:,} (from {unique_requests} users)</tg-spoiler>

⚖️ <b><i>Copyright Claims:</i></b> <tg-spoiler>{total_copyright_claims:,}</tg-spoiler>

📚 <b><i>Stories in Database:</i></b> <tg-spoiler>{total_stories:,}</tg-spoiler>

🕐 <b><i>Current Date & Time:</i></b> <tg-spoiler>{current_datetime}</tg-spoiler>

🔗 <b><i>Bot Link:</i></b> <a href="https://t.me/StoriesFinderBot">Click here to open bot</a>

<tg-spoiler><i>Last updated: {last_updated}</i></tg-spoiler>
"""
    
    await update.message.reply_text(
        text=status_text,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    
    await log(
        context,
        f"STATUS | user_id={update.effective_user.id} username={update.effective_user.username}"
    )


# -----------------------
# /scan premium progress
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scan_in_progress, scan_cancel_requested, scan_user, story_index, last_scan_count

    cmd_msg = update.message
    if cmd_msg:
        try:
            await cmd_msg.delete()
        except Exception:
            pass

    if not is_admin(update.effective_user.id):
        await update.effective_chat.send_message("⛔ This command is for admins only.")
        return

    # Check if scan is already running
    if scan_in_progress:
        user_mention = update.effective_user.mention_html()
        scan_user_mention = scan_user.mention_html() if scan_user else "Unknown"
        
        funny_messages = [
            f"🚫 <b>Whoa there, {user_mention}!</b>\n\nA scan is already in progress by {scan_user_mention}.\n\n<i>Patience, young grasshopper! Good stories come to those who wait...</i> 🌱",
            f"🤚 <b>Hold your horses, {user_mention}!</b>\n\n{scan_user_mention} is currently scanning the channel.\n\n<i>The database is having a spa treatment - please wait! 💆‍♀️</i>",
            f"⏳ <b>Easy there, {user_mention}!</b>\n\n{scan_user_mention} is on a story-hunting mission.\n\n<i>Let them finish before you start your adventure! 🗺️</i>"
        ]
        
        await update.effective_chat.send_message(
            text=funny_messages[hash(user_mention) % len(funny_messages)],
            parse_mode="HTML"
        )
        return

    # Set scan state
    scan_in_progress = True
    scan_cancel_requested = False
    scan_user = update.effective_user

    scan_text = (
        "🔎 *Riya Database Scan*\n\n"
        "*Status:* _Initializing scanner..._\n\n"
        "*Progress:* ░░░░░░░░░░ 0%\n\n"
        "🆕 *Tap here to cancel scan*"
    )

    keyboard = [[InlineKeyboardButton("🛑 Cancel Scan", callback_data="cancel_scan")]]
    msg = await update.effective_chat.send_message(
        text=scan_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    await asyncio.sleep(1)

    try:
        # Check if cancelled before starting
        if scan_cancel_requested:
            await msg.edit_text(
                text="❌ *Scan Cancelled*\n\nThe scan was cancelled by the user.",
                parse_mode="Markdown"
            )
            return

        await msg.edit_text(
            text="""\
🔎 *Riya Database Scan*

*Status:* _Fetching channel messages..._

*Progress:* ▓▓░░░░░░░ 20%

🆕 *Tap here to cancel scan*""",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await asyncio.sleep(1)

        # Check if cancelled
        if scan_cancel_requested:
            await msg.edit_text(
                text="❌ *Scan Cancelled*\n\nThe scan was cancelled by the user.",
                parse_mode="Markdown"
            )
            return

        await msg.edit_text(
            text="""\
🔎 *Riya Database Scan*

*Status:* _Detecting stories..._

*Progress:* ▓▓▓▓░░░░░ 40%

🆕 *Tap here to cancel scan*""",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await asyncio.sleep(1)

        await log(
            context,
            f"SCAN START | user_id={update.effective_user.id} username={update.effective_user.username}"
        )

        # Check if cancelled before scanning
        if scan_cancel_requested:
            await msg.edit_text(
                text="❌ *Scan Cancelled*\n\nThe scan was cancelled by the user.",
                parse_mode="Markdown"
            )
            return

        result = await scan_channel(CHANNEL_ID, bot=context.bot, log_channel=LOG_CHANNEL)

        # Check if cancelled during scan
        if scan_cancel_requested:
            await msg.edit_text(
                text="❌ *Scan Cancelled*\n\nThe scan was cancelled by the user.",
                parse_mode="Markdown"
            )
            return

        await msg.edit_text(
            text="""\
🔎 *Riya Database Scan*

*Status:* _Building search index..._

*Progress:* ▓▓▓▓▓▓▓▓░░ 80%

🆕 *Tap here to cancel scan*""",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await asyncio.sleep(1)

        story_index = result.get("names", [])

        build_search_index(story_index)
        save_story_index(story_index)

        last_scan_count = result.get("stories", len(story_index))

        await msg.edit_text(
            text=f"""\
✅ *Scan Completed*

📚 *Stories Indexed:* {last_scan_count}  
⚡ *Search Engine:* _Optimized_

_Your story database is now fully updated._""",
            parse_mode="Markdown"
        )

        await log(
            context,
            f"SCAN DONE | stories={last_scan_count}"
        )

    except Exception as e:
        await msg.edit_text(
            text=f"❌ *Scan Failed*\n\n{e}",
            parse_mode="Markdown"
        )

        await log(
            context,
            f"SCAN ERROR | {e}"
        )
    finally:
        # Reset scan state
        scan_in_progress = False
        scan_user = None
        scan_cancel_requested = False

    async def _delete_scan_reply_later():
        await asyncio.sleep(1800)
        try:
            await msg.delete()
        except Exception:
            pass

    spawn_task(_delete_scan_reply_later())


# -----------------------
# /stories command
# -----------------------

def _stories_page(page=0):
    """Build stories list for a given page."""
    start = page * STORIES_PER_PAGE
    end = start + STORIES_PER_PAGE
    chunk = story_index[start:end]
    lines = []
    for i, name in enumerate(chunk, start + 1):
        title = clean_story(name)
        lines.append(f"{i} {title}")
    header = "<b>Available stories on this channel 👇🏻</b>\n"
    text = header + "\n<pre>" + "\n".join(lines) + "</pre>"
    total = len(story_index)
    has_next = end < total
    has_prev = page > 0
    return text, has_prev, has_next, page


async def stories(update: Update, context: ContextTypes.DEFAULT_TYPE):

    cmd_msg = update.message
    if cmd_msg:
        try:
            await cmd_msg.delete()
        except Exception:
            pass

    if not story_index:
        await update.effective_chat.send_message("No stories indexed yet. Run /scan first.")
        return

    text, has_prev, has_next, page = _stories_page(0)

    keyboard = []
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"stories_p|{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"stories_p|{page+1}"))
    if nav:
        keyboard.append(nav)

    reply = await update.effective_chat.send_message(
        text=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
    )

    await log(
        context,
        f"STORIES | user_id={cmd_msg.from_user.id} username={cmd_msg.from_user.username}"
    )

    async def _delete_later():
        await asyncio.sleep(1800)
        try:
            await reply.delete()
        except Exception:
            pass

    spawn_task(_delete_later())


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

        spawn_task(_delete_later())

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

    if REQUEST_GROUP:
        try:
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
        except Exception as e:
            logger.warning("Failed to forward request to REQUEST_GROUP: %s", e)
    else:
        logger.warning("REQUEST_GROUP is not configured; request is stored locally only")

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

    msg = update.message or update.channel_post
    if not msg or not getattr(msg, "text", None):
        return

    user = update.effective_user
    if not user:
        return  # e.g. channel_post without forward info

    query_text = (msg.text or "").strip()

    if not query_text or len(query_text) < 2:
        return

    # Rate limit
    now = time.time()
    last = cooldown_db.get(user.id, 0)
    if now - last < SEARCH_COOLDOWN:
        return
    cooldown_db[user.id] = now

    # Only respond if it looks like a story search query
    # Must be at least 3 characters and contain letters only (mostly)
    if len(query_text) < 3 or not re.match(r'^[a-zA-Z0-9\s\-_]+$', query_text):
        return

    # Try exact match first, then substring match (only from DB)
    fast_results = fast_search(query_text)
    if not fast_results:
        fast_results = fast_search_contains(query_text, limit=1)

    if not fast_results:
        # Don't respond to queries that don't match stories
        # Only log if it looks like a genuine story search attempt
        if len(query_text) >= 3 and re.search(r'[a-zA-Z]', query_text):
            await log(
                context,
                f"SEARCH MISS | user_id={user.id} username={user.username} query={query_text}"
            )
            suggestions = fast_search_contains(query_text, limit=5)
            if len(suggestions) >= 2:
                # "Did you mean?" with buttons
                keyboard = []
                for s in suggestions:
                    key = clean_story(s).lower()
                    if len(f"srch|{key}") <= 64:
                        keyboard.append([InlineKeyboardButton(s, callback_data=f"srch|{key}")])
                no_msg = await msg.reply_text(
                    "❓ Did you mean one of these?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                no_msg = await msg.reply_text(
                    "❌ No story found with that name.\n\n"
                    "Check spelling or use /stories to see available titles."
                )
            async def _del_no():
                await asyncio.sleep(30)
                try:
                    await no_msg.delete()
                except Exception:
                    pass
            spawn_task(_del_no())
        return

    # pick the first match and load full data from DB
    candidate_name = fast_results[0]
    result = get_story(clean_story(candidate_name).lower())

    if not result:
        return

    # delete user query immediately on hit (as requested)
    try:
        await msg.delete()
    except Exception:
        pass

    mention = user.mention_html()

    story_name = clean_story(result["text"])

    # Prefer pre‑computed story_type from the scanner, fallback to regex
    story_type = result.get("story_type")

    if not story_type:
        caption_text = result.get("caption", "")
        story_type = extract_story_type(caption_text)

    if not story_type:
        story_type = "Not specified"

    keyboard = [
        [InlineKeyboardButton("OPEN STORY", url=result["link"])],
        [InlineKeyboardButton("Got Copyright ?", callback_data=f"copyright|{story_name}")],
        [InlineKeyboardButton("Delete", callback_data="delete")]
    ]

    photo = result.get("photo") or result.get("image")
    story_type_line = f"\n<b>Story Type:-</b> <i>{story_type}</i>" if story_type != "Not specified" else ""
    caption = f"""Hey {mention} 👋
<b>I found this story</b> 👇

<i>Name:-</i> <b>{story_name}</b>{story_type_line}

<tg-spoiler>This reply will be deleted automatically in 5 minutes.</tg-spoiler>
"""

    chat_id = update.effective_chat.id

    if photo:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        # Only use default video when story has no image
        msg = await context.bot.send_video(
            chat_id=chat_id,
            video="https://files.catbox.moe/lr91ja.mp4",
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

    spawn_task(_delete_later())

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

    if query.data == "cancel_scan":
        global scan_cancel_requested, scan_in_progress
        if scan_in_progress:
            scan_cancel_requested = True
            await query.edit_message_text(
                text="🛑 *Cancelling scan...*\n\nPlease wait for the current operation to finish.",
                parse_mode="Markdown"
            )
        else:
            await query.answer("No scan is currently running.", show_alert=True)
        return

    if query.data.startswith("srch|"):
        # "Did you mean?" button clicked - send story reply
        try:
            key = query.data.split("|", 1)[1].strip()
        except IndexError:
            await query.answer()
            return
        result = get_story(key)
        if not result:
            await query.answer("Story not found.", show_alert=True)
            return
        user = query.from_user
        mention = user.mention_html()
        story_name = clean_story(result.get("text", result.get("name", "")))
        story_type = result.get("story_type") or extract_story_type(result.get("caption", "")) or "Not specified"
        story_type_line = f"\n<b>Story Type:-</b> <i>{story_type}</i>" if story_type != "Not specified" else ""
        caption = f"""Hey {mention} 👋
<b>I found this story</b> 👇

<i>Name:-</i> <b>{story_name}</b>{story_type_line}

<tg-spoiler>This reply will be deleted automatically in 5 minutes.</tg-spoiler>
"""
        keyboard = [
            [InlineKeyboardButton("OPEN STORY", url=result["link"])],
            [InlineKeyboardButton("Got Copyright ?", callback_data=f"copyright|{story_name}")],
            [InlineKeyboardButton("Delete", callback_data="delete")]
        ]
        photo = result.get("photo") or result.get("image")
        try:
            if photo:
                sent = await context.bot.send_photo(
                    chat_id=query.message.chat.id,
                    photo=photo,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                # Only use default video when story has no image
                sent = await context.bot.send_video(
                    chat_id=query.message.chat.id,
                    video="https://files.catbox.moe/lr91ja.mp4",
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            message_owner[sent.message_id] = user.id
            try:
                await query.message.delete()
            except Exception:
                pass

            async def _del_later():
                await asyncio.sleep(300)
                try:
                    await sent.delete()
                except Exception:
                    pass
            spawn_task(_del_later())
        except Exception as e:
            logger.warning("srch callback failed: %s", e)
        await query.answer()
        return

    if query.data.startswith("stories_p|"):
        try:
            page = int(query.data.split("|")[1])
        except (IndexError, ValueError):
            await query.answer()
            return
        text, has_prev, has_next, _ = _stories_page(page)
        keyboard = []
        nav = []
        if has_prev:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"stories_p|{page-1}"))
        if has_next:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"stories_p|{page+1}"))
        if nav:
            keyboard.append(nav)
        try:
            await query.message.edit_text(
                text=text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
        except Exception:
            pass
        await query.answer()
        return

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


async def chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When user selects inline result - the message is sent to chat; Message handler will reply. Log for debugging."""
    chosen = update.chosen_inline_result
    await log(context, f"CHOSEN INLINE | user_id={chosen.from_user.id} result_id={chosen.result_id}")


# -----------------------
# start bot
# -----------------------

def validate_config():
    """Fail fast for missing critical deployment configuration."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not CHANNEL_ID:
        missing.append("CHANNEL_ID")
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not SESSION_STRING:
        missing.append("SESSION_STRING")

    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def start_bot():

    global app

    validate_config()
    init_search_index()

    async def _post_init(application):
        if str(AUTO_SCAN).lower() == "true" and CHANNEL_ID:
            spawn_task(auto_scan_loop(application.bot))
        # Start health check loop
        spawn_task(health_check_loop())

    async def _post_shutdown(application):
        # Gracefully cancel all tracked tasks (timers/loops) to prevent pending-task warnings
        tasks = list(background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # Use improved ApplicationBuilder configuration to prevent conflicts
    app = (Application.builder()
            .token(BOT_TOKEN)
            .post_init(_post_init)
            .post_shutdown(_post_shutdown)
            .get_updates_connect_timeout(30)
.get_updates_read_timeout(30)
.get_updates_write_timeout(30)
.get_updates_pool_timeout(30)
            .build())

    # Add error handler for all updates
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling an update: %s", context.error)
        # Don't crash the bot on individual update errors

    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("stories", stories))
    app.add_handler(CommandHandler("request", request_story))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("how", how))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(CommandHandler("stats", status_cmd))
    app.add_handler(CommandHandler("status", status_cmd))

    app.add_handler(InlineQueryHandler(inline_search))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    app.add_handler(CallbackQueryHandler(buttons))

    logger.info("Riya Bot running")

    # Use improved polling configuration to prevent conflicts
    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=None  # Get all update types
        )
    except Exception as e:
        logger.error("Bot polling error: %s", e)
        # Don't restart automatically - let platform handle it
        logger.info("Bot stopped. Platform will restart if needed.")


# -----------------------
# main
# -----------------------

def main():

    threading.Thread(target=start_server, daemon=True).start()

    start_bot()


if __name__ == "__main__":
    main()
