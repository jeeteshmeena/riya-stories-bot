import logging
import os
import signal
import sys
import threading
import http.server
import socketserver
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

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
    ChatMemberHandler,
    filters,
)

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    COPYRIGHT_CHANNEL,
    REQUEST_GROUP,
    LOG_CHANNEL,
    ADMIN_ID,
    OWNER_ID,
    AUTO_SCAN,
    RUN_HTTP_SERVER,
    SESSION_STRING,
    API_ID,
    API_HASH,
)
from scanner_client import scan_channel
from search_engine import fuzzy_search
from filters_text import is_valid_query
from database import (
    get_story,
    load_db,
    load_claims,
    save_claims,
    load_requests,
    save_requests,
    load_search_index,
    save_search_index,
    load_story_index,
    save_story_index,
    load_languages,
    save_languages,
    load_cooldowns,
    save_cooldowns,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------
# Optional HTTP server (Render needs it for health checks; GCP VPS usually doesn't)
# -----------------------

def start_server():
    if not RUN_HTTP_SERVER:
        logger.info("HTTP server disabled (RUN_HTTP_SERVER=false). Set to 'true' if needed.")
        return
    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            logger.info(f"HTTP server listening on port {port}")
            httpd.serve_forever()
    except OSError as e:
        logger.warning("HTTP server failed to start: %s", e)


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

# runtime state
BOT_START_TS = time.time()
IS_SCANNING = False
cooldowns_db = load_cooldowns()  # user_id(str) -> {'until': ts, 'reason': str}
COPYRIGHT_DEFAULT_COOLDOWN_MIN = 1440  # 24h
chat_languages = load_languages()  # chat_id(str) -> 'en'/'hi'

# Rate limit: min seconds between searches per user
SEARCH_COOLDOWN = 2

# Pagination
STORIES_PER_PAGE = 25


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
    async def _send():
        try:
            await asyncio.wait_for(
                context.bot.send_message(chat_id=LOG_CHANNEL, text=text),
                timeout=3
            )
        except Exception as e:
            logger.warning("Log send failed: %s", e)

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


def get_chat_lang(chat_id: int) -> str:
    """Return 'en' or 'hi' for this chat."""
    return chat_languages.get(str(chat_id), "en")


def set_chat_lang(chat_id: int, lang: str):
    chat_languages[str(chat_id)] = lang
    save_languages(chat_languages)


def _user_mention_by_id(user_id: int, fallback: str = "User") -> str:
    return f'<a href="tg://user?id={user_id}">{fallback}</a>' if user_id else fallback


def _normalize_story_query(text: str) -> str:
    """Attempt to extract story title from casual user phrases."""
    t = (text or "").lower().strip()
    t = re.sub(r"episode\s*\d+.*$", "", t).strip()
    t = re.sub(r"\b(link|pls|please|plz|do|de|dede|send|bhejo|give|anyone|can|kya|ki|ka|ke)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return clean_story(t)


def _looks_like_existing_story_query(text: str) -> bool:
    """Return True only if this text plausibly refers to an existing story title."""
    q = _normalize_story_query(text)
    if not q or len(q) < 2:
        return False

    # exact/contains matches first
    if fast_search(q):
        return True
    if fast_search_contains(q, limit=1):
        return True

    # fuzzy with token overlap safeguard
    fuzzy = fuzzy_search(q)
    if not fuzzy:
        return False
    name_tokens = set(clean_story(fuzzy.get("name", fuzzy.get("text", ""))).lower().split())
    query_tokens = set(clean_story(q).lower().split())
    stop = {"se", "ke", "ki", "the", "a", "an", "pls", "please", "anyone", "episode", "link"}
    return bool((name_tokens - stop) & (query_tokens - stop))


def _get_cooldown(user_id: int):
    entry = cooldowns_db.get(str(user_id))
    if not isinstance(entry, dict):
        return None
    until = entry.get("until")
    reason = entry.get("reason") or "cooldown"
    try:
        until_f = float(until)
    except Exception:
        return None
    return {"until": until_f, "reason": str(reason)}


def _set_cooldown(user_id: int, minutes: int, reason: str):
    until = time.time() + max(int(minutes), 1) * 60
    cooldowns_db[str(user_id)] = {"until": until, "reason": reason}
    save_cooldowns(cooldowns_db)


def _clear_cooldown(user_id: int):
    if str(user_id) in cooldowns_db:
        cooldowns_db.pop(str(user_id), None)
        save_cooldowns(cooldowns_db)


async def _enforce_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if user is blocked and we've informed them."""
    user = update.effective_user
    if not user:
        return False
    entry = _get_cooldown(user.id)
    if not entry:
        return False
    now = time.time()
    if now >= entry["until"]:
        _clear_cooldown(user.id)
        return False

    remaining = int(entry["until"] - now)
    mins = max(1, remaining // 60)
    lang = get_chat_lang(update.effective_chat.id) if update.effective_chat else "en"
    reason = entry.get("reason", "cooldown")

    if lang == "hi":
        text = (
            f"<b>⛔ आप cooldown पर हैं</b>\n\n"
            f"{user.mention_html()}\n"
            f"<i>कारण:</i> <b>{reason}</b>\n"
            f"<i>कृपया लगभग {mins} मिनट बाद फिर से कोशिश करें।</i>"
        )
    else:
        text = (
            f"<b>⛔ You are on cooldown</b>\n\n"
            f"{user.mention_html()}\n"
            f"<i>Reason:</i> <b>{reason}</b>\n"
            f"<i>Please try again after approximately {mins} minutes.</i>"
        )

    # reply to whatever is available
    try:
        target = update.message or (update.callback_query.message if update.callback_query else None)
        if target:
            await target.reply_text(text=text, parse_mode="HTML")
    except Exception:
        pass
    return True


async def _send_scan_busy_notice(msg, lang: str):
    """Send scan busy notice, auto-delete after 24h."""
    if lang == "hi":
        text = (
            "<b>⏳ कृपया प्रतीक्षा करें</b>\n\n"
            "<i>Riya अभी डेटाबेस से स्टोरीज़ अपडेट कर रही है।</i>\n"
            "कृपया थोड़ी देर बाद फिर से कोशिश करें।\n\n"
            f"<code>{datetime.utcnow().strftime('%d-%m-%Y %H:%M:%S')} UTC</code>"
        )
    else:
        text = (
            "<b>⏳ Please wait</b>\n\n"
            "<i>Riya is currently fetching and updating stories from the database.</i>\n"
            "Try again after some time.\n\n"
            f"<code>{datetime.utcnow().strftime('%d-%m-%Y %H:%M:%S')} UTC</code>"
        )

    notice = await msg.reply_text(text=text, parse_mode="HTML")

    async def _delete_notice():
        await asyncio.sleep(86400)
        try:
            await notice.delete()
        except Exception:
            pass

    asyncio.create_task(_delete_notice())


def init_search_index():
    """Bootstrap search index from DB if empty (e.g. after first deploy)."""
    global story_index, search_index, last_scan_count
    if search_index:
        return
    db = load_db()
    if not db:
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


async def auto_scan_loop(bot=None):
    """Periodically rescan channel and refresh search index (runs in background)."""
    global story_index, last_scan_count
    if not CHANNEL_ID or (str(AUTO_SCAN).lower() != "true"):
        return
    if not (SESSION_STRING and API_ID and API_HASH):
        logger.warning("Auto-scan skipped: SESSION_STRING, API_ID, API_HASH required for Telethon")
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
        except Exception as e:
            logger.error("Auto scan error: %s", e)


# -----------------------
# Welcome message
# -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return

    u = update.effective_user
    user = u.mention_html()
    chat = update.effective_chat
    lang = get_chat_lang(chat.id)

    if lang == "hi":
        text = f"""
<b>♡ नमस्ते, स्वागत है</b>, {user}

<blockquote>@StoriesFinderBot</blockquote>

Commands: / टाइप करें और मेनू से विकल्प चुनें – खोजें, रिक्वेस्ट करें या कहानियाँ एक्सप्लोर करें।

<blockquote><i>Disclaimer 📌
हम केवल Telegram फ़ाइलों को इंडेक्स करते हैं, हम कोई कंटेंट होस्ट नहीं करते।</i></blockquote>

<u>अपनी कहानी का नाम भेजकर शुरू करें!</u>

<b>By</b> @MeJeetX
"""
    else:
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
    if await _enforce_cooldown(update, context):
        return

    chat = update.effective_chat
    lang = get_chat_lang(chat.id)

    if lang == "hi":
        text = """
<b>📚 Riya Bot के बारे में</b>

<blockquote><i>Riya एक समझदार Telegram स्टोरी डिस्कवरी बॉट है जो आपको कई Telegram चैनलों में शेयर की गई कहानियाँ तुरंत ढूंढने में मदद करता है।</i></blockquote>

<u>✨ Riya क्या कर सकती है</u>

• तेज़ स्टोरी सर्च
• तुरंत डेटाबेस रिज़ल्ट
• स्टोरी रिक्वेस्ट सिस्टम
• स्टोरी अपलोड होने पर ऑटो नोटिफ़िकेशन

<b>👨‍💻 Developer:</b>
@MeJeetX

<b>⚙ Version:</b>
Riya Pie v11
"""
    else:
        text = """
<b>📚 About Riya Bot</b>

<blockquote><i>Riya is an intelligent Telegram story discovery bot that helps users find stories shared across multiple Telegram channels instantly.</i></blockquote>

<u>✨ What Riya Can Do</u>

• Fast Story Search
• Instant Database Results
• Story Request System
• Auto Notification when story is uploaded

<b>👨‍💻 Developer:</b>
@MeJeetX

<b>⚙ Version:</b>
Riya Pie v11
"""

    reply = await update.message.reply_text(text=text, parse_mode="HTML")

    async def _delete_cmd():
        await asyncio.sleep(300)
        try:
            await update.message.delete()
        except Exception:
            pass

    async def _delete_reply():
        await asyncio.sleep(1800)
        try:
            await reply.delete()
        except Exception:
            pass

    asyncio.create_task(_delete_cmd())
    asyncio.create_task(_delete_reply())


async def how(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return

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
    if await _enforce_cooldown(update, context):
        return

    chat = update.effective_chat
    lang = get_chat_lang(chat.id)

    if lang == "hi":
        text = """
<b>🆘 हेल्प सेंटर</b>

<i>इन कमांड्स से आप बॉट के साथ इंटरैक्ट कर सकते हैं:</i>

<u>/start</u> → बॉट शुरू करें
<u>/request</u> → स्टोरी रिक्वेस्ट करें
<u>/scan</u> → स्टोरी डेटाबेस रिफ्रेश करें [सिर्फ़ एडमिन]
<u>/info</u> → स्टोरी डिटेल्स
<u>/stats</u> → बॉट स्टैटिस्टिक्स [एडमिन]

<b>आप सीधे स्टोरी का नाम भेजकर भी सर्च कर सकते हैं।</b>
"""
    else:
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

    asyncio.create_task(_delete_later())


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return
    """Show story details: /info <title>"""
    if IS_SCANNING:
        lang = get_chat_lang(update.effective_chat.id)
        await _send_scan_busy_notice(update.message, lang)
        return
    if not context.args:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text(
            "Usage: /info <story name>" if lang != "hi" else "उपयोग: /info <स्टोरी का नाम>"
        )
        return
    query = " ".join(context.args).strip()
    results = fast_search(query) or fast_search_contains(query, limit=1)
    if not results:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Story not found." if lang != "hi" else "कहानी नहीं मिली।")
        return
    result = get_story(clean_story(results[0]).lower())
    if not result:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Story not found." if lang != "hi" else "कहानी नहीं मिली।")
        return
    name = clean_story(result.get("text", result.get("name", "")))
    link = result.get("link", "")
    stype = result.get("story_type") or extract_story_type(result.get("caption", ""))
    type_line = f"\n<b>Story Type:</b> {stype}" if stype else ""
    text = f"""<b>📖 {name}</b>{type_line}

<b>Link:</b> {link}"""
    await update.message.reply_text(text, parse_mode="HTML")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return
    """Bot statistics (admin only)."""
    if IS_SCANNING:
        lang = get_chat_lang(update.effective_chat.id)
        await _send_scan_busy_notice(update.message, lang)
        return
    if not is_admin(update.effective_user.id):
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("⛔ Admin only." if lang != "hi" else "⛔ केवल एडमिन।")
        return
    db = load_db()
    total_stories = len(db)
    total_requests = sum(len(v) for v in request_db.values()) if isinstance(request_db, dict) else 0
    unique_requests = len(request_db)
    text = f"""<b>📊 Bot Statistics</b>

<b>Stories in DB:</b> {total_stories}
<b>Indexed titles:</b> {len(story_index)}
<b>Unique requests:</b> {unique_requests}
<b>Total request count:</b> {total_requests}"""
    await update.message.reply_text(text, parse_mode="HTML")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return
    """Show bot uptime and basic stats in IST, auto-delete after delays."""
    if IS_SCANNING:
        lang = get_chat_lang(update.effective_chat.id)
        await _send_scan_busy_notice(update.message, lang)
        return
    user = update.effective_user
    chat = update.effective_chat
    cmd_msg = update.message

    # compute uptime
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    ist = now_utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    uptime_seconds = int(time.time() - BOT_START_TS)
    days, rem = divmod(uptime_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    uptime_parts = []
    if days:
        uptime_parts.append(f"{days}d")
    if hours or days:
        uptime_parts.append(f"{hours}h")
    uptime_parts.append(f"{minutes}m")
    uptime_str = " ".join(uptime_parts)

    db = load_db()
    total_stories = len(db)

    mention = user.mention_html() if user else "User"

    text = (
        f"<b>📊 Riya Status</b>\n\n"
        f"{mention}, here is the current status:\n\n"
        f"<b>⏱ Uptime:</b> <i>{uptime_str}</i>\n"
        f"<b>🕒 Local Time (IST):</b> <code>{ist.strftime('%d-%m-%Y %H:%M:%S')} IST</code>\n"
        f"<b>📚 Stories in database:</b> <i>{total_stories}</i>\n"
    )

    # include first page of stories header with total count
    if story_index:
        header_text, _, _, _ = _stories_page(0)
        text += f"\n{header_text}"

    reply = await chat.send_message(text=text, parse_mode="HTML")

    # delete command message after 5 minutes, reply after 24 hours
    async def _delete_cmd():
        await asyncio.sleep(300)
        try:
            await cmd_msg.delete()
        except Exception:
            pass

    async def _delete_reply():
        await asyncio.sleep(86400)
        try:
            await reply.delete()
        except Exception:
            pass

    asyncio.create_task(_delete_cmd())
    asyncio.create_task(_delete_reply())


# -----------------------
# /scan premium progress
# -----------------------

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return

    global story_index, last_scan_count, IS_SCANNING

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ This command is for admins only.")
        return

    if not CHANNEL_ID:
        await update.message.reply_text("⛔ CHANNEL_ID is not configured.")
        return

    if not (SESSION_STRING and API_ID and API_HASH):
        await update.message.reply_text(
            "⛔ Telethon credentials missing. Set SESSION_STRING, API_ID, API_HASH in .env"
        )
        return

    if IS_SCANNING:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text(
            "A scan is already in progress." if lang != "hi" else "स्कैन पहले से चल रहा है।"
        )
        return

    IS_SCANNING = True

    expected_total = last_scan_count or 0
    scan_start_ts = time.time()
    last_ui_update = 0.0
    last_story_name = ""
    last_found = 0

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

        async def _progress_cb(p):
            nonlocal last_ui_update, last_story_name, last_found
            now = time.time()
            last_story_name = (p.get("last_story") or "").strip()
            last_found = int(p.get("stories_found") or 0)

            # throttle UI edits (Telegram rate limits)
            if now - last_ui_update < 2.5:
                return
            last_ui_update = now

            elapsed = max(now - scan_start_ts, 1)
            rate = last_found / elapsed
            remaining = max(expected_total - last_found, 0) if expected_total else 0
            eta_s = int(remaining / rate) if (expected_total and rate > 0) else 0
            eta_text = f"`~{eta_s//60:02d}:{eta_s%60:02d}`" if eta_s else "`--:--`"

            # premium-ish live UI: show last story and counts
            safe_story = (last_story_name[:60] + "…") if len(last_story_name) > 60 else last_story_name
            await msg.edit_text(
                text=(
                    "🔎 *Riya Database Scan*\n\n"
                    f"*Status:* _Scanning & adding stories..._\n\n"
                    f"*Last Added:* _{safe_story}_\n"
                    f"*Stories Found:* *{last_found}*\n"
                    f"*Estimated Remaining:* {eta_text}\n\n"
                    "_Please wait until the database is fully updated._"
                ),
                parse_mode="Markdown",
            )

        result = await scan_channel(
            CHANNEL_ID,
            bot=context.bot,
            log_channel=LOG_CHANNEL,
            progress_cb=_progress_cb,
        )

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
        save_story_index(story_index)

        last_scan_count = result.get("stories", len(story_index))

        # Notify requesters for stories that are now available
        try:
            await _notify_fulfilled_requests(context)
        except Exception as e:
            logger.warning("Request notification failed: %s", e)

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

        lang = get_chat_lang(update.effective_chat.id)
        await msg.edit_text(
            text=(f"Scan failed\n{e}" if lang != "hi" else f"स्कैन फेल हो गया\n{e}")
        )

        await log(
            context,
            f"SCAN ERROR | {e}"
        )
    finally:
        IS_SCANNING = False

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

def _stories_page(page=0):
    """Build stories list for a given page."""
    start = page * STORIES_PER_PAGE
    end = start + STORIES_PER_PAGE
    chunk = story_index[start:end]
    lines = []
    for i, name in enumerate(chunk, start + 1):
        title = clean_story(name)
        # per‑story monospace so each can be copied individually
        lines.append(f"<code>{i} {title}</code>")
    total = len(story_index)
    header = f"<b>Available stories on this channel ({total}) 👇🏻</b>\n"
    text = header + "\n" + "\n".join(lines)
    has_next = end < total
    has_prev = page > 0
    return text, has_prev, has_next, page


async def stories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return

    if IS_SCANNING:
        lang = get_chat_lang(update.effective_chat.id)
        await _send_scan_busy_notice(update.message, lang)
        return

    if not story_index:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text(
            "No stories indexed yet. Run /scan first." if lang != "hi" else "अभी तक कोई स्टोरी इंडेक्स नहीं हुई। पहले /scan चलाएँ।"
        )
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

    cmd_msg = update.message

    reply = await update.message.reply_text(
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
        try:
            await cmd_msg.delete()
        except Exception:
            pass

    asyncio.create_task(_delete_later())


# -----------------------
# request story
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return

    if not context.args:

        chat = update.effective_chat
        lang = get_chat_lang(chat.id)

        if lang == "hi":
            warn_text = """
🎬 कृपया स्टोरी/सीरीज़ का नाम लिखें

📝 उदाहरण:
/request Vashikaran
/request Saaya
"""
        else:
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

    story_raw = " ".join(context.args).strip()
    story = clean_story(story_raw).lower()

    # if story already exists in DB, no need to request
    existing = get_story(story)
    if existing:
        link = existing.get("link", "")
        user = update.effective_user
        mention = user.mention_html() if user else ""
        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            text = f"""
<b>{mention}</b>

<b>इस स्टोरी को रिक्वेस्ट करने की जरूरत नहीं है।</b>
<i>यह पहले से हमारे डेटाबेस में मौजूद है।</i>

<b>Link:</b> <tg-spoiler>{link}</tg-spoiler>
"""
        else:
            text = f"""
<b>{mention}</b>

<b>No need to request this story.</b>
<i>It already exists in our database.</i>

<b>Link:</b> <tg-spoiler>{link}</tg-spoiler>
"""
        await update.effective_chat.send_message(text=text, parse_mode="HTML")
        try:
            await update.message.delete()
        except Exception:
            pass
        return

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

        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            text = f"""
<b>{mention}</b>

<i>आप पहले ही <b>{story}</b> रिक्वेस्ट कर चुके हैं।  
कृपया डुप्लीकेट रिक्वेस्ट न भेजें।</i>
"""
        else:
            text = f"""
<b>{mention}</b>

<i>You have already requested <b>{story}</b>.  
Please avoid sending duplicate requests.</i>
"""
        await update.effective_chat.send_message(text=text, parse_mode="HTML")

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

        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            text = f"""
<b>{mention}</b>

<b>आपकी <i>{story}</i> की रिक्वेस्ट भेज दी गई है।  
हम इसे उपलब्ध कराने की पूरी कोशिश करेंगे।  
जैसे ही मिलेगी, जल्द अपलोड कर दी जाएगी।</b>
"""
        else:
            text = f"""
<b>{mention}</b>

<b>Your request for <i>{story}</i> has been sent.  
We will try our best to provide this story.  
If we find it, it will be uploaded soon.</b>
"""

    else:

        others = count - 1

        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            text = f"""
<b>{mention}</b>

<b>आपके साथ {others} और लोगों ने भी <i>{story}</i> रिक्वेस्ट की है।  
हम इसे उपलब्ध कराने की पूरी कोशिश करेंगे।  
जैसे ही मिलेगी, जल्द अपलोड कर दी जाएगी।</b>
"""
        else:
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


async def _notify_fulfilled_requests(context: ContextTypes.DEFAULT_TYPE):
    """After a scan, notify chats where stories were requested if now available."""
    global request_db, request_chat

    if not isinstance(request_db, dict) or not request_db:
        return

    db = load_db()
    if not db:
        return

    fulfilled = []
    for story_key, user_ids in list(request_db.items()):
        if story_key in db:
            fulfilled.append(story_key)

    if not fulfilled:
        return

    for story_key in fulfilled:
        story = db.get(story_key) or {}
        link = story.get("link", "")
        chat_id = request_chat.get(story_key)
        user_ids = list(request_db.get(story_key) or [])
        if not chat_id or not user_ids:
            # cleanup anyway
            request_db.pop(story_key, None)
            request_chat.pop(story_key, None)
            continue

        mentions = " ".join([_user_mention_by_id(uid) for uid in user_ids])
        title = clean_story(story.get("text", story_key))
        # language based on the target chat
        lang = get_chat_lang(int(chat_id))
        if lang == "hi":
            text = (
                f"<b>✅ स्टोरी अब उपलब्ध है</b>\n\n"
                f"{mentions}\n\n"
                f"<b>{title}</b>\n"
                f"<b>Link:</b> {link}\n\n"
                f"<tg-spoiler>यह संदेश 24 घंटे बाद अपने आप डिलीट हो जाएगा।</tg-spoiler>"
            )
        else:
            text = (
                f"<b>✅ Story available now</b>\n\n"
                f"{mentions}\n\n"
                f"<b>{title}</b>\n"
                f"<b>Link:</b> {link}\n\n"
                f"<tg-spoiler>This message will be deleted automatically in 24 hours.</tg-spoiler>"
            )

        try:
            sent = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception:
            sent = None

        if sent:
            async def _del_later(m):
                await asyncio.sleep(86400)
                try:
                    await m.delete()
                except Exception:
                    pass
            asyncio.create_task(_del_later(sent))

        # remove fulfilled request
        request_db.pop(story_key, None)
        request_chat.pop(story_key, None)

    save_requests({"requests": request_db, "chats": request_chat})


# -----------------------
# search
# -----------------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return

    msg = update.message or update.channel_post
    if not msg or not getattr(msg, "text", None):
        return

    user = update.effective_user
    if not user:
        return  # e.g. channel_post without forward info

    raw_text = (msg.text or "").strip()

    if not raw_text or len(raw_text) < 2:
        return

    # ignore obvious small talk / random messages
    if not is_valid_query(raw_text):
        return

    query_text = raw_text

    # If a scan is running, tell user to wait
    if IS_SCANNING:
        # Only show busy notice for queries that plausibly refer to an existing story.
        if _looks_like_existing_story_query(query_text):
            lang = get_chat_lang(update.effective_chat.id)
            await _send_scan_busy_notice(msg, lang)
        return

    # Rate limit
    now = time.time()
    last = cooldown_db.get(user.id, 0)
    if now - last < SEARCH_COOLDOWN:
        return
    cooldown_db[user.id] = now

    # Try exact match first, then substring match (only from DB)
    fast_results = fast_search(query_text)
    if not fast_results:
        fast_results = fast_search_contains(query_text, limit=1)

    result = None
    if fast_results:
        candidate_name = fast_results[0]
        result = get_story(clean_story(candidate_name).lower())
    else:
        # fuzzy search with safeguards: allow small typos but avoid unrelated matches
        fuzzy = fuzzy_search(query_text)
        if fuzzy:
            name_tokens = set(clean_story(fuzzy.get("name", fuzzy.get("text", ""))).lower().split())
            query_tokens = set(clean_story(query_text).lower().split())
            stop = {"se", "ke", "ki", "the", "a", "an", "pls", "please", "anyone", "episode"}
            if name_tokens and query_tokens and (name_tokens - stop) & (query_tokens - stop):
                result = fuzzy

    if not result:
        await log(
            context,
            f"SEARCH MISS | user_id={user.id} username={user.username} query={query_text}"
        )
        suggestions = fast_search_contains(query_text, limit=5)
        if len(suggestions) >= 2:
            keyboard = []
            for s in suggestions:
                key = clean_story(s).lower()
                if len(f"srch|{key}") <= 64:
                    keyboard.append([InlineKeyboardButton(s, callback_data=f"srch|{key}")])
            lang = get_chat_lang(update.effective_chat.id)
            no_msg = await msg.reply_text(
                "❓ Did you mean one of these?" if lang != "hi" else "❓ क्या आप इनमें से कोई स्टोरी कहना चाहते थे?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            lang = get_chat_lang(update.effective_chat.id)
            no_msg = await msg.reply_text(
                ("❌ No story found with that name.\n\nCheck spelling or use /stories to see available titles."
                 if lang != "hi"
                 else "❌ इस नाम की कोई स्टोरी नहीं मिली।\n\nस्पेलिंग चेक करें या उपलब्ध टाइटल देखने के लिए /stories उपयोग करें।")
            )
        async def _del_no():
            await asyncio.sleep(30)
            try:
                await no_msg.delete()
            except Exception:
                pass
        asyncio.create_task(_del_no())
        return

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

    if await _enforce_cooldown(update, context):
        return

    if query.data.startswith("lang|"):
        lang = query.data.split("|", 1)[1]
        if lang not in ("en", "hi"):
            return
        chat_id = query.message.chat.id
        set_chat_lang(chat_id, lang)
        if lang == "hi":
            text = "✅ इस ग्रुप में Riya अब हिन्दी में जवाब देगी।"
        else:
            text = "✅ Riya will now respond in English in this chat."
        try:
            await query.message.edit_text(text=text)
        except Exception:
            await query.message.reply_text(text)
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
            asyncio.create_task(_del_later())
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

        # If user abuses claims, admin can apply cooldown via /copyright_mute.
        # (Cooldown is enforced globally by _enforce_cooldown above.)

        if key in claims_db:

            await query.answer(
                text="You already claimed this story",
                show_alert=True
            )
            return

        claims_db[key] = True
        save_claims(claims_db)

        if COPYRIGHT_CHANNEL:
            try:
                await context.bot.send_message(
                    chat_id=COPYRIGHT_CHANNEL,
                    text=f"""
Copyright Claim

Story: {story}
User: @{user.username if user.username else user.first_name}
ID: {user.id}
"""
                )
            except Exception as e:
                logger.warning("Failed to send copyright claim to channel: %s", e)

        lang = get_chat_lang(query.message.chat.id)
        if lang == "hi":
            text = (
                f"✅ <b>कॉपीराइट क्लेम प्राप्त हुआ</b>\n\n"
                f"{user.mention_html()}, आपका क्लेम सबमिट हो गया है।\n"
                f"<i>अगर आपको लगता है कि कॉपीराइट समस्या है, हमारी टीम जांच करके उचित कार्रवाई करेगी।</i>"
            )
        else:
            text = (
                f"✅ <b>Copyright claim received</b>\n\n"
                f"{user.mention_html()}, your claim has been submitted.\n"
                f"<i>If you believe there is a copyright issue, our team will review it and take appropriate action.</i>"
            )
        await query.message.reply_text(text=text, parse_mode="HTML")


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


async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when the bot is added to a group: set default language + show permissions + language buttons."""
    chat_member = update.my_chat_member
    if not chat_member:
        return

    chat = chat_member.chat
    new_status = chat_member.new_chat_member.status
    old_status = chat_member.old_chat_member.status

    # bot added to a chat
    if old_status in ("left", "kicked") and new_status in ("member", "administrator"):
        # default language English
        set_chat_lang(chat.id, "en")

        text = (
            "<b>👋 Thanks for adding Riya Bot</b>\n\n"
            "<i>To work properly, Riya needs:</i>\n"
            "• Permission to read messages\n"
            "• Permission to send messages\n"
            "• (Optional) Permission to delete messages for auto-cleanup\n\n"
            "<b>Select bot language for this chat:</b>"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("English", callback_data="lang|en"),
                    InlineKeyboardButton("हिन्दी", callback_data="lang|hi"),
                ]
            ]
        )

        await context.bot.send_message(
            chat_id=chat.id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


# -----------------------
# admin commands: announce & copyright mute
# -----------------------

async def announce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: send announcement to the current chat."""

    if not is_admin(update.effective_user.id):
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("⛔ Admin only." if lang != "hi" else "⛔ केवल एडमिन।")
        return

    if not context.args:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Usage: /announce <message>" if lang != "hi" else "उपयोग: /announce <मैसेज>")
        return

    text = " ".join(context.args)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    lang = get_chat_lang(update.effective_chat.id)
    await update.message.reply_text("✅ Announcement sent." if lang != "hi" else "✅ अनाउंसमेंट भेज दिया गया।")


async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: set language for this chat: /setlang en|hi."""
    if not is_admin(update.effective_user.id):
        lang0 = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("⛔ Admin only." if lang0 != "hi" else "⛔ केवल एडमिन।")
        return
    if not context.args:
        lang0 = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Usage: /setlang en|hi" if lang0 != "hi" else "उपयोग: /setlang en|hi")
        return
    lang = context.args[0].strip().lower()
    if lang not in ("en", "hi"):
        lang0 = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Usage: /setlang en|hi" if lang0 != "hi" else "उपयोग: /setlang en|hi")
        return
    set_chat_lang(update.effective_chat.id, lang)
    await update.message.reply_text("✅ Updated." if lang == "en" else "✅ भाषा अपडेट हो गई।")


async def copyright_mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: put a user on cooldown for copyright claims."""
    if not is_admin(update.effective_user.id):
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("⛔ Admin only." if lang != "hi" else "⛔ केवल एडमिन।")
        return

    if not context.args:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text(
            "Usage: /copyright_mute <user_id> [minutes] [reason...]" if lang != "hi"
            else "उपयोग: /copyright_mute <user_id> [minutes] [reason...]"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("First argument must be a user id.")
        return

    minutes = COPYRIGHT_DEFAULT_COOLDOWN_MIN
    if len(context.args) >= 2:
        try:
            minutes = int(context.args[1])
        except ValueError:
            pass
    reason = "false copyright claim"
    if len(context.args) >= 3:
        reason = " ".join(context.args[2:]).strip() or reason

    _set_cooldown(target_id, minutes=minutes, reason=reason)

    await update.message.reply_text(
        f"✅ User {target_id} is on cooldown for {minutes} minutes.\nReason: {reason}"
    )


# -----------------------
# start bot
# -----------------------

def start_bot():

    global app

    init_search_index()

    async def _post_init(application):
        if str(AUTO_SCAN).lower() == "true" and CHANNEL_ID:
            asyncio.create_task(auto_scan_loop(application.bot))

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("stories", stories))
    app.add_handler(CommandHandler("request", request_story))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("how", how))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("status", status_cmd))

    # admin-only utility commands
    app.add_handler(CommandHandler("announce", announce_cmd))
    app.add_handler(CommandHandler("copyright_mute", copyright_mute_cmd))
    app.add_handler(CommandHandler("setlang", setlang_cmd))

    # react when bot is added/removed
    app.add_handler(ChatMemberHandler(chat_member_update))

    app.add_handler(InlineQueryHandler(inline_search))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search)
    )

    app.add_handler(CallbackQueryHandler(buttons))

    logger.info("Riya Bot running")

    # drop_pending_updates avoids processing stale updates that may cause issues after restart
    app.run_polling(drop_pending_updates=True)


# -----------------------
# main
# -----------------------

def _validate_config():
    """Fail fast with clear errors if critical config is missing."""
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token":
        logger.error("BOT_TOKEN is not set. Create a .env file or set the environment variable.")
        sys.exit(1)
    if CHANNEL_ID == 0:
        logger.warning("CHANNEL_ID is 0 or unset. /scan and auto-scan will not work.")
    if REQUEST_GROUP == 0:
        logger.warning("REQUEST_GROUP is 0 or unset. /request will fail.")
    logger.info("Config validation OK")


def main():
    _validate_config()

    if RUN_HTTP_SERVER:
        http_thread = threading.Thread(target=start_server, daemon=True)
        http_thread.start()

    def shutdown(signum=None, frame=None):
        logger.info("Shutdown signal received, stopping bot...")
        if "app" in globals() and app:
            try:
                app.updater.stop()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    start_bot()


if __name__ == "__main__":
    main()
