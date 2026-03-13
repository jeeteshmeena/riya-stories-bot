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
import json
import html
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
from link_checker import start_link_checker
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
    load_link_flags,
    save_link_flags,
    load_config,
    save_config,
    remove_stories_not_in,
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
link_flags = load_link_flags()  # story_key -> {'broken': bool, 'link': str, 'voters': [{'id','name'}], 'chats': [int]}
active_link_votes = {}  # vote_id -> {'story_key', 'chat_id', 'message_id', 'voters': {user_id: name}, 'link', 'story_name'}
bot_config = load_config()

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
    safe = html.escape(fallback or str(user_id))
    return f'<a href="tg://user?id={user_id}">{safe}</a>' if user_id else safe


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


async def _is_link_alive(url: str) -> bool:
    """Best-effort HTTP check to see if a t.me link is reachable."""
    try:
        import httpx  # uses dependency from python-telegram-bot
    except ImportError:
        return True  # fall back to assuming OK if httpx not available

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
            resp = await client.get(url)
        # Telegram often returns 200 for valid links
        return resp.status_code == 200
    except Exception:
        return False


async def link_check_loop(bot=None):
    """Background loop that periodically verifies stored links and clears/restores flags."""
    while True:
        try:
            await asyncio.sleep(3600)
            db = load_db()
            if not db:
                continue
            changed = False
            for key, story in list(db.items()):
                link = story.get("link")
                if not link:
                    continue
                alive = await _is_link_alive(link)
                lf = link_flags.get(key) or {}
                was_broken = lf.get("broken", False)
                # mark newly broken
                if not alive and not was_broken:
                    lf.update(
                        {
                            "broken": True,
                            "link": link,
                            "voters": lf.get("voters") or [],
                            "chats": lf.get("chats") or [],
                        }
                    )
                    link_flags[key] = lf
                    changed = True
                # restore if link is back
                elif alive and was_broken:
                    voters = lf.get("voters") or []
                    chats = lf.get("chats") or []
                    title = clean_story(story.get("text", key))
                    for chat_id in chats:
                        lang = get_chat_lang(chat_id)
                        mentions = " ".join(
                            _user_mention_by_id(v.get("id"), v.get("name", str(v.get("id")))) for v in voters
                        )
                        if lang == "hi":
                            text = (
                                f"<b>✅ लिंक फिर से काम कर रहा है</b>\n\n"
                                f"{mentions}\n\n"
                                f"<i>{title}</i>\n"
                                f"<b>Link:</b> {link}"
                            )
                        else:
                            text = (
                                f"<b>✅ Link is working again</b>\n\n"
                                f"{mentions}\n\n"
                                f"<i>{title}</i>\n"
                                f"<b>Link:</b> {link}"
                            )
                        try:
                            sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
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
                    # clear flag
                    link_flags.pop(key, None)
                    changed = True

            if changed:
                save_link_flags(link_flags)
        except Exception as e:
            logger.error("Link check loop error: %s", e)


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

<blockquote><i>Riya एक समझदार Telegram स्टोरी खोज बॉट है जो उपयोगकर्ताओं को कई Telegram चैनलों में साझा की गई कहानियाँ तुरंत खोजने में मदद करता है।</i></blockquote>

<u>✨ विशेषताएं</u>

• AI फजी सर्च
• प्रोग्रेस बार रिप्लाई
• स्पैम फिल्टर
• भाषा सिस्टम
• इनलाइन बटन
• JSON डेटाबेस
• एडमिन स्टैट्स, /scan, /request

<b>👨‍💻 डेवलपर:</b>
@MeJeetX

<b>⚙ Version:</b>
Riya v10
"""
    else:
        text = """
<b>📚 About Riya Bot</b>

<blockquote><i>Riya is an intelligent Telegram story finder bot with AI fuzzy search, inline buttons, and admin stats.</i></blockquote>

<u>✨ Features</u>

• AI fuzzy search
• Progress bar replies
• Spam filter
• Language system
• Inline buttons
• JSON database
• Admin stats, /scan, /request

<b>👨‍💻 Developer:</b>
@MeJeetX

<b>⚙ Version:</b>
Riya v10
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

    # Load additional statistics
    requests_data = load_requests()
    total_requests = len(requests_data.get("requests", {}))
    
    link_flags_data = load_link_flags()
    total_broken_reports = len([flag for flag in link_flags_data.values() if flag.get("broken", False)])

    text = (
        f"<b>📊 Riya Status</b>\n\n"
        f"{mention}, here is the current bot status:\n\n"
        f"<b>⏱ Uptime:</b> <i>{uptime_str}</i>\n"
        f"<b>🕒 Local Time (IST):</b> <code>{ist.strftime('%d-%m-%Y %H:%M:%S')} IST</code>\n"
        f"<b>📚 Stories in database:</b> <i>{total_stories}</i>\n"
        f"<b>📝 Total story requests:</b> <i>{total_requests}</i>\n"
        f"<b>⚠️ Broken link reports:</b> <i>{total_broken_reports}</i>\n"
        f"<b>🤖 Bot status:</b> <i>Running normally</i>\n"
    )

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

        # build source channel list: primary + extra from config
        sources = []
        if CHANNEL_ID:
            sources.append(CHANNEL_ID)
        extra_sources = bot_config.get("sources", [])
        for cid in extra_sources:
            try:
                c_int = int(cid)
            except Exception:
                continue
            if c_int and c_int not in sources:
                sources.append(c_int)

        all_names = []
        all_keys = set()
        total_stories_found = 0

        formats_by_channel = bot_config.get("formats", {})

        for idx, cid in enumerate(sources):
            result = await scan_channel(
                cid,
                bot=context.bot,
                log_channel=LOG_CHANNEL,
                progress_cb=_progress_cb if idx == 0 else None,
                cleanup=False,
                formats_by_channel=formats_by_channel,
            )
            all_names.extend(result.get("names", []))
            all_keys.update(result.get("keys", []))
            total_stories_found += result.get("stories", 0)

        # Cleanup once using union of all keys
        if all_keys:
            remove_stories_not_in(all_keys)

        # de-duplicate names preserving order
        seen_names = set()
        uniq_names = []
        for n in all_names:
            if n not in seen_names:
                seen_names.add(n)
                uniq_names.append(n)

        story_index = uniq_names

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

        # clear link flags for stories whose link has been updated/confirmed again
        db = load_db()
        changed = False
        for key, flag in list(link_flags.items()):
            if key in db:
                new_link = db[key].get("link", "")
                if flag.get("link") and new_link and new_link != flag["link"]:
                    link_flags.pop(key, None)
                    changed = True
        if changed:
            save_link_flags(link_flags)

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

    # Delete command message after 5 seconds
    async def _delete_cmd():
        await asyncio.sleep(5)
        try:
            await cmd_msg.delete()
        except Exception:
            pass
    
    asyncio.create_task(_delete_cmd())

    reply = await update.message.reply_text(
        text=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
    )

    await log(
        context,
        f"STORIES | user_id={cmd_msg.from_user.id} username={cmd_msg.from_user.username}"
    )

    # Delete command message quickly (after 5 seconds)
    async def _delete_cmd():
        await asyncio.sleep(5)
        try:
            await cmd_msg.delete()
        except Exception:
            pass

    # Delete reply later (after 30 minutes)
    async def _delete_reply():
        await asyncio.sleep(1800)
        try:
            await reply.delete()
        except Exception:
            pass

    asyncio.create_task(_delete_cmd())
    asyncio.create_task(_delete_reply())


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

    chat_id = str(update.effective_chat.id)

    if story not in request_db:
        request_db[story] = {}
    if chat_id not in request_db[story]:
        request_db[story][chat_id] = set()

    if user.id in request_db[story][chat_id]:

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

    request_db[story][chat_id].add(user.id)

    # total count across all chats
    count = sum(len(uids) for uids in request_db[story].values())

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
    save_requests({"requests": request_db})

    await log(
        context,
        f"REQUEST | user_id={user.id} username={user.username} story={story}"
    )


async def _notify_fulfilled_requests(context: ContextTypes.DEFAULT_TYPE):
    """After a scan, notify chats where stories were requested if now available."""
    global request_db

    if not isinstance(request_db, dict) or not request_db:
        return

    db = load_db()
    if not db:
        return

    # story_key -> per-chat user sets
    for story_key, chats in list(request_db.items()):
        if story_key not in db or not isinstance(chats, dict):
            continue
        story = db.get(story_key) or {}
        link = story.get("link", "")
        title = clean_story(story.get("text", story_key))

        # notify each chat separately
        for chat_id_str, users in list(chats.items()):
            if not users:
                continue
            chat_id = int(chat_id_str)
            mentions = " ".join([_user_mention_by_id(uid) for uid in users])
            lang = get_chat_lang(chat_id)
            if lang == "hi":
                text = (
                    f"<b>✅ {mentions}</b>\n\n"
                    f"<i>{title}</i>\n\n"
                    f"<b>यहाँ पढ़ें:</b> {link}"
                )
            else:
                text = (
                    f"<b>✅ {mentions}</b>\n\n"
                    f"<i>{title}</i>\n\n"
                    f"<b>Read here:</b> {link}"
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

            # clear this chat's users for this story
            request_db[story_key][chat_id_str] = set()

        # drop story entry if all chats cleared
        if not any(request_db[story_key].values()):
            request_db.pop(story_key, None)

    save_requests({"requests": request_db})


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
    story_key = result.get("name") or clean_story(result["text"]).lower()

    # Prefer pre‑computed story_type from the scanner, fallback to regex
    story_type = result.get("story_type")

    if not story_type:
        caption_text = result.get("caption", "")
        story_type = extract_story_type(caption_text)

    if not story_type:
        story_type = "Not specified"

    keyboard = [
        [InlineKeyboardButton("OPEN STORY", url=result["link"])],
        [InlineKeyboardButton("⚠ Link Not Working?", callback_data=f"lnw|{story_key}")],
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
            video="https://files.catbox.moe/rq7km7.mp4",
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

    if query.data.startswith("cfg|"):
        await _handle_config_callback(query, context)
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
        story_key = result.get("name") or story_name.lower()
        story_type = result.get("story_type") or extract_story_type(result.get("caption", "")) or "Not specified"
        story_type_line = f"\n<b>Story Type:-</b> <i>{story_type}</i>" if story_type != "Not specified" else ""
        caption = f"""Hey {mention} 👋
<b>I found this story</b> 👇

<i>Name:-</i> <b>{story_name}</b>{story_type_line}

<tg-spoiler>This reply will be deleted automatically in 5 minutes.</tg-spoiler>
"""
        keyboard = [
            [InlineKeyboardButton("OPEN STORY", url=result["link"])],
            [InlineKeyboardButton("⚠ Link Not Working?", callback_data=f"lnw|{story_key}")],
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
                    video="https://files.catbox.moe/rq7km7.mp4",
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

    # -----------------------
    # Link-not-working voting flow
    # -----------------------

    if query.data.startswith("lnw|"):
        # initial "Link Not Working?" click – only requester can start
        story_key = query.data.split("|", 1)[1]
        owner = message_owner.get(query.message.message_id)
        if owner is not None and owner != user.id:
            lang = get_chat_lang(query.message.chat.id)
            if lang == "hi":
                warn_text = "<b>केवल वही यूज़र लिंक रिपोर्ट कर सकता है, जिसने स्टोरी सर्च की हो।</b>"
            else:
                warn_text = "<b>Only the original requester can report this link.</b>"
            warn = await query.message.reply_text(warn_text, parse_mode="HTML")

            async def _del_warn():
                await asyncio.sleep(15)
                try:
                    await warn.delete()
                except Exception:
                    pass

            asyncio.create_task(_del_warn())
            return

        story = get_story(story_key)
        if not story:
            await query.answer("Story not found.", show_alert=True)
            return
        
        chat_id = query.message.chat.id
        user_id = user.id
        story_name = clean_story(story.get("text", story.get("name", "")))
        
        # Anti-spam: Check if user already reported this story
        existing_flag = link_flags.get(story_key)
        if existing_flag and existing_flag.get("broken"):
            # Story already confirmed as broken
            lang = get_chat_lang(chat_id)
            if lang == "hi":
                txt = f"<b>आप पहले ही इस स्टोरी की रिपोर्ट कर चुके हैं।</b>\n\n<i>यह स्टोरी पहले से ही टूटी हुई के रूप में मार्क कर दी गई है। कृपया समाधान का इंतजार करें।</i>"
            else:
                txt = f"<b>You have already submitted a report for this story.</b>\n\n<i>This story is already marked as broken. Please wait while the issue is being resolved.</i>"
            await query.answer(txt, show_alert=True)
            return

        # Check if user has already voted in this specific vote
        vote_id = f"{chat_id}:{story_key}"
        existing_vote = active_link_votes.get(vote_id)
        if existing_vote and user_id in existing_vote.get("voters", {}):
            lang = get_chat_lang(chat_id)
            if lang == "hi":
                txt = f"<b>आप पहले ही इस वेरिफिकेशन में भाग ले चुके हैं।</b>\n\n<i>कृपया समस्या के समाधान का इंतजार करें।</i>"
            else:
                txt = f"<b>You have already participated in this verification.</b>\n\n<i>Please wait while the issue is being resolved.</i>"
            await query.answer(txt, show_alert=True)
            return
        
        lang = get_chat_lang(chat_id)
        if lang == "hi":
            text = (
                f"<b>⚠ लिंक रिपोर्त करना चाहते हैं?</b>\n\n"
                f"<i>{story_name}</i>\n\n"
                "अगर यह स्टोरी लिंक सच में काम नहीं कर रहा है, तो नीचे कन्फर्म करें।"
            )
            confirm_label = "✅ कन्फर्म रिपोर्ट"
            cancel_label = "❌ कैंसल"
        else:
            text = (
                f"<b>⚠ Report link not working?</b>\n\n"
                f"<i>{story_name}</i>\n\n"
                "If this story link is really broken, please confirm below."
            )
            confirm_label = "✅ Confirm"
            cancel_label = "❌ Cancel"

        reporter_id = owner or user.id

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(confirm_label, callback_data=f"lnw_confirm|{story_key}|{reporter_id}"),
                    InlineKeyboardButton(cancel_label, callback_data=f"lnw_cancel|{reporter_id}"),
                ]
            ]
        )
        conf = await query.message.reply_text(text=text, parse_mode="HTML", reply_markup=kb)

        async def _del_conf():
            await asyncio.sleep(3600)
            try:
                await conf.delete()
            except Exception:
                pass

        asyncio.create_task(_del_conf())
        return

    if query.data.startswith("lnw_cancel|"):
        try:
            _, reporter_raw = query.data.split("|", 1)
            reporter_id = int(reporter_raw)
        except Exception:
            reporter_id = None

        if reporter_id is not None and reporter_id != user.id:
            lang = get_chat_lang(query.message.chat.id)
            txt = "<b>Only the requester can cancel this report.</b>" if lang != "hi" else "<b>केवल requester ही इस रिपोर्ट को कैंसल कर सकता है।</b>"
            warn = await query.message.reply_text(txt, parse_mode="HTML")

            async def _del_warn():
                await asyncio.sleep(15)
                try:
                    await warn.delete()
                except Exception:
                    pass

            asyncio.create_task(_del_warn())
            return

        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if query.data.startswith("lnw_confirm|"):
        try:
            _, rest = query.data.split("|", 1)
            story_key, reporter_raw = rest.split("|", 1)
            reporter_id = int(reporter_raw)
        except Exception:
            story_key = query.data.split("|", 1)[1]
            reporter_id = None

        story = get_story(story_key)
        if not story:
            await query.answer("Story not found.", show_alert=True)
            return
        chat_id = query.message.chat.id
        user_id = user.id

        if reporter_id is not None and reporter_id != user_id:
            lang = get_chat_lang(chat_id)
            txt = "<b>Only the requester can confirm this report.</b>" if lang != "hi" else "<b>केवल requester ही इस रिपोर्ट को कन्फर्म कर सकता है।</b>"
            warn = await query.message.reply_text(txt, parse_mode="HTML")

            async def _del_warn():
                await asyncio.sleep(15)
                try:
                    await warn.delete()
                except Exception:
                    pass
        
        # Check if user has already reported this story (anti-spam)
        existing_flag = link_flags.get(story_key)
        if existing_flag and existing_flag.get("broken"):
            # Story already confirmed as broken
            lang = get_chat_lang(chat_id)
            if lang == "hi":
                txt = f"<b>आप पहले ही इस स्टोरी की रिपोर्ट कर चुके हैं।</b>\n\n<i>यह स्टोरी पहले से ही टूटी हुई के रूप में मार्क कर दी गई है। कृपया समाधान का इंतजार करें।</i>"
            else:
                txt = f"<b>You have already submitted a report for this story.</b>\n\n<i>This story is already marked as broken. Please wait while the issue is being resolved.</i>"
            warn = await query.message.reply_text(txt, parse_mode="HTML")
            
            async def _del_warn():
                await asyncio.sleep(15)
                try:
                    await warn.delete()
                except Exception:
                    pass
            
            asyncio.create_task(_del_warn())
            return

        # Check if user has already voted in this specific vote
        vote_id = f"{chat_id}:{story_key}"
        existing_vote = active_link_votes.get(vote_id)
        if existing_vote and user_id in existing_vote.get("voters", {}):
            lang = get_chat_lang(chat_id)
            if lang == "hi":
                txt = f"<b>आप पहले ही इस वोट में भाग ले चुके हैं।</b>\n\n<i>आपका वोट पहले से दर्ज है। कृपया दोहराएं नहीं।</i>"
            else:
                txt = f"<b>You have already participated in this vote.</b>\n\n<i>Your vote is already registered. Please do not repeat.</i>"
            warn = await query.message.reply_text(txt, parse_mode="HTML")
            
            async def _del_warn():
                await asyncio.sleep(15)
                try:
                    await warn.delete()
                except Exception:
                    pass
            
            asyncio.create_task(_del_warn())
            return

        # delete confirmation panel immediately after confirm
        try:
            await query.message.delete()
        except Exception:
            pass
        story_name = clean_story(story.get("text", story.get("name", "")))
        link = story.get("link", "")

        vote_id = f"{chat_id}:{story_key}"
        vote = active_link_votes.get(vote_id)
        if not vote:
            vote = {
                "story_key": story_key,
                "chat_id": chat_id,
                "message_id": None,
                "voters": {},  # user_id -> display_name
                "link": link,
                "story_name": story_name,
            }
            active_link_votes[vote_id] = vote
        display_name = user.full_name or user.first_name or str(user_id)
        vote["voters"][user_id] = display_name

        current = len(vote["voters"])
        required = 3

        lang = get_chat_lang(chat_id)
        if lang == "hi":
            title = "<b>⚠ लिंक वेरीफिकेशन वोट</b>"
            body = f"<i>{story_name}</i>\n\n"
            body += f"<b>वोट का उद्देश्य:</b> यह जांचना के लिए कि स्टोरी लिंक काम कर रहा है या टूटा हुआ है\n\n"
            votes_line = f"<b>वोट:</b> {current} / {required} (कुल {required - current} और वोट चाहिए)"
            broken_label = "❌ लिंक नहीं चल रहा"
            ok_label = "🔗 चल रहा है"
        else:
            title = "<b>⚠ Link Verification Vote</b>"
            body = f"<i>{story_name}</i>\n\n"
            body += f"<b>Purpose:</b> To verify if the story link is working or broken\n\n"
            votes_line = f"<b>Votes:</b> {current} / {required} ({required - current} more votes needed)"
            broken_label = "🔗 Broken"
            ok_label = "🔗 Working"

        text = f"{title}\n\n{body}{votes_line}"
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(broken_label, callback_data=f"lnwv_broken|{vote_id}"),
                    InlineKeyboardButton(ok_label, callback_data=f"lnwv_ok|{vote_id}"),
                ]
            ]
        )

        if vote["message_id"]:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=vote["message_id"],
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception:
                pass
        else:
            sent = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=kb)
            vote["message_id"] = sent.message_id
            try:
                await context.bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
            except Exception:
                pass
            # auto-unpin and delete vote after 24h if not resolved
            async def _cleanup_vote(m_id, v_id, c_id):
                await asyncio.sleep(86400)
                v = active_link_votes.pop(v_id, None)
                try:
                    await context.bot.unpin_chat_message(chat_id=c_id, message_id=m_id)
                except Exception:
                    pass
                try:
                    await context.bot.delete_message(chat_id=c_id, message_id=m_id)
                except Exception:
                    pass
            asyncio.create_task(_cleanup_vote(sent.message_id, vote_id, chat_id))
        return

    if query.data.startswith("lnwv_"):
        action, vote_id = query.data.split("|", 1)
        vote = active_link_votes.get(vote_id)
        if not vote:
            await query.answer()
            return
        chat_id = vote["chat_id"]
        story_key = vote["story_key"]
        story_name = vote["story_name"]
        link = vote["link"]

        # update voters (one vote per user; they can switch between broken/ok)
        if action == "lnwv_broken":
            display_name = user.full_name or user.first_name or str(user.id)
            vote["voters"][user.id] = display_name
        elif action == "lnwv_ok":
            vote["voters"].pop(user.id, None)

        current = len(vote["voters"])
        required = 3

        lang = get_chat_lang(chat_id)
        if lang == "hi":
            title = "<b>⚠ लिंक वेरीफिकेशन वोट</b>"
            body = f"<i>{story_name}</i>\n\n"
            body += f"<b>वोट का उद्देश्य:</b> यह जांचना के लिए कि स्टोरी लिंक काम कर रहा है या टूटा हुआ है\n\n"
            votes_line = f"<b>वोट:</b> {current} / {required} (कुल {required - current} और वोट चाहिए)"
            broken_label = "❌ लिंक नहीं चल रहा"
            ok_label = "🔗 चल रहा है"
        else:
            title = "<b>⚠ Link Verification Vote</b>"
            body = f"<i>{story_name}</i>\n\n"
            body += f"<b>Purpose:</b> To verify if the story link is working or broken\n\n"
            votes_line = f"<b>Votes:</b> {current} / {required} ({required - current} more votes needed)"
            broken_label = "🔗 Broken"
            ok_label = "🔗 Working"

        text = f"{title}\n\n{body}{votes_line}"
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(broken_label, callback_data=f"lnwv_broken|{vote_id}"),
                    InlineKeyboardButton(ok_label, callback_data=f"lnwv_ok|{vote_id}"),
                ]
            ]
        )

        # update vote message
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=vote["message_id"],
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            pass

        # check threshold
        if current >= required and not vote.get("completed", False):
            # Mark as completed to prevent duplicate processing
            vote["completed"] = True
            
            voter_items = list(vote["voters"].items())
            mentions = " ".join(_user_mention_by_id(uid, name) for uid, name in voter_items)
            if lang == "hi":
                final_text = (
                    f"<b>✅ लिंक टूटा हुआ कन्फर्म हो गया</b>\n\n"
                    f"{mentions}\n\n"
                    f"<i>{story_name}</i>\n"
                    f"<b>Link:</b> {link}"
                )
            else:
                final_text = (
                    f"<b>✅ Link confirmed broken</b>\n\n"
                    f"{mentions}\n\n"
                    f"<i>{story_name}</i>\n"
                    f"<b>Link:</b> {link}"
                )
            try:
                await context.bot.send_message(chat_id=chat_id, text=final_text, parse_mode="HTML")
            except Exception:
                pass

            # Delete the vote message immediately to prevent further voting
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=vote["message_id"])
            except Exception:
                pass
            
            # Unpin the message
            try:
                await context.bot.unpin_chat_message(chat_id=chat_id, message_id=vote["message_id"])
            except Exception:
                pass

            # persist flag
            lf = link_flags.get(story_key) or {}
            existing_voters = {v.get("id") for v in (lf.get("voters") or [])}
            voter_objs = lf.get("voters") or []
            for uid, name in voter_items:
                if uid not in existing_voters:
                    voter_objs.append({"id": uid, "name": name})
            lf.update(
                {
                    "broken": True,
                    "link": link,
                    "voters": voter_objs,
                    "chats": list(set((lf.get("chats") or []) + [chat_id])),
                }
            )
            link_flags[story_key] = lf
            save_link_flags(link_flags)

            # notify admin/copyright channel
            if COPYRIGHT_CHANNEL:
                voters_txt = "\n".join([f"- {_user_mention_by_id(uid, fallback=str(uid))} (id: {uid})" for uid, name in voter_items])
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
                report = (
                    f"⚠ Link Broken Report\n\n"
                    f"📖 Story: {story_name}\n"
                    f"🔑 Story key: {story_key}\n"
                    f"🔗 Broken Link: {link}\n"
                    f"💬 Chat ID: {chat_id}\n"
                    f"⏰ Timestamp: {timestamp}\n\n"
                    f"👥 Voters:\n{voters_txt}"
                )
                try:
                    await context.bot.send_message(chat_id=COPYRIGHT_CHANNEL, text=report, parse_mode="HTML")
                except Exception as e:
                    logger.warning("Failed to send link broken report: %s", e)

# clean up vote
            active_link_votes.pop(vote_id, None)

        return


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
# admin config panel (/config) and source management
# -----------------------

async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for admin config panel."""
    if not is_admin(update.effective_user.id):
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("⛔ Admin only." if lang != "hi" else "⛔ केवल एडमिन।")
        return

    lang = get_chat_lang(update.effective_chat.id)
    if lang == "hi":
        text = (
            "<b>⚙ Riya Config Panel</b>\n\n"
            "<i>नीचे दिए गए सेक्शन्स से बॉट की सेटिंग्स मैनेज करें:</i>\n"
        )
        buttons = [
            [InlineKeyboardButton(" Source Channels & Formats", callback_data="cfg|sources")],
            [InlineKeyboardButton("⏱ Auto Delete Timers", callback_data="cfg|timers")],
            [InlineKeyboardButton("🌐 Language", callback_data="cfg|lang")],
        ]
    else:
        text = (
            "<b>⚙ Riya Config Panel</b>\n\n"
            "<i>Use the sections below to manage bot settings:</i>\n"
        )
        buttons = [
            [InlineKeyboardButton("📚 Source Channels & Formats", callback_data="cfg|sources")],
            [InlineKeyboardButton("⏱ Auto Delete Timers", callback_data="cfg|timers")],
            [InlineKeyboardButton("🌐 Language", callback_data="cfg|lang")],
        ]

    await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _handle_config_callback(query, context: ContextTypes.DEFAULT_TYPE):
    """Handle cfg| callbacks for the simple config panel."""
    data = query.data
    _, section = data.split("|", 1)
    chat_id = query.message.chat.id
    lang = get_chat_lang(chat_id)

    if section == "sources":
        sources = bot_config.get("sources", [])
        if lang == "hi":
            header = "<b>📚 सोर्स चैनल</b>\n\n"
            if sources:
                body = "<i>अभी जो चैनल स्कैन हो रहे हैं:</i>\n" + "\n".join(f"- <code>{cid}</code>" for cid in sources)
            else:
                body = "<i>अभी कोई extra सोर्स चैनल सेट नहीं है।</i>"
            footer = "\n\n<code>/addsource &lt;channel_id&gt;</code>\n<code>/removesource &lt;channel_id&gt;</code>"
        else:
            header = "<b>📚 Source Channels</b>\n\n"
            if sources:
                body = "<i>Currently scanned extra channels:</i>\n" + "\n".join(f"- <code>{cid}</code>" for cid in sources)
            else:
                body = "<i>No extra source channels configured yet.</i>"
            footer = "\n\n<code>/addsource &lt;channel_id&gt;</code>\n<code>/removesource &lt;channel_id&gt;</code>"
        await query.message.edit_text(header + body + footer, parse_mode="HTML")
        return

    if section == "lang":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("English", callback_data="lang|en"),
                 InlineKeyboardButton("हिन्दी", callback_data="lang|hi")]
            ]
        )
        if lang == "hi":
            text = "<b>🌐 भाषा सेटिंग</b>\n\n<i>इस चैट के लिए भाषा चुनें:</i>"
        else:
            text = "<b>🌐 Language Setting</b>\n\n<i>Select language for this chat:</i>"
        await query.message.edit_text(text=text, parse_mode="HTML", reply_markup=kb)
        return

    if section == "timers":
        timers = bot_config.get("auto_delete", {})
        if lang == "hi":
            body = "<i>Auto delete टाइमर्स (सेकंड में या मिनट/घंटा में):</i>\n" + json.dumps(timers, indent=2, ensure_ascii=False)
            footer = "\n\n<code>/settimer &lt;key&gt; &lt;seconds&gt;</code>"
        else:
            body = "<i>Auto delete timers (in seconds/minutes/hours):</i>\n" + json.dumps(timers, indent=2)
            footer = "\n\n<code>/settimer &lt;key&gt; &lt;seconds&gt;</code>"
        await query.message.edit_text("<b>⏱ Auto Delete Timers</b>\n\n" + body + footer, parse_mode="HTML")
        return


# -----------------------
# start bot
# -----------------------

def start_bot():

    global app

    init_search_index()

    async def _post_init(application):
        if str(AUTO_SCAN).lower() == "true" and CHANNEL_ID:
            asyncio.create_task(auto_scan_loop(application.bot))
        # Start background link checker
        asyncio.create_task(start_link_checker())

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
    app.add_handler(CommandHandler("config", config_cmd))

    # admin-only utility commands
    app.add_handler(CommandHandler("announce", announce_cmd))
    app.add_handler(CommandHandler("copyright_mute", copyright_mute_cmd))
    app.add_handler(CommandHandler("setlang", setlang_cmd))
    app.add_handler(CommandHandler("addsource", addsource_cmd))
    app.add_handler(CommandHandler("removesource", removesource_cmd))

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


async def addsource_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: add a source channel."""
    if not is_admin(update.effective_user.id):
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("⛔ Admin only." if lang != "hi" else "⛔ केवल एडमिन।")
        return
    
    if not context.args:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Usage: /addsource <channel_id>" if lang != "hi" else "उपयोग: /addsource <channel_id>")
        return
    
    try:
        channel_id = int(context.args[0])
    except ValueError:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Invalid channel ID." if lang != "hi" else "अमान्य चैनल ID।")
        return
    
    global bot_config
    sources = bot_config.get("sources", [])
    if channel_id in sources:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Channel already in sources." if lang != "hi" else "चैनल पहले से सोर्स में है।")
        return
    
    sources.append(channel_id)
    bot_config["sources"] = sources
    save_config(bot_config)
    
    lang = get_chat_lang(update.effective_chat.id)
    await update.message.reply_text(f"✅ Source channel added: {channel_id}" if lang != "hi" else f"✅ सोर्स चैनल जोड़ा गया: {channel_id}")


async def removesource_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: remove a source channel."""
    if not is_admin(update.effective_user.id):
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("⛔ Admin only." if lang != "hi" else "⛔ केवल एडमिन।")
        return
    
    if not context.args:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Usage: /removesource <channel_id>" if lang != "hi" else "उपयोग: /removesource <channel_id>")
        return
    
    try:
        channel_id = int(context.args[0])
    except ValueError:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Invalid channel ID." if lang != "hi" else "अमान्य चैनल ID।")
        return
    
    global bot_config
    sources = bot_config.get("sources", [])
    if channel_id not in sources:
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("Channel not found in sources." if lang != "hi" else "चैनल सोर्स में नहीं मिला।")
        return
    
    sources.remove(channel_id)
    bot_config["sources"] = sources
    save_config(bot_config)
    
    lang = get_chat_lang(update.effective_chat.id)
    await update.message.reply_text(f"✅ Source channel removed: {channel_id}" if lang != "hi" else f"✅ सोर्स चैनल हटाया गया: {channel_id}")


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
