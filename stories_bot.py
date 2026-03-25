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
    ChatMemberHandler,
    PollAnswerHandler,
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
    GROUP_ID,
)
from scanner_client import scan_channel
from search_engine import search_story_exact_or_alias, get_suggestions
from filters_text import is_valid_query
from link_checker import start_link_checker
from database import (
    get_story,
    load_db,
    save_db,
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
    load_favorites, save_favorites,
    load_stats, save_stats,
    load_subs, save_subs,
    load_learned_formats, save_learned_formats,
    load_voting_db, save_voting_db,
)
from format_learner import learn_format, build_preview, build_test_result, extract_with_template
from external_check import verify_story_external
from post_builder import post_builder_handler

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
spam_requests_count = {}

# Load persisted indexes so search works immediately after restart
story_index = load_story_index()
search_index = load_search_index()

last_scan_count = len(story_index)

# runtime state
BOT_START_TS = time.time()
IS_SCANNING = False

# Maintenance mode state
MAINTENANCE_MODE = False          # True while bot is in maintenance
MAINTENANCE_UNTIL: float = 0.0   # epoch timestamp when maintenance ends (0 = indefinite)
cooldowns_db = load_cooldowns()  # user_id(str) -> {'until': ts, 'reason': str}
COPYRIGHT_DEFAULT_COOLDOWN_MIN = 1440  # 24h
chat_languages = load_languages()  # chat_id(str) -> 'en'/'hi'
link_flags = load_link_flags()  # story_key -> {'broken': bool, 'link': str, 'voters': [{'id','name'}], 'chats': [int]}
active_link_votes = {}  # vote_id -> {'story_key', 'chat_id', 'message_id', 'voters': {user_id: name}, 'link', 'story_name'}
bot_config = load_config()
stats_db = load_stats()  # {"searches": {}, "users": {}, "trending": {}}
favorites_db = load_favorites()  # { user_id_str: [story_key1, ...] }
subs_db = load_subs()  # [ user_id1_str, ... ]
learned_formats_db = load_learned_formats()  # { str(channel_id): [template_dict, ...] }

# Restore maintenance state from config (survives restarts)
_maint_cfg = bot_config.get("maintenance", {})
if _maint_cfg.get("enabled") and (_maint_cfg.get("until", 0) == 0 or _maint_cfg.get("until", 0) > time.time()):
    MAINTENANCE_MODE = True
    MAINTENANCE_UNTIL = float(_maint_cfg.get("until", 0))
else:
    MAINTENANCE_MODE = False
    MAINTENANCE_UNTIL = 0.0

# Global Scan Progress State
SCAN_PROGRESS = {
    "stories_found": 0,
    "total_messages": 0,
    "expected_total": 0, # Will be set to last_scan_count below
    "eta_s": 0,
    "last_story": "",
    "start_ts": 0
}
SCAN_PROGRESS["expected_total"] = last_scan_count or 0

# --- Voting Persistence ---
voting_db = load_voting_db()
# voting_queue: [{"name": str, "requesters": {chat_id: [uids]}}, ...]
voting_queue = voting_db.get("queue", [])
# active_polls: {poll_id: {message_id, chat_id, options, votes, created_at}}
active_polls = voting_db.get("polls", {})

VOTING_THRESHOLD = 5 # 5 votes to win
VOTING_SIZE_FOR_POLL = 3 # 3 unique stories to start a poll
POLL_TIMEOUT_SEC = 86400 # 24 hours

_maint_cfg = bot_config.get("maintenance", {})
if _maint_cfg.get("enabled") and (_maint_cfg.get("until", 0) == 0 or _maint_cfg.get("until", 0) > time.time()):
    MAINTENANCE_MODE = True
    MAINTENANCE_UNTIL = float(_maint_cfg.get("until", 0))
else:
    MAINTENANCE_MODE = False
    MAINTENANCE_UNTIL = 0.0

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
    # Check OWNER_ID, ADMIN_ID (if non-zero), or moderators list
    if user_id == OWNER_ID or (ADMIN_ID != 0 and user_id == ADMIN_ID):
        return True
    moderators = bot_config.get("moderators", [])
    if str(user_id) in moderators or user_id in moderators:
        return True
    return False


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
    if search_story_exact_or_alias(q):
        return True
    
    if len(get_suggestions(q, limit=1)) > 0:
        return True
    
    return False


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


_cooldown_msg_cache = {}

async def _fake_report_live_timer(target_msg, context, user, reason, until):
    try:
        lang = get_chat_lang(target_msg.chat.id) if getattr(target_msg, "chat", None) else "en"
    except Exception:
        lang = "en"
        
    rem = max(0, int(until - time.time()))
    h, r = divmod(rem, 3600)
    m, s = divmod(r, 60)
    timer_str = f"{h:02d}:{m:02d}:{s:02d}"
    
    text_template = (
        f"<b>⛔ Error: Interaction Blocked</b>\n\n"
        f"{user.mention_html()}\n"
        f"✧ <i>Reason / कारण:</i> <b>{reason}</b>\n"
        f"✧ <i>Access will be restored in:</i> <b>[TIMER]</b>"
    )
        
    try:
        sent = await target_msg.reply_text(text=text_template.replace("[TIMER]", timer_str), parse_mode="HTML")
    except Exception:
        return
        
    chat_id = sent.chat.id
    msg_id = sent.message_id
    
    end_task = time.time() + 300
    while time.time() < end_task:
        await asyncio.sleep(20)
        rem = max(0, int(until - time.time()))
        h, r = divmod(rem, 3600)
        m, s = divmod(r, 60)
        timer_str = f"{h:02d}:{m:02d}:{s:02d}"
        patched_text = text_template.replace("[TIMER]", timer_str)
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=patched_text, parse_mode="HTML")
        except Exception:
            pass
        if rem <= 0:
            break

async def _enforce_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if user is blocked or if system is scanning, to abort command."""
    user = update.effective_user
    if getattr(update, "callback_query", None):
        target_msg = update.callback_query.message
    else:
        target_msg = update.message

    if IS_SCANNING:
        if not (user and is_admin(user.id)):
            lang = get_chat_lang(update.effective_chat.id) if update.effective_chat else "en"
            await _send_scan_busy_notice(target_msg, lang)
            return True

    # Maintenance mode check — blocks non-admins while maintenance is ON
    if MAINTENANCE_MODE:
        if not (user and is_admin(user.id)):
            # Auto-expire if the timed window has passed
            if MAINTENANCE_UNTIL > 0 and time.time() >= MAINTENANCE_UNTIL:
                _end_maintenance()
                # maintenance just ended — allow through
                return False
            # Still in maintenance window (or indefinite)
            lang = get_chat_lang(update.effective_chat.id) if update.effective_chat else "en"
            await _send_maintenance_notice(target_msg, lang)
            return True

    if not user:
        return False
    entry = _get_cooldown(user.id)
    if not entry:
        return False
    now = time.time()
    if now >= entry["until"]:
        _clear_cooldown(user.id)
        return False

    target = update.message or (update.callback_query.message if update.callback_query else None)
    
    last_msg = _cooldown_msg_cache.get(user.id, 0)
    if now - last_msg < 60:
        return True
        
    _cooldown_msg_cache[user.id] = now
    
    remaining = int(entry["until"] - now)
    mins = max(1, remaining // 60)
    lang = get_chat_lang(update.effective_chat.id) if update.effective_chat else "en"
    reason = entry.get("reason", "cooldown")

    if reason.lower() == "fake report":
        if target:
            asyncio.create_task(_fake_report_live_timer(target, context, user, reason, entry["until"]))
        return True

    if lang == "hi":
        text = (
            f"<b>◌ Cooldown Active / आप कोल्डाउन पर हैं</b>\n\n"
            f"{user.mention_html()}\n"
            f"✧ <i>कारण:</i> <b>{reason}</b>\n"
            f"<i>➔ कृपया लगभग {mins} मिनट बाद फिर से कोशिश करें।</i>"
        )
    else:
        text = (
            f"<b>◌ Cooldown Active</b>\n\n"
            f"{user.mention_html()}\n"
            f"✧ <i>Reason:</i> <b>{reason}</b>\n"
            f"<i>➔ Please try again after approximately {mins} minutes.</i>"
        )

    # reply to whatever is available
    try:
        if target:
            await target.reply_text(text=text, parse_mode="HTML")
    except Exception:
        pass
    return True


async def _send_scan_busy_notice(msg, lang: str):
    """Silently ignore messages during scan as requested by user. Removed auto-delete behavior."""
    pass


def _looks_like_existing_story_query(query: str) -> bool:
    """Return True if the query looks like it could be a story name search.
    Filters out very short queries, greetings, or random text.
    """
    q = query.strip().lower()
    if len(q) < 2:
        return False
    # Common greetings and filler words that are NOT story searches
    noise = {
        "hi", "hello", "hey", "ok", "yes", "no", "thanks", "thank you",
        "bye", "good", "bad", "nice", "wow", "lol", "haha", "hmm",
        "please", "help", "start", "stop", "admin", "bro", "sis",
        "kya", "kaise", "kaisa", "kyun", "haan", "nahi", "acha",
        "theek", "shukriya", "dhanyavaad", "namaste",
    }
    if q in noise:
        return False
    return True


def _end_maintenance():
    """Disable maintenance mode and persist the off-state."""
    global MAINTENANCE_MODE, MAINTENANCE_UNTIL
    MAINTENANCE_MODE = False
    MAINTENANCE_UNTIL = 0.0
    bot_config["maintenance"] = {"enabled": False, "until": 0}
    save_config(bot_config)


async def _send_maintenance_notice(msg, lang: str):
    """Send a premium maintenance notice to blocked users (auto-deleted after 2 min)."""
    if MAINTENANCE_UNTIL > 0:
        rem = max(0, int(MAINTENANCE_UNTIL - time.time()))
        h, r = divmod(rem, 3600)
        m, _ = divmod(r, 60)
        if h > 0:
            eta_en = f"approximately {h}h {m}m"
            eta_hi = f"लगभग {h}h {m}m"
        elif m > 0:
            eta_en = f"approximately {m} minute(s)"
            eta_hi = f"लगभग {m} मिनट"
        else:
            eta_en = "a few seconds"
            eta_hi = "कुछ सेकंड"
    else:
        eta_en = "an unspecified time"
        eta_hi = "अनिश्चित समय"

    if lang == "hi":
        text = (
            "<b>🔧 Riya — रखरखाव मोड</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "<i>◎ Riya अभी मेंटेनेंस पर है।</i>\n\n"
            "❁ <b>हम क्या कर रहे हैं?</b>\n"
            "➔ सिस्टम अपग्रेड और सुधार\n"
            "➔ डेटाबेस ऑप्टिमाइज़ेशन\n\n"
            f"✦ <b>अनुमानित समय:</b> <code>{eta_hi}</code>\n\n"
            "<i>आपके धैर्य के लिए धन्यवाद। 🙏</i>"
        )
    else:
        text = (
            "<b>🔧 Riya — Maintenance Mode</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "<i>Riya is currently undergoing scheduled maintenance.</i>\n\n"
            "✦ <b>What's happening?</b>\n"
            "✧ System upgrades & improvements\n"
            "✧ Database optimisation\n\n"
            f"✦ <b>Expected back in:</b> <code>{eta_en}</code>\n\n"
            "<i>Thank you for your patience. We'll be back shortly!</i> 🙏"
        )

    try:
        notice = await msg.reply_text(text=text, parse_mode="HTML")

        async def _del():
            await asyncio.sleep(120)
            try:
                await notice.delete()
            except Exception:
                pass

        asyncio.create_task(_del())
    except Exception:
        pass


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
    global story_index, last_scan_count, IS_SCANNING
    if not CHANNEL_ID or (str(AUTO_SCAN).lower() != "true"):
        return
    if not (SESSION_STRING and API_ID and API_HASH):
        logger.warning("Auto-scan skipped: SESSION_STRING, API_ID, API_HASH required for Telethon")
        return
    first_run = True
    while True:
        try:
            if first_run:
                await asyncio.sleep(600)  # wait 10 min before first run
                first_run = False
            else:
                await asyncio.sleep(14400)  # Wait 4 hours (6x per 24h)
            
            if IS_SCANNING:
                continue # Skip if already scanning via command
                
            IS_SCANNING = True
            logger.info("Auto scan started...")
            
            scan_start_ts = time.time()
            expected_total = last_scan_count or 0
            
            # Reset global progress for auto-scan
            SCAN_PROGRESS.update({
                "stories_found": 0,
                "total_messages": 0,
                "expected_total": expected_total,
                "eta_s": 0,
                "start_ts": scan_start_ts,
                "last_story": ""
            })

            async def _auto_progress_cb(p):
                now = time.time()
                last_found = int(p.get("stories_found") or 0)
                elapsed = max(now - scan_start_ts, 1)
                rate = last_found / elapsed
                remaining = max(expected_total - last_found, 0) if expected_total else 0
                eta_s = int(remaining / rate) if (expected_total and rate > 0) else 0
                
                SCAN_PROGRESS["stories_found"] = last_found
                SCAN_PROGRESS["total_messages"] = p.get("total_messages", 0)
                SCAN_PROGRESS["eta_s"] = eta_s
                SCAN_PROGRESS["last_story"] = (p.get("last_story") or "").strip()

            result = await scan_channel(
                CHANNEL_ID, 
                bot=bot, 
                log_channel=LOG_CHANNEL,
                progress_cb=_auto_progress_cb
            )
            names = result.get("names", [])
            # Only update indexes if we got a successful result - never wipe on failure
            if names:
                story_index = names
                build_search_index(story_index)
                save_story_index(story_index)
                last_scan_count = len(story_index)
                logger.info("Auto scan done | stories=%d", last_scan_count)
                if bot: pass  # auto scan removed
            else:
                logger.warning("Auto scan returned no stories, keeping existing index")
        except Exception as e:
            logger.error("Auto scan error: %s", e)
        finally:
            IS_SCANNING = False


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

# ═══════════════════════════════════════════════
#  INLINE MENU SYSTEM
#  All builder functions return (text, markup).
#  Navigation always uses query.message.edit_text
#  so the same message is always edited in-place.
# ═══════════════════════════════════════════════

_DIVIDER = "━━━━━━━━━━━━━━━━"


def _nav_row(caller_id: int, back: str | None = None) -> list:
    """Standard bottom navigation row: [Back] [✕ Close]"""
    row = []
    if back:
        row.append(InlineKeyboardButton("➦ Back", callback_data=f"menu|{back}|{caller_id}"))
    row.append(InlineKeyboardButton("✖ Close", callback_data=f"menu|close|{caller_id}"))
    return row


def _menu_main(caller_id: int, lang: str = "en", mention: str = "") -> tuple:
    """Main / Home menu."""
    greet = f", {mention}" if mention else ""
    if lang == "hi":
        text = (
            f"<b>♡ नमस्ते,</b>{greet} ( ˶ˆᗜˆ˵ )\n\n"
            f"{_DIVIDER}\n"
            "<b>📚 Riya — Main Menu</b>\n"
            "<i>✺ स्टोरी खोजें, एक्सप्लोर करें, और ज़्यादा। ✨</i>\n"
            f"{_DIVIDER}"
        )
    else:
        text = (
            f"<b>♡ Hey,</b>{greet} ( ˶ˆᗜˆ˵ )\n\n"
            f"{_DIVIDER}\n"
            "<b>📚 Riya — Main Menu</b>\n"
            "<i>✺ Search stories, explore, and more. ✨</i>\n"
            f"{_DIVIDER}"
        )
    btn_rows = [
        [
            InlineKeyboardButton("♥ Favourites",  callback_data=f"menu|saved|{caller_id}"),
            InlineKeyboardButton("✸ New Series",   callback_data=f"menu|new|{caller_id}"),
        ],
        [
            InlineKeyboardButton("✺ Trending",    callback_data=f"menu|trending|{caller_id}"),
            InlineKeyboardButton("❁ About",        callback_data=f"menu|about|{caller_id}"),
        ],
        [
            InlineKeyboardButton("✧ Language",    callback_data=f"menu|lang|{caller_id}"),
            InlineKeyboardButton("✦ Help",         callback_data=f"menu|help|{caller_id}"),
        ]
    ]
    btn_rows.append([InlineKeyboardButton("✖ Close", callback_data=f"menu|close|{caller_id}")])

    markup = InlineKeyboardMarkup(btn_rows)
    return text, markup


def _menu_trending(caller_id: int) -> tuple:
    trending = stats_db.get("trending", {})
    now = time.time()
    week_ago = now - (7 * 24 * 3600)
    
    # Prune and compute
    trending_counts = {}
    for k, v in list(trending.items()):
        if isinstance(v, list):
            valid_times = [t for t in v if t >= week_ago]
            if valid_times:
                trending[k] = valid_times
                trending_counts[k] = len(valid_times)
            else:
                del trending[k]
        else:
            # Transition legacy integer tracking to list
            trending[k] = [now]
            trending_counts[k] = 1

    sorted_trend = sorted(trending_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    if sorted_trend:
        lines = "\n".join(
            f"<b>{i}.</b> {k}  <i>✶ {v} searches (this week)</i>"
            for i, (k, v) in enumerate(sorted_trend, 1)
        )
        body = lines
    else:
        body = "<i>☆ No trending stories yet. Start searching! ✨</i>"
    text = (
        "<b>🔥 Trending Stories</b> ( ˶ˆᗜˆ˵ )\n"
        "<i>✺ Top searched stories right now. ✨</i>\n"
        f"{_DIVIDER}\n\n"
        f"{body}"
    )
    markup = InlineKeyboardMarkup([_nav_row(caller_id, back="home")])
    return text, markup


def _menu_new(caller_id: int) -> tuple:
    db = load_db()
    all_stories = list(db.values())
    all_stories.sort(key=lambda s: s.get("message_id", 0), reverse=True)
    stories = all_stories[:10]
    if stories:
        lines = []
        for s in stories:
            name = clean_story(s.get("name") or s.get("text") or "Story")
            link = s.get("link", "")
            if link:
                lines.append(f'✸ <a href="{link}">{name}</a>')
            else:
                lines.append(f"✸ {name}")
        body = "\n".join(lines)
    else:
        body = "<i>☆ No new series in the database yet.</i>"
    text = (
        "<b>✦ New Series</b>\n"
        "<i>✺ The latest stories added to the database.</i>\n"
        f"{_DIVIDER}\n\n"
        f"{body}"
    )
    markup = InlineKeyboardMarkup([_nav_row(caller_id, back="home")])
    return text, markup


def _menu_saved(caller_id: int) -> tuple:
    user_id_str = str(caller_id)
    favs = favorites_db.get(user_id_str, [])
    if len(favs) > 0:
        body = "\n".join([f"♥ <code>{clean_story(s)}</code>" for s in favs[:15]])
        if len(favs) > 15:
            body += f"\n<i>…and {len(favs) - 15} more.</i>"
    else:
        body = "<i>♡ No favourites yet.\n➔ Tap ★ on any search result to save it.</i>"
    text = (
        "<b>♥ My Favourites</b>\n"
        "<i>✧ Your personal saved stories.</i>\n"
        f"{_DIVIDER}\n\n"
        f"{body}"
    )
    markup = InlineKeyboardMarkup([_nav_row(caller_id, back="home")])
    return text, markup


def _menu_browse(caller_id: int) -> tuple:
    db = load_db()
    types = sorted({s.get("story_type") for s in db.values() if s.get("story_type")})[:12]
    if types:
        # build 2-per-row grid
        rows = []
        for i in range(0, len(types), 2):
            row = []
            for t in types[i:i+2]:
                label = t.capitalize()[:14]
                row.append(InlineKeyboardButton(
                    label,
                    switch_inline_query_current_chat=t
                ))
            rows.append(row)
        rows.append(_nav_row(caller_id, back="home"))
        body = "<i>Tap a category to search inline.</i>"
    else:
        rows = [_nav_row(caller_id, back="home")]
        body = "<i>No categories available yet. Run /scan first.</i>"
    text = (
        "<b>📑 Browse by Category</b>\n"
        "<i>Explore stories by type.</i>\n"
        f"{_DIVIDER}\n\n"
        f"{body}"
    )
    markup = InlineKeyboardMarkup(rows)
    return text, markup


def _menu_how(caller_id: int, lang: str = "en") -> tuple:
    # "How It Works" is now merged into Help
    return _menu_help(caller_id, lang)


def _menu_about(caller_id: int, lang: str = "en") -> tuple:
    if lang == "hi":
        body = (
            "<blockquote><i>Riya एक स्मार्ट Telegram स्टोरी खोज बॉट है। ✨</i></blockquote>\n\n"
            "<u>❁ Features</u>\n"
            "▪ AI फ़जी सर्च 🔎\n"
            "▪ इनलाइन मेनू नेविगेशन 📂\n"
            "▪ JSON डेटाबेस 💾\n"
            "▪ एडमिन /scan ⚙️\n"
            "▪ फेवरेट सिस्टम ♥️\n\n"
            "<b>👨‍💻 Developer:</b> @MeJeetX\n"
            "<b>⚙ Version:</b> Riya v10"
        )
    else:
        body = (
            "<blockquote><i>Riya is an intelligent Telegram story finder bot. ✨</i></blockquote>\n\n"
            "<u>❁ Features</u>\n"
            "▪ AI fuzzy search 🔎\n"
            "▪ Inline menu navigation 📂\n"
            "▪ JSON database 💾\n"
            "▪ Admin /scan command ⚙️\n"
            "▪ Favourites system ♥️\n\n"
            "<b>👨‍💻 Developer:</b> @MeJeetX\n"
            "<b>⚙ Version:</b> Riya v10"
        )
    text = (
        "<b>📚 About Riya Bot</b> ( ˶ˆᗜˆ˵ )\n"
        "<i>✧ Everything you need to know. ✨</i>\n"
        f"{_DIVIDER}\n\n"
        f"{body}"
    )
    markup = InlineKeyboardMarkup([_nav_row(caller_id, back="home")])
    return text, markup


def _menu_help(caller_id: int, lang: str = "en") -> tuple:
    if lang == "hi":
        body = (
            "<u>❁ Bot कैसे काम करता है? ( ˶ˆᗜˆ˵ )</u>\n"
            "▪️ स्टोरी का नाम भेजें... ✨\n"
            "▪️ बॉट डेटाबेस में खोजता है 🔎\n"
            "▪️ मिली ➜ लिंक मिलता है ✅\n"
            "▪️ नहीं मिली ➜ /request करें ⚠️\n\n"
            "<i>स्टोरी मिलने पर आपको नोटिफिकेशन मिलेगा।🔔</i>\n\n"
            "<u>✦ Commands</u>\n"
            "➔ /start — बॉट शुरू करें ✨\n"
            "➔ /request — स्टोरी रिक्वेस्ट 📂\n"
            "➔ /info — स्टोरी डिटेल्स 📑\n"
            "➔ /saved — फेवरेट देखें ♥️\n"
            "➔ /trending — ट्रेंडिंग 🔥\n"
            "➔ /subscribe — नोटिफिकेशन 💬\n\n"
            "<i>स्टोरी का नाम भेजकर सीधे खोजें। ✨</i>"
        )
    else:
        body = (
            "<u>✦ How It Works ( ˶ˆᗜˆ˵ )</u>\n"
            "▪️ Send a story name... ✨\n"
            "▪️ Bot searches the database 🔎\n"
            "▪️ Found ➜ you get the link ✅\n"
            "▪️ Not found ➜ use /request ⚠️\n\n"
            "<i>When the story is uploaded, you will be notified automatically.🔔</i>\n\n"
            "<u>✦ Commands</u>\n"
            "➔ /start — Open the menu ✨\n"
            "➔ /request — Request a story 📂\n"
            "➔ /info — Story details 📑\n"
            "➔ /saved — Your favourites ♥️\n"
            "➔ /trending — Trending stories 🔥\n"
            "➔ /subscribe — Get notifications 💬\n\n"
            "<i>You can also just send a story name to search. ✨</i>"
        )
    text = (
        "<b>🚧 Help &amp; How It Works</b> ( ˶ˆᗜˆ˵ )\n"
        "<i>✧ Everything you need to use this bot. ✨</i>\n"
        f"{_DIVIDER}\n\n"
        f"{body}"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Language",  callback_data=f"menu|lang|{caller_id}"),
            InlineKeyboardButton("🔥 Trending",  callback_data=f"menu|trending|{caller_id}"),
        ],
        _nav_row(caller_id, back="home"),
    ])
    return text, markup


def _menu_lang(caller_id: int) -> tuple:
    text = (
        "<b>🌐 Language / भाषा</b> ( ˶ˆᗜˆ˵ )\n"
        "<i>Select your preferred language for this chat. ✨</i>\n"
        f"{_DIVIDER}\n\n"
        "Choose a language below 👇"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇺🇸 English", callback_data="lang|en"),
            InlineKeyboardButton("🇮🇳 हिन्दी",  callback_data="lang|hi"),
        ],
        _nav_row(caller_id, back="home"),
    ])
    return text, markup


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return

    u = update.effective_user
    chat = update.effective_chat
    lang = get_chat_lang(chat.id)
    caller_id = u.id

    # Send the integrated welcome + inline menu panel
    text, markup = _menu_main(caller_id, lang, u.mention_html() if u else "")
    await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)

    await log(context, f"START | user_id={u.id} username={u.username}")


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

    keyboard = [[InlineKeyboardButton("➦ Back to Menu", callback_data="cmd|start")]]
    reply = await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

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

    keyboard = [[InlineKeyboardButton("➦ Back to Menu", callback_data="cmd|start")]]
    msg = await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

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
        text = f"<b>🆘 हेल्प सेंटर</b> ( ˶ˆᗜˆ˵ )\n\n" \
               f"<i>इन कमांड्स से आप बॉट के साथ इंटरैक्ट कर सकते हैं: ✨</i>\n\n" \
               f"<u>/start</u> → बॉट शुरू करें ✨\n" \
               f"<u>/request</u> → स्टोरी रिक्वेस्ट 📂\n" \
               f"<u>/scan</u> → डेटाबेस रिफ्रेश करें [सिर्फ़ एडमिन] ⚙️\n" \
               f"<u>/info</u> → स्टोरी डिटेल्स 📑\n" \
               f"<u>/stats</u> → बॉट स्टैटिस्टिक्स [एडमिन] 📊\n\n" \
               f"<b>आप सीधे स्टोरी का नाम भेजकर भी सर्च कर सकते हैं। ✨</b>"
    else:
        text = f"<b>🆘 Help Center</b> ( ˶ˆᗜˆ˵ )\n\n" \
               f"<i>Use these commands to interact with the bot: ✨</i>\n\n" \
               f"<u>/start</u> → Start the bot ✨\n" \
               f"<u>/request</u> → Request a story 📂\n" \
               f"<u>/scan</u> → Refresh database [admins only] ⚙️\n" \
               f"<u>/info</u> → Story details 📑\n" \
               f"<u>/stats</u> → Bot statistics [admins] 📊\n\n" \
               f"<b>You can also simply send a story name to search. ✨</b>"

    keyboard = [
        [
            InlineKeyboardButton("🔥 Trending", callback_data="cmd|trending"),
            InlineKeyboardButton("🌐 Language", callback_data="cmd|lang_menu")
        ],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd|start")]
    ]
    msg = await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

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
    type_line = f"\n<b>✽ Story Type:</b> <i>{stype}</i>" if stype else ""
    desc = result.get("description", "")
    desc_line = f"\n\n<b>❁ Description:</b>\n<i>{desc[:200]}</i>" if desc else ""
    text = f"""<b>📖 {name}</b>{type_line}{desc_line}

<b>➜ Link:</b> {link}"""
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
    text = (
        "<b>◆ Bot Statistics</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"
        f"▪ <b>Stories in DB:</b> <i>{total_stories}</i>\n"
        f"▪ <b>Indexed titles:</b> <i>{len(story_index)}</i>\n"
        f"▪ <b>Unique requests:</b> <i>{unique_requests}</i>\n"
        f"▪ <b>Total requests:</b> <i>{total_requests}</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot uptime and basic stats in IST, auto-delete after 60 sec."""
    if await _enforce_cooldown(update, context):
        return
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
        f"<b>★ Riya Bot Status</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"👤 {mention}\n\n"
        f"◍ <b>Uptime:</b>  <i>{uptime_str}</i>\n"
        f"◍ <b>Time (IST):</b>  <code>{ist.strftime('%d-%m-%Y %H:%M:%S')}</code>\n\n"
        f"◆ <b>Database</b>\n"
        f"▪ <b>Total Stories:</b>  <i>{total_stories}</i>\n"
        f"▪ <b>Requests:</b>  <i>{total_requests}</i>\n"
        f"▪ <b>Broken Links:</b>  <i>{total_broken_reports}</i>\n\n"
        f"◎ <b>Bot Status:</b>  <i>Running normally ✺</i>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"✧ <i>This message deletes in 60 seconds.</i>"
    )

    caller_id = user.id if user else 0
    delete_btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🗑️ Delete", callback_data=f"status_delete|{caller_id}")]]
    )

    reply = await chat.send_message(text=text, parse_mode="HTML", reply_markup=delete_btn)

    # auto-delete command immediately and reply after 60 seconds
    async def _delete_cmd():
        await asyncio.sleep(5)
        try:
            await cmd_msg.delete()
        except Exception:
            pass

    async def _delete_reply():
        await asyncio.sleep(60)
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

            elapsed = max(now - scan_start_ts, 1)
            rate = last_found / elapsed
            remaining = max(expected_total - last_found, 0) if expected_total else 0
            eta_s = int(remaining / rate) if (expected_total and rate > 0) else 0

            # Update GLOBAL state for other users
            SCAN_PROGRESS["stories_found"] = last_found
            SCAN_PROGRESS["total_messages"] = p.get("total_messages", 0)
            SCAN_PROGRESS["eta_s"] = eta_s
            SCAN_PROGRESS["last_story"] = last_story_name

            # throttle UI edits (Telegram rate limits)
            if now - last_ui_update < 2.5:
                return
            last_ui_update = now

            eta_text = f"`~{eta_s//60:02d}:{eta_s%60:02d}`" if eta_s else "`--:--`"

            # premium-ish live UI: show last story and counts
            # Strip markdown characters that crash parse_mode="Markdown"
            safe_story = re.sub(r'[*_`\[\]()~]', '', last_story_name)
            safe_story = (safe_story[:60] + "…") if len(safe_story) > 60 else safe_story
            try:
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
            except Exception as parse_err:
                logger.warning(f"Failed to update scan UI: {parse_err}")

        # build source channel list: primary + extra from config
        sources = []
        if CHANNEL_ID:
            sources.append(CHANNEL_ID)
        extra_sources = bot_config.get("sources", [])
        for cid in extra_sources:
            # Support both integer IDs and string usernames
            try:
                c_int = int(cid)
                if c_int and c_int not in sources:
                    sources.append(c_int)
            except (ValueError, TypeError):
                # Could be a username string like "@channelname"
                if isinstance(cid, str) and cid.strip() and cid not in sources:
                    sources.append(cid.strip())

        all_names = []
        all_keys = set()
        total_stories_found = 0
        scan_errors = []

        formats_by_channel = bot_config.get("formats", {})

        for idx, cid in enumerate(sources):
            try:
                logger.info(f"Scanning channel {idx+1}/{len(sources)}: {cid}")
                result = await scan_channel(
                    cid,
                    bot=context.bot,
                    log_channel=LOG_CHANNEL,
                    progress_cb=_progress_cb,
                    cleanup=False,
                    formats_by_channel=formats_by_channel,
                )
                all_names.extend(result.get("names", []))
                all_keys.update(result.get("keys", []))
                total_stories_found += result.get("stories", 0)
                logger.info(f"Channel {cid}: found {result.get('stories', 0)} stories")
            except Exception as ch_err:
                error_msg = f"Channel {cid}: {ch_err}"
                logger.error(f"Scan failed for {error_msg}")
                scan_errors.append(error_msg)
                # Continue with remaining channels
                continue

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

        story_index = uniq_names

        build_search_index(story_index)
        save_story_index(story_index)

        last_scan_count = len(story_index)
        # Index already built and saved above — no extra step needed

        # clear link flags for stories whose link has been updated/confirmed again
        db = load_db()
        changed = False
        for key, flag in list(link_flags.items()):
            if key in db:
                new_link = db[key].get("link", "")
                if flag.get("link") and new_link and new_link != flag["link"]:
                    # Notify users who reported/voted for this link
                    voters = flag.get("voters", [])
                    chats = flag.get("chats", [])
                    story_name = db[key].get("text", flag.get("story_name", "N/A"))
                    
                    # Create user mentions in Markdown
                    voter_mentions = " ".join([f"[{v.get('name', str(v.get('id')))}](tg://user?id={v.get('id')})" for v in voters])
                    if voter_mentions:
                        voter_mentions = f"{voter_mentions}\n\n"
                        
                    notification_text = (
                        f"✦ **Link Fixed**\n\n"
                        f"{voter_mentions}"
                        f"📖 Story: {story_name}\n"
                        f"🔗 Link: {new_link}\n"
                        f"⏰ Fixed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"The link is now working again!"
                    )
                    
                    # Send notifications to chats where the vote occurred
                    for chat_id in chats:
                        try:
                            # Context applies html naturally but telethon markdown was used here historically.
                            # Send message with Markdown parsing since tg://user format requires HTML or Markdown.
                            await context.bot.send_message(chat_id=chat_id, text=notification_text, parse_mode="Markdown")
                        except Exception as e:
                            logger.warning(f"Failed to send fix notification: {e}")
                            
                    link_flags.pop(key, None)
                    changed = True
        if changed:
            save_link_flags(link_flags)

        # Notify requesters for stories that are now available
        try:
            await _notify_fulfilled_requests(context)
        except Exception as e:
            logger.warning("Request notification failed: %s", e)

        error_text = ""
        if scan_errors:
            # Strip markdown characters from errors to prevent UI crashes in parse_mode="Markdown"
            safe_errors = [re.sub(r'[*_`\[\]()~]', '', str(e)) for e in scan_errors]
            error_text = "\n\n⚠️ *Errors:*\n" + "\n".join(f"• {e}" for e in safe_errors)

        await msg.edit_text(
            text=f"""
✅ *Scan Completed*

✶ *Stories Indexed:* {last_scan_count}  
◍ *Channels Scanned:* {len(sources) - len(scan_errors)}/{len(sources)}
⚡ *Search Engine:* _Optimized_

_Your story database is now fully updated._{error_text}
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
    db = load_db()
    for i, name in enumerate(chunk, start + 1):
        title = clean_story(name)
        db_story = db.get(name, {})
        link = db_story.get("link", "") or db_story.get("message_url", "")
        # Number is plain text (non-copyable); title is linked without mono-space blocking
        # If linked: entire title = clickable anchor
        if link:
            lines.append(f"{i}. <a href='{link}'>{html.escape(title)}</a>")
        else:
            lines.append(f"{i}. {html.escape(title)}")
    total = len(story_index)
    header = (
        f"<b>✦ Story List  ·  {total} titles</b>\n"
        f"<i>▪ Page {page + 1}  ·  #{start + 1}–#{min(end, total)}</i>\n"
        "━━━━━━━━━━━━━━━━\n"
    )
    text = header + "\n".join(lines)
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

    caller_id = update.effective_user.id
    text, has_prev, has_next, page = _stories_page(0)

    total_pages = (len(story_index) + STORIES_PER_PAGE - 1) // STORIES_PER_PAGE
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton("◄ Prev", callback_data=f"stories_p|{page-1}|{caller_id}"))
    nav.append(InlineKeyboardButton(f"1/{total_pages}", callback_data="noop"))
    if has_next:
        nav.append(InlineKeyboardButton("Next ►", callback_data=f"stories_p|{page+1}|{caller_id}"))
    keyboard = [
        nav, 
        [
            InlineKeyboardButton("🗑️ Delete", callback_data=f"story_delete|{caller_id}"),
            InlineKeyboardButton("📋 Full List", callback_data=f"story_wtf|{caller_id}")
        ]
    ]

    cmd_msg = update.message

    reply = await update.message.reply_text(
        text=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    await log(
        context,
        f"STORIES | user_id={cmd_msg.from_user.id} username={cmd_msg.from_user.username}"
    )

    # Delete /stories command message after 5s, reply after 30 min
    async def _delete_cmd():
        await asyncio.sleep(5)
        try:
            await cmd_msg.delete()
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


# -----------------------
# request story
# -----------------------

async def request_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _enforce_cooldown(update, context):
        return

    # Always delete the /request command message from the group after 5 seconds
    async def _delete_cmd_msg():
        await asyncio.sleep(5)
        try:
            await update.message.delete()
        except Exception:
            pass

    if update.message:
        asyncio.create_task(_delete_cmd_msg())

    if not context.args:

        chat = update.effective_chat
        lang = get_chat_lang(chat.id)

        if lang == "hi":
            warn_text = """
<b>✮ कृपया स्टोरी/सीरीज़ का नाम लिखें</b>

<i>➔ उदाहरण:</i>
▪ /request Vashikaran
▪ /request Saaya
"""
        else:
            warn_text = """
<b>✮ Please provide the name of the Story/Series</b>

<i>➔ Examples:</i>
▪ /request Vashikaran
▪ /request Saaya
"""

        warn_msg = await update.effective_chat.send_message(warn_text)

        async def _delete_warn():
            await asyncio.sleep(30)
            try:
                await warn_msg.delete()
            except Exception:
                pass

        asyncio.create_task(_delete_warn())
        return

    story_raw = " ".join(context.args).strip()
    if not story_raw or len(story_raw) < 2:
        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            warn_text = "<b>✮ कृपया स्टोरी का सही नाम लिखें</b>\n\n<i>➔ उदाहरण:</i>\n▪ /request Vashikaran\n▪ /request Saaya"
        else:
            warn_text = "<b>✮ Please enter a valid story name</b>\n\n<i>➔ Examples:</i>\n▪ /request Vashikaran\n▪ /request Saaya"
        warn_msg = await update.effective_chat.send_message(warn_text, parse_mode="HTML")
        async def _del_warn():
            await asyncio.sleep(30)
            try: await warn_msg.delete()
            except: pass
        asyncio.create_task(_del_warn())
        return
    story = clean_story(story_raw).lower()

    # if story already exists in DB or matches alias, no need to request
    existing = search_story_exact_or_alias(story)
    if existing:
        link = existing.get("link", "")
        story_key = existing.get("key", story)
        user = update.effective_user
        mention = user.mention_html() if user else ""
        lang = get_chat_lang(update.effective_chat.id)
        
        # Check if link is marked as broken
        flag = link_flags.get(story_key, {})
        if flag.get("broken"):
            if lang == "hi":
                text = f"""
<b>{mention}</b>

<b>☆ लिंक अस्थायी रूप से अनुपलब्ध है</b>
<i>यह स्टोरी हमारे डेटाबेस में है, लेकिन फिलहाल इसका लिंक काम नहीं कर रहा है। एडमिन्स को सूचित कर दिया गया है। जब लिंक फिक्स हो जाएगा, तब आप इसे एक्सेस कर पाएंगे।</i>
"""
            else:
                text = f"""
<b>{mention}</b>

<b>☆ Link Temporarily Unavailable</b>
<i>This story is in our database, but its link is currently broken or experiencing issues. Admins have been notified. Please wait until it is fixed.</i>
"""
            await update.effective_chat.send_message(text=text, parse_mode="HTML")
            try:
                await update.message.delete()
            except Exception:
                pass
            return
            
        # Send normal existing link
        if lang == "hi":
            text = f"""
<b>{mention}</b>

<b>☆ यह स्टोरी पहले से उपलब्ध है</b>
<i>यह पहले से हमारे डेटाबेस में मौजूद है।</i>

<b>Link:</b> <tg-spoiler>{link}</tg-spoiler>
"""
        else:
            text = f"""
<b>{mention}</b>

<b>☆ Already in Database</b>
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
        
        spam_key = f"{user.id}:{story}"
        spam_requests_count[spam_key] = spam_requests_count.get(spam_key, 0) + 1
        
        if spam_requests_count[spam_key] >= 2:  # It warns them on the 3rd total attempt (1 original + 2 dupes)
            _set_cooldown(user.id, 30, "Repeated spam requests for the same voice/story")
            lang = get_chat_lang(update.effective_chat.id)
            if lang == "hi":
                text = f"<b>☆ Cooldown Active / आप कोल्डाउन पर हैं</b>\n\n{mention}\n✧ <i>कारण:</i> <b>एक ही स्टोरी के लिए बार-बार स्पैम रिक्वेस्ट करना</b>\n<i>कृपया लगभग 30 मिनट बाद फिर से कोशिश करें।</i>"
            else:
                text = f"<b>☆ Cooldown Active</b>\n\n{mention}\n✧ <i>Reason:</i> <b>Repeated spam requests for the same story</b>\n<i>Please try again after approximately 30 minutes.</i>"
            await update.effective_chat.send_message(text=text, parse_mode="HTML")
            return

        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            text = f"""
<b>{mention}</b>

<i>◌ आप पहले ही <b>{story}</b> रिक्वेस्ट कर चुके हैं।  
कृपया डुप्लीकेट रिक्वेस्ट न भेजें।</i>
"""
        else:
            text = f"""
<b>{mention}</b>

<i>◌ You have already requested <b>{story}</b>.  
Please avoid sending duplicate requests.</i>
"""
        await update.effective_chat.send_message(text=text, parse_mode="HTML")

        return

    request_db[story][chat_id].add(user.id)

    # total count across all chats
    count = sum(len(uids) for uids in request_db[story].values())

    username = f"@{user.username}" if user.username else "No username"

    # Format story key safely for callback
    safe_story = story[:40] # Prevent callback data from exceeding 64 bytes
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✖ REJECT", callback_data=f"req_rej|{user.id}|{safe_story}"),
            InlineKeyboardButton("◌ WARN", callback_data=f"req_warn|{user.id}|{safe_story}")
        ]
    ])

    sent_req = await context.bot.send_message(
        chat_id=REQUEST_GROUP,
        text=f"""
✶ Story Request

▪ Name: {story}
▪ User ID: {user.id}
▪ Username: {username}

▪ Total Requests: {count}
"""
    )
    
    # Store the message ID so we can aggressively delete it later if REJECT is pressed
    # We use a memory cache
    global _req_msg_cache
    if "_req_msg_cache" not in globals():
        _req_msg_cache = {}
    _req_msg_cache[safe_story] = sent_req.message_id

    if count == 1:

        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            text = f"""
<b>{mention}</b>

<b>✦ आपकी <i>{story}</i> की रिक्वेस्ट भेज दी गई है।
➔ हम इसे उपलब्ध कराने की पूरी कोशिश करेंगे।
➔ जैसे ही मिलेगी, जल्द अपलोड कर दी जाएगी।</b>
"""
        else:
            text = f"""
<b>{mention}</b>

<b>✦ Your request for <i>{story}</i> has been sent.
➔ We will try our best to provide this story.
➔ If we find it, it will be uploaded soon.</b>
"""

    else:

        others = count - 1

        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            text = f"""
<b>{mention}</b>

<b>✦ आपके साथ {others} और लोगों ने भी <i>{story}</i> रिक्वेस्ट की है।
➔ हम इसे उपलब्ध कराने की पूरी कोशिश करेंगे।
➔ जैसे ही मिलेगी, जल्द अपलोड कर दी जाएगी।</b>
"""
        else:
            text = f"""
<b>{mention}</b>

<b>✦ You and {others} others also requested <i>{story}</i>.
➔ We will try our best to provide this story.
➔ If we find it, it will be uploaded soon.</b>
"""

    save_requests({"requests": request_db})

    # --- Voting Queue Integration ---
    # story is already clean_story(story_raw).lower()
    in_queue = any(q["name"] == story for q in voting_queue)
    in_polls = any(story in p["options"] for p in active_polls.values())

    if not in_queue and not in_polls:
        voting_queue.append({
            "name": story,
            "requesters": {chat_id: [user.id]}
        })
    elif in_queue:
        # Update existing queue entry with new requester
        for q in voting_queue:
            if q["name"] == story:
                chat_reqs = q.get("requesters", {})
                chat_reqs.setdefault(chat_id, [])
                if user.id not in chat_reqs[chat_id]:
                    chat_reqs[chat_id].append(user.id)
                q["requesters"] = chat_reqs
                break
    
    save_voting_db({"queue": voting_queue, "polls": active_polls})

    queue_len = len(voting_queue)
    
    # Append queue status to reply text
    if lang == "hi":
        if queue_len < VOTING_SIZE_FOR_POLL:
            text += f"\n\n<b>📊 वोटिंग कतार:</b> <code>{queue_len}/{VOTING_SIZE_FOR_POLL}</code> स्टोरीज़\n<i>(कम्युनिटी वोटिंग शुरू होने के लिए {VOTING_SIZE_FOR_POLL} अलग-अलग स्टोरीज़ चाहिए।)</i>"
        else:
            text += f"\n\n<b>🎉 वोटिंग कतार फुल हो गई है!</b>\n<i>जल्द ही रिक्वेस्ट चैनल में एक नया पोल शुरू होगा।</i>"
    else:
        if queue_len < VOTING_SIZE_FOR_POLL:
            text += f"\n\n<b>📊 Voting Queue:</b> <code>{queue_len}/{VOTING_SIZE_FOR_POLL}</code> Stories\n<i>(A poll will start once {VOTING_SIZE_FOR_POLL} unique stories are requested.)</i>"
        else:
            text += f"\n\n<b>🎉 Voting Queue is full!</b>\n<i>A new poll will start shortly in the request channel.</i>"

    await update.effective_chat.send_message(
        text=text,
        parse_mode="HTML",
        reply_markup=kb
    )

    # Trigger poll creation if threshold reached
    if queue_len >= VOTING_SIZE_FOR_POLL:
        asyncio.create_task(trigger_community_poll(context, int(chat_id)))

    await log(
        context,
        f"REQUEST | user_id={user.id} username={user.username} story={story}"
    )


# ---------------------------------------------------------------------------
# Group Cleanup Handler
# Auto-deletes bot commands and @botusername messages from the group to keep
# the chat clean and organised for new users.
# ---------------------------------------------------------------------------

# --- Voting System Logic ---

async def trigger_community_poll(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Create a community poll if the queue has enough unique stories."""
    global voting_queue, active_polls
    
    # Remove empty/whitespace invalid targets that cause "Poll Creation Failed: Text must be non-empty"
    valid_queue = [q for q in voting_queue if str(q.get("name", "")).strip()]
    if len(valid_queue) != len(voting_queue):
        voting_queue = valid_queue
        save_voting_db({"queue": voting_queue, "polls": active_polls})
        
    if len(voting_queue) < VOTING_SIZE_FOR_POLL:
        return

    # Take the first 3 stories
    to_poll = voting_queue[:VOTING_SIZE_FOR_POLL]
    voting_queue = voting_queue[VOTING_SIZE_FOR_POLL:]
    
    # Truncate to 100 chars (Telegram API limit)
    options = [q["name"][:100] for q in to_poll]
    
    # Send poll to the requester's chat
    target_chat = chat_id
    
    try:
        poll_msg = await context.bot.send_poll(
            chat_id=target_chat,
            question="Which story should be uploaded next? (Community Vote)",
            options=options,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        # Restore queue on failure
        voting_queue = to_poll + voting_queue
        # Crucial: save the restored queue back to JSON so it doesn't stay out of sync if bot restarts!
        save_voting_db({"queue": voting_queue, "polls": active_polls})
        
        # Ping the admin so they aren't completely blind as to why Telegram rejected the poll!
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"❌ <b>Poll Creation Failed!</b>\nTelegram rejected the community poll.\n\n<pre>{html.escape(str(e))}</pre>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        return
    
    # Pin the poll
    try:
        await context.bot.pin_chat_message(chat_id=target_chat, message_id=poll_msg.message_id)
    except Exception:
        pass

    poll_id = poll_msg.poll.id
    active_polls[poll_id] = {
        "message_id": poll_msg.message_id,
        "chat_id": target_chat,
        "options": options,
        "votes": {str(i): [] for i in range(len(options))},
        "created_at": time.time(),
        "requesters": {q["name"]: q["requesters"] for q in to_poll}
    }
    
    save_voting_db({"queue": voting_queue, "polls": active_polls})
    await log(context, f"POLL CREATED | poll_id={poll_id} options={options}")


async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-anonymous poll state updates."""
    global active_polls, voting_queue
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_id = answer.user.id
    
    if poll_id not in active_polls:
        return

    poll_data = active_polls[poll_id]
    
    # Remove user from previous vote
    for vote_list in poll_data["votes"].values():
        if user_id in vote_list:
            vote_list.remove(user_id)
            
    # Add to new option
    for option_id in answer.option_ids:
        poll_data["votes"][str(option_id)].append(user_id)
        
    # Check max votes
    for option_id, voters in poll_data["votes"].items():
        if len(voters) >= VOTING_THRESHOLD:
            await _declare_poll_winner(context, poll_id, int(option_id))
            # Re-save voting_db to permanently lock out the poll since it's removed
            save_voting_db({"queue": voting_queue, "polls": active_polls})
            break


async def _declare_poll_winner(context, poll_id, winner_idx):
    """Declare a winner, notify, and clean up the poll."""
    global active_polls
    poll_data = active_polls.pop(poll_id, None)
    if not poll_data:
        return

    winner_name = poll_data["options"][winner_idx]
    chat_id = poll_data["chat_id"]
    msg_id = poll_data["message_id"]

    # Stop and unpin
    try:
        await context.bot.stop_poll(chat_id=chat_id, message_id=msg_id)
        await context.bot.unpin_chat_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

    # Announce winner
    win_text = f"🎊 <b>Community Voting Result</b>\n\nApproved Story: <b>{winner_name}</b>\n\nThis story has been approved by community voting and will be uploaded soon! 🚀"
    await context.bot.send_message(chat_id=chat_id, text=win_text, parse_mode="HTML")
    
    # Notify request channel
    if REQUEST_GROUP:
        await context.bot.send_message(
            chat_id=REQUEST_GROUP,
            text=f"✅ Story Approved via Voting: {winner_name}\nPoll ID: {poll_id}"
        )

    # Notify requesters
    requesters = poll_data.get("requesters", {}).get(winner_name, {})
    for rid_chat, uids in requesters.items():
        for uid in uids:
            try:
                mention = _user_mention_by_id(uid, fallback="User")
                await context.bot.send_message(
                    chat_id=int(rid_chat),
                    text=f"👋 {mention}, your requested story <b>{winner_name}</b> has been approved via community voting! It will be uploaded soon.",
                    parse_mode="HTML"
                )
            except Exception:
                pass


async def poll_timeout_manager(context: ContextTypes.DEFAULT_TYPE):
    """Background task to close stale polls."""
    global active_polls, voting_queue
    while True:
        await asyncio.sleep(3600) # Check every hour
        now = time.time()
        to_remove = []
        for pid, data in active_polls.items():
            if now - data["created_at"] > POLL_TIMEOUT_SEC:
                to_remove.append(pid)
        
        for pid in to_remove:
            data = active_polls.pop(pid)
            try:
                await context.bot.stop_poll(chat_id=data["chat_id"], message_id=data["message_id"])
                await context.bot.unpin_chat_message(chat_id=data["chat_id"], message_id=data["message_id"])
            except Exception:
                pass
        
        if to_remove:
            save_voting_db({"queue": voting_queue, "polls": active_polls})


async def group_cleanup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    In group chats: silently delete any message that is either:
      • A bot command (starts with /)
      • Text that mentions the bot's username (@botname)
    Uses a 5-second delay so the user briefly sees their message was received.
    Admin messages are also cleaned so the group stays tidy.
    """
    msg = update.message or update.edited_message
    if not msg:
        return

    # Only run in groups / supergroups
    if update.effective_chat.type not in ("group", "supergroup"):
        return

    # If GROUP_ID is configured, only clean that specific group
    if GROUP_ID and str(update.effective_chat.id) != str(GROUP_ID):
        return

    async def _do_delete():
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass

    asyncio.create_task(_do_delete())


def _user_mention_by_id(user_id: int, fallback: str = "User") -> str:
    """Return a HTML mention for a user ID."""
    return f'<a href="tg://user?id={user_id}">{html.escape(fallback)}</a>'

async def _notify_fulfilled_requests(context: ContextTypes.DEFAULT_TYPE):
    """After a scan, notify chats where stories were requested if now available."""
    global request_db

    if not isinstance(request_db, dict) or not request_db:
        return

    db = load_db()
    if not db:
        return

    # story_key -> per-chat user sets
    for req_key, chats in list(request_db.items()):
        if not isinstance(chats, dict):
            continue
            
        story = search_story_exact_or_alias(req_key)

        if not story or not story.get("link"):
            continue

        link = story.get("link", "")
        title = clean_story(story.get("text", story.get("name", req_key)))
        story_key_db = story.get("name") or req_key

        # Check if the found story is actually broken:
        lf = load_link_flags()
        if lf.get(story_key_db, {}).get("broken", False):
            continue # still broken, don't notify yet

        # notify each chat separately
        for chat_id_str, users in list(chats.items()):
            if not users:
                continue
            chat_id = int(chat_id_str)
            mentions = " ".join([_user_mention_by_id(uid) for uid in users])
            lang = get_chat_lang(chat_id)
            if lang == "hi":
                text = (
                    f"<b>✸ {mentions}</b>\n\n"
                    f"<b>❁ {title}</b>\n"
                    f"<i>➜ आपकी रिक्वेस्ट की हुई स्टोरी अब उपलब्ध है!</i>\n\n"
                    f"<b>➜ यहाँ पढ़ें:</b> {link}"
                )
            else:
                text = (
                    f"<b>✸ {mentions}</b>\n\n"
                    f"<b>❁ {title}</b>\n"
                    f"<i>➜ Your requested story is now available!</i>\n\n"
                    f"<b>➜ Read here:</b> {link}"
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
            request_db[req_key][chat_id_str] = set()

        # drop story entry if all chats cleared
        if not any(request_db[req_key].values()):
            request_db.pop(req_key, None)

    save_requests({"requests": request_db})


async def _check_force_sub(user_id, context):
    force_sub = bot_config.get("force_sub_channels", [])
    if not force_sub: return True
    for cid in force_sub:
        try:
            res = await context.bot.get_chat_member(cid, user_id)
            if res.status in ["left", "kicked"]: return False
        except Exception:
            pass
    return True

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

    # Strip any leading @mention from query (e.g. "@username StoryName" → "StoryName")
    # Also extract the tagged user so we can reply to them
    query_text = raw_text

    if query_text.isdigit():
        idx = int(query_text) - 1
        if 0 <= idx < len(story_index):
            query_text = story_index[idx]

    _tagged_user_mention: str | None = None
    _tagged_user_id: int | None = None
    if msg.entities:
        for _ent in msg.entities:
            if _ent.type == "mention":
                # @username at start → strip it from query
                mention_str = raw_text[_ent.offset : _ent.offset + _ent.length]
                query_text = raw_text[_ent.offset + _ent.length :].strip()
                _tagged_user_mention = mention_str
                break
            elif _ent.type == "text_mention" and _ent.user:
                mention_str = raw_text[_ent.offset : _ent.offset + _ent.length]
                query_text = raw_text[_ent.offset + _ent.length :].strip()
                _tagged_user_mention = _ent.user.mention_html()
                _tagged_user_id = _ent.user.id
                break

    if not query_text or len(query_text) < 2:
        return

    # Force Sub Check
    if not await _check_force_sub(user.id, context):
        keyboard = []
        for cid in bot_config.get("force_sub_channels", []):
            try:
                chat_info = await context.bot.get_chat(cid)
                invite_link = chat_info.invite_link
                if not invite_link and chat_info.username:
                    invite_link = f"https://t.me/{chat_info.username}"
                if invite_link:
                    title = chat_info.title or "Channel"
                    keyboard.append([InlineKeyboardButton(f"Join {title}", url=invite_link)])
            except Exception:
                pass
        keyboard.append([InlineKeyboardButton("✦ I've Joined", callback_data="check_sub")])
        if keyboard:
            await msg.reply_text("<b>⚠️ Access Denied</b>\n\nPlease join our channels to search stories.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
            return

    # If a scan is running, tell user to wait
    if IS_SCANNING:
        lang = get_chat_lang(update.effective_chat.id)
        await _send_scan_busy_notice(msg, lang)
        return

    # Rate limit
    now = time.time()
    last = cooldown_db.get(user.id, 0)
    if now - last < SEARCH_COOLDOWN:
        return
    cooldown_db[user.id] = now

    # Try exact match or alias match from Search Engine
    result = search_story_exact_or_alias(query_text)

    # If no story found, show suggestions
    if not result:
        await log(
            context,
            f"SEARCH MISS | user_id={user.id} username={user.username} query={query_text}"
        )
        
        suggestions = get_suggestions(query_text, limit=5)
        
        if len(suggestions) > 0:
            keyboard = []
            for s in suggestions:
                key = clean_story(s).lower()
                if len(f"srch|{key}") <= 64:
                    keyboard.append([InlineKeyboardButton(s, callback_data=f"srch|{key}")])
            lang = get_chat_lang(update.effective_chat.id)
            no_msg = await msg.reply_text(
                "✧ Did you mean one of these?" if lang != "hi" else "✧ क्या आप इनमें से कोई स्टोरी कहना चाहते थे?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            lang = get_chat_lang(update.effective_chat.id)
            no_msg = await msg.reply_text(
                ("☆ No story found with that name.\n\nCheck spelling or use /stories to see available titles."
                 if lang != "hi"
                 else "☆ इस नाम की कोई स्टोरी नहीं मिली।\n\nस्पेलिंग चेक करें या उपलब्ध टाइटल देखने के लिए /stories उपयोग करें।")
            )
        async def _del_no():
            await asyncio.sleep(30)
            try:
                await no_msg.delete()
            except Exception:
                pass
        asyncio.create_task(_del_no())
        return

    # Store original query message to delete it *after* we reply
    user_query_msg = msg

    target_mention = user.mention_html()
    target_user_id = user.id

    # Prefer reply_to_message as the tagging target
    if getattr(msg, "reply_to_message", None) and msg.reply_to_message.from_user:
        target_mention = msg.reply_to_message.from_user.mention_html()
        target_user_id = msg.reply_to_message.from_user.id
    elif _tagged_user_id:
        # text_mention entity (no @username)
        target_mention = _tagged_user_mention or user.mention_html()
        target_user_id = _tagged_user_id
    elif _tagged_user_mention:
        # plain @username mention — keep as string mention
        target_mention = _tagged_user_mention

    mention = target_mention

    story_name = clean_story(result.get("text", result.get("name", "Unknown")))
    story_key = result.get("name") or clean_story(result.get("text", "Unknown")).lower()

    # Update trending (real-time week-based limits)
    now = time.time()
    week_ago = now - (7 * 24 * 3600)
    
    if "trending" not in stats_db or not isinstance(stats_db["trending"], dict): 
        stats_db["trending"] = {}
        
    current_records = stats_db["trending"].get(story_key, [])
    if not isinstance(current_records, list):
        current_records = []  # Clear legacy integer formats
        
    # Prune older than 7 days, append new search
    current_records = [t for t in current_records if t >= week_ago]
    current_records.append(now)
    stats_db["trending"][story_key] = current_records
    
    # Occasional garbage collection of the entire trending DB to maintain size
    if random.random() < 0.1:
        for k in list(stats_db["trending"].keys()):
            r = stats_db["trending"][k]
            if isinstance(r, list):
                r = [t for t in r if t >= week_ago]
                if not r:
                    del stats_db["trending"][k]
                else:
                    stats_db["trending"][k] = r
    save_stats(stats_db)

    # Prefer pre‑computed story_type from the scanner, fallback to regex
    story_type = result.get("story_type")

    if not story_type:
        caption_text = result.get("caption", "")
        story_type = extract_story_type(caption_text)

    if not story_type:
        story_type = "Not specified"

    # Background checking interception:
    lf = load_link_flags()
    is_broken = lf.get(story_key, {}).get("broken", False)

    if is_broken:
        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            broken_msg = f"<b>☆ लिंक अस्थायी रूप से अनुपलब्ध है</b>\n\n<i>{html.escape(story_name)}</i>\n\nइस स्टोरी के लिंक में वर्तमान में कोई समस्या है (जैसे कॉपीराइट या डिलीट होना) और एडमिन्स को सूचित कर दिया गया है। कृपया समस्या के ठीक होने तक प्रतीक्षा करें।"
        else:
            broken_msg = f"<b>☆ Link Temporarily Unavailable</b>\n\n<i>{html.escape(story_name)}</i>\n\nThere is currently an issue with this story's link (like copyright or deletion) and admins have been notified. Please wait until it is fixed."
        
        sent = await msg.reply_text(broken_msg, parse_mode="HTML")
        async def _del_broken():
            await asyncio.sleep(30)
            try: await sent.delete()
            except: pass
        asyncio.create_task(_del_broken())
        return

    chat_id = update.effective_chat.id

    keyboard = []
    if result.get("link"):
        keyboard.append([InlineKeyboardButton("Open Story", url=result["link"])])
    
    # safeguard for long lengths
    fav_data = f"fav|{story_key}"
    if len(fav_data) > 64: fav_data = "fav|toolong"
    lnw_data = f"lnw|{story_key}"
    if len(lnw_data) > 64: lnw_data = "lnw|toolong"
    
    keyboard.append([
        InlineKeyboardButton("Favourites", callback_data=fav_data),
        InlineKeyboardButton("Link Broken?", callback_data=lnw_data)
    ])
    keyboard.append([InlineKeyboardButton("🗑️ Delete", callback_data="delete")])

    photo = result.get("photo") or result.get("image")
    story_type_line = f"\n<b>✽ Story Type:-</b> <i>{story_type}</i>" if story_type != "Not specified" else ""

    if result.get("format") in ("LIGHT", "LIGHT_PRO"):
        light_name     = result.get("text", story_name)
        light_status   = result.get("status", "Unknown")
        light_platform = result.get("platform", "Unknown")
        light_genre    = result.get("genre", "Unknown")
        caption = (
            f"Hey {mention} 👋\n"
            f"<b>✫ I found this story</b> ➴\n\n"
            f"♨️<b>Story</b> : <b>{html.escape(light_name)}</b>\n"
            f"🔰<b>Status</b> : <b>{html.escape(light_status)}</b>\n"
            f"🖥<b>Platform</b> : <b>{html.escape(light_platform)}</b>\n"
            f"🧩<b>Genre</b> : <b>{html.escape(light_genre)}</b>\n"
        )
        if result.get("format") == "LIGHT_PRO" and result.get("episodes"):
            caption += f"🎬<b>Episodes</b> : <b>{html.escape(result['episodes'])}</b>\n"
        caption += f"\n<tg-spoiler>◒ This reply will be deleted automatically in 5 minutes.</tg-spoiler>"
    else:
        caption = (
            f"Hey {mention} 👋\n"
            f"<b>✫ I found this story</b> ➴\n\n"
            f"<i>❁ Name:-</i> <b>{html.escape(story_name)}</b>{story_type_line}\n\n"
            f"<tg-spoiler>◒ This reply will be deleted automatically in 5 minutes.</tg-spoiler>"
        )

    if photo:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo="https://files.catbox.moe/i59f4o.jpg",
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


    message_owner[msg.message_id] = user.id

    try:
        await user_query_msg.delete()
    except Exception:
        pass

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

    if not query.data.startswith(("fav|", "check_sub")):
        try:
            await query.answer()
        except Exception:
            pass

    if await _enforce_cooldown(update, context):
        return

    if query.data.startswith("cfg_") or query.data.startswith("cfg|"):
        await query.answer()
        await _handle_config_callback(query, context)
        return

    # -----------------------
    # Admin Request Management (Reject / Warn)
    # -----------------------
    if query.data.startswith("req_rej|") or query.data.startswith("req_warn|"):
        if not is_admin(user.id):
            await query.answer("⛔ Admin only.", show_alert=True)
            return

        parts = query.data.split("|", 2)
        if len(parts) < 3:
            return
            
        action = parts[0]
        req_uid = int(parts[1])
        req_story = parts[2]
        
        # Clean from request_db
        to_delete = []
        for db_story in list(request_db.keys()):
            if db_story.startswith(req_story): # Matches sliced callback data
                for chat_id_str, uids in list(request_db[db_story].items()):
                    if req_uid in uids:
                        uids.remove(req_uid)
                to_delete.append(db_story)
                
        for k in to_delete:
            if not any(request_db[k].values()):
                request_db.pop(k, None)
        save_requests({"requests": request_db})
        
        try:
            await query.message.delete()
        except:
            pass
            
        # Delete from request channel if we have the message ID stored
        if action == "req_rej":
            global _req_msg_cache
            if "_req_msg_cache" in globals() and req_story in _req_msg_cache:
                try:
                    await context.bot.delete_message(chat_id=REQUEST_GROUP, message_id=_req_msg_cache[req_story])
                    _req_msg_cache.pop(req_story, None)
                except Exception:
                    pass

        # Language fallback logic for PMs since we don't have their original chat ID context here
        # We will attempt to send them a direct message
        if action == "req_rej":
            msg_text = f"<b>☆ Request Rejected</b>\n\nYour request for <i>{req_story}</i> has been rejected by administrators.\n(Reason: Fake, unavailable, or violates rules)\n\n<b>☆ रिक्वेस्ट रिजेक्ट</b>\nआपकी <i>{req_story}</i> की रिक्वेस्ट रिजेक्ट कर दी गई है।"
            try:
                await context.bot.send_message(req_uid, msg_text, parse_mode="HTML")
            except:
                pass

        elif action == "req_warn":
            _set_cooldown(req_uid, 30, f"Fake/Spam request warning for: {req_story}")
            msg_text = f"<b>☆ WARNING / चेतावनी</b>\n\nYou have been placed on a 30-minute cooldown by administrators for sending fake or spam requests (<i>{req_story}</i>).\n\nफेक या स्पैम रिक्वेस्ट भेजने के कारण एडमिन्स ने आपको 30 मिनट के cooldown पर रखा है।"
            try:
                await context.bot.send_message(req_uid, msg_text, parse_mode="HTML")
            except:
                pass
        return

    # ── PUNISH FAKE REPORT ──────────────────────────────────────────────────
    if query.data.startswith("punish|"):
        if not is_admin(user.id):
            await query.answer("⛔ Only admins can use this.", show_alert=True)
            return

        parts = query.data.split("|")
        # punish|fake|<reporter_id>
        if len(parts) >= 3 and parts[1] == "fake":
            try:
                target_uid = int(parts[2])
                try:
                    c = await context.bot.get_chat(target_uid)
                    target_nick = c.full_name or c.username or str(target_uid)
                except Exception:
                    target_nick = f"User {target_uid}"
            except ValueError:
                await query.answer("Invalid user ID.", show_alert=True)
                return

            _set_cooldown(target_uid, 2880, "Fake report")  # 2 days = 2880 mins
            await query.answer(f"🔨 {target_nick} punished for 2 days.", show_alert=True)

            msg_text = (
                f"<b>⚠ WARNING / चेतावनी</b>\n\n"
                f"<a href='tg://user?id={target_uid}'>{target_nick}</a> you have been placed on a 2-day timeout by administrators for submitting a fake report.\n\n"
                f"<blockquote expandable>If you are not able to access episodes of any story from the bot, then check:\n\n"
                f"1. Click the link given in the channel — it will take you to a bot.\n\n"
                f"2. After opening the bot, it will ask you to join 3–4 channels. Complete that step and try again. Then your episodes will start working (they remain available for 6–8 hours and then get deleted due to possible copyright issues). The same process applies to all stories available in the bot.\n\n"
                f"3. If you are unable to find stories like Saaya, Vashikaran, or Yakshini, please scroll a bit — you will find them.\n\n"
                f"4. If you searched for another story and got a wrong result, that still does not give you the right to misuse this feature for fun.</blockquote>\n\n"
                f"<i>“Sorry, but I’m human — so be kind. Do not send fake reports again, otherwise you may be banned.”</i>"
            )
            try:
                await context.bot.send_message(chat_id=query.message.chat.id, text=msg_text, parse_mode="HTML")
            except Exception:
                pass
        return

    # ── NEW: menu|<section>|<caller_id>  ──────────────────────────────────────
    # Every section edits the SAME message. No new messages are ever sent.
    if query.data.startswith("menu|"):
        parts = query.data.split("|")
        # parts: ["menu", section, caller_id]
        section = parts[1] if len(parts) > 1 else "home"
        try:
            caller_id = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            caller_id = 0

        lang = get_chat_lang(query.message.chat.id) if query.message else "en"

        # Close / delete the menu
        if section == "close":
            if user.id == caller_id or is_admin(user.id):
                try:
                    await query.message.delete()
                except Exception:
                    await query.answer("Could not delete.", show_alert=True)
            else:
                await query.answer("⛔ Only the user who opened this menu (or an admin) can close it.", show_alert=True)
            return

        # Build the right section
        if section in ("home", "start"):
            text, markup = _menu_main(caller_id, lang, query.from_user.mention_html())
        elif section == "trending":
            text, markup = _menu_trending(caller_id)
        elif section == "new":
            text, markup = _menu_new(caller_id)
        elif section == "saved":
            text, markup = _menu_saved(caller_id)
        elif section == "browse":
            text, markup = _menu_browse(caller_id)
        elif section == "how":
            text, markup = _menu_how(caller_id, lang)
        elif section == "about":
            text, markup = _menu_about(caller_id, lang)
        elif section == "help":
            text, markup = _menu_help(caller_id, lang)
        elif section == "lang":
            text, markup = _menu_lang(caller_id)
        else:
            await query.answer("Unknown section.", show_alert=True)
            return

        try:
            await query.message.edit_text(text=text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass  # already same content, ignore "message not modified"
        await query.answer()
        return

    # ── LEGACY: cmd|<section>  ──────────────────────────────────────────────
    # Kept for any buttons created before the new menu system. Routes to menu|.
    if query.data.startswith("cmd|"):
        cmd = query.data.split("|")[1]
        caller_id = user.id
        lang = get_chat_lang(query.message.chat.id) if query.message else "en"
        section_map = {
            "start": "home", "help": "help", "about": "about",
            "how": "how", "trending": "trending", "saved": "saved",
            "browse": "browse", "new": "new", "lang_menu": "lang",
        }
        section = section_map.get(cmd)
        if section:
            if section == "home":
                text, markup = _menu_main(caller_id, lang, query.from_user.mention_html())
            elif section == "trending":
                text, markup = _menu_trending(caller_id)
            elif section == "new":
                text, markup = _menu_new(caller_id)
            elif section == "saved":
                text, markup = _menu_saved(caller_id)
            elif section == "browse":
                text, markup = _menu_browse(caller_id)
            elif section == "how":
                text, markup = _menu_how(caller_id, lang)
            elif section == "about":
                text, markup = _menu_about(caller_id, lang)
            elif section == "help":
                text, markup = _menu_help(caller_id, lang)
            elif section == "lang":
                text, markup = _menu_lang(caller_id)
            else:
                await query.answer()
                return
            try:
                await query.message.edit_text(text=text, parse_mode="HTML", reply_markup=markup)
            except Exception:
                pass
        await query.answer()
        return


    if query.data.startswith("fav|"):
        story_key = query.data.split("|")[1]
        user_id_str = str(user.id)
        if user_id_str not in favorites_db: favorites_db[user_id_str] = []
        if story_key in favorites_db[user_id_str]:
            favorites_db[user_id_str].remove(story_key)
            await query.answer("☆ Removed from Favourites", show_alert=False)
        else:
            favorites_db[user_id_str].append(story_key)
            await query.answer("★ Added to Favourites!", show_alert=False)
        save_favorites(favorites_db)
        return

    if query.data == "check_sub":
        if await _check_force_sub(user.id, context):
            await query.answer("Thanks for joining! You can now use the bot.", show_alert=True)
            try: await query.message.delete()
            except: pass
        else:
            await query.answer("You haven't joined all channels yet. Please join them first.", show_alert=True)
        return

    if query.data.startswith("status_delete|"):
        # Allow the original caller and admins to delete the status message
        try:
            caller_id = int(query.data.split("|")[1])
        except (IndexError, ValueError):
            caller_id = 0
        if user.id == caller_id or is_admin(user.id):
            try:
                await query.message.delete()
            except Exception:
                await query.answer("Could not delete.", show_alert=True)
        else:
            await query.answer("⛔ Only the person who ran /status (or an admin) can delete this.", show_alert=True)
        return

    if query.data.startswith("lang|"):
        await query.answer()
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
            [InlineKeyboardButton("➔ Open Story", url=result["link"])],
            [InlineKeyboardButton("◌ Link Not Working?", callback_data=f"lnw|{story_key}")],
            [InlineKeyboardButton("🗑️ Delete", callback_data="delete")]
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
                sent = await context.bot.send_photo(
                    chat_id=query.message.chat.id,
                    photo="https://files.catbox.moe/i59f4o.jpg",
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

    if query.data == "noop":
        await query.answer()
        return

    if query.data.startswith("story_delete|"):
        try:
            cid = int(query.data.split("|")[1])
        except (IndexError, ValueError):
            cid = 0
        if user.id == cid or is_admin(user.id):
            try:
                await query.message.delete()
            except Exception:
                await query.answer("✧ Could not delete.", show_alert=True)
        else:
            await query.answer("⛔ Only the user who opened /stories (or an admin) can delete this.", show_alert=True)
        return

    if query.data.startswith("stories_p|"):
        parts = query.data.split("|")
        try:
            page = int(parts[1])
            caller_id = int(parts[2]) if len(parts) > 2 else 0
        except (IndexError, ValueError):
            await query.answer()
            return
        text, has_prev, has_next, _ = _stories_page(page)
        total_pages = (len(story_index) + STORIES_PER_PAGE - 1) // STORIES_PER_PAGE
        nav = []
        if has_prev:
            nav.append(InlineKeyboardButton("◄ Prev", callback_data=f"stories_p|{page-1}|{caller_id}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        if has_next:
            nav.append(InlineKeyboardButton("Next ►", callback_data=f"stories_p|{page+1}|{caller_id}"))
        rows = [nav, [InlineKeyboardButton("🗑️ Delete", callback_data=f"story_delete|{caller_id}")]]
        try:
            await query.message.edit_text(
                text=text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(rows)
            )
        except Exception:
            pass
        await query.answer()
        return

    if query.data.startswith("story_wtf|"):
        try:
            caller_id = int(query.data.split("|")[1])
        except:
            caller_id = 0
            
        if user.id != caller_id and not is_admin(user.id):
            await query.answer("⛔ Only the user who opened /stories (or an admin) can do this.", show_alert=True)
            return
            
        await query.answer("Preparing complete index...")
        try:
            await query.message.delete()
        except:
            pass
            
        await storylist_cmd(update, context)
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
        story_key = query.data.split("|", 1)[1]
        
        story = get_story(story_key)
        if not story:
            await query.answer("Story not found.", show_alert=True)
            return
        
        chat_id = query.message.chat.id
        user_id = user.id
        story_name = clean_story(story.get("text", story.get("name", "")))
        
        lang = get_chat_lang(chat_id)

        # 1. Already confirmed broken — block all users
        existing_flag = link_flags.get(story_key)
        if existing_flag and existing_flag.get("broken"):
            if lang == "hi":
                txt = "⚠ यह लिंक पहले ही रिपोर्ट किया जा चुका है। इसे जल्द ही चैनल में फिक्स और अपडेट किया जाएगा, कृपया प्रतीक्षा करें।"
            else:
                txt = "⚠ This link is already reported. It will be fixed and updated in the channel soon, please wait."
            await query.answer(txt, show_alert=True)
            return

        # 2. Active vote already running for this story — block the user from raising another
        vote_id = f"{chat_id}:{story_key}"
        existing_vote = active_link_votes.get(vote_id)
        if existing_vote:
            if user_id in existing_vote.get("voters", {}):
                txt = "⛔ आपने पहले ही इस वोट में भाग लिया है।" if lang == "hi" else "⛔ You have already participated in this broken link report."
                await query.answer(txt, show_alert=True)
            else:
                txt = "⚠ इस लिंक की जांच पहले से हो रही है। कृपया प्रतीक्षा करें।" if lang == "hi" else "⚠ This link is already reported. It will be fixed and updated in the channel soon, please wait."
                await query.answer(txt, show_alert=True)
            return
        
        lang = get_chat_lang(chat_id)
        if lang == "hi":
            text = (
                f"<b>★ लिंक रिपोर्ट</b>\n\n"
                f"<i>{story_name}</i>\n\n"
                "अगर यह स्टोरी लिंक सच में काम नहीं कर रहा है, तो नीचे कन्फर्म करें।"
            )
            confirm_label = "✅ कन्फर्म रिपोर्ट"
            cancel_label = "❌ कैंसल"
        else:
            text = (
                f"<b>★ Report Broken Link</b>\n\n"
                f"<i>{story_name}</i>\n\n"
                "If this story link is really broken, please confirm below."
            )
            confirm_label = "✅ Confirm"
            cancel_label = "❌ Cancel"

        reporter_id = user.id

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(confirm_label, callback_data=f"lnw_confirm|{story_key}|{reporter_id}"),
                    InlineKeyboardButton(cancel_label, callback_data=f"lnw_cancel|{reporter_id}"),
                ],
                [
                    InlineKeyboardButton("🔨 Punish User", callback_data=f"punish|fake|{reporter_id}")
                ]
            ]
        )
        conf = await query.message.reply_text(text=text, parse_mode="HTML", reply_markup=kb)

        async def _del_conf():
            await asyncio.sleep(300) # 5 minutes
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

        # Allow: original reporter OR any admin
        if reporter_id is not None and reporter_id != user.id and not is_admin(user.id):
            lang = get_chat_lang(query.message.chat.id)
            txt = "<b>Only the requester or an admin can cancel this report.</b>" if lang != "hi" else "<b>केवल requester या एडमिन ही इस रिपोर्ट को कैंसल कर सकते हैं।</b>"
            await query.answer(txt, show_alert=True)
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
                "reporter_id": reporter_id if reporter_id is not None else user_id,
            }
            active_link_votes[vote_id] = vote
        display_name = user.full_name or user.first_name or str(user_id)
        vote["voters"][user_id] = display_name

        current = len(vote["voters"])
        required = 3

        lang = get_chat_lang(chat_id)
        if lang == "hi":
            title = "<b>★ लिंक वेरीफिकेशन वोट</b>"
            body = f"<i>{story_name}</i>\n\n"
            body += f"✧ <b>उद्देश्य:</b> यह जांचना के लिए कि स्टोरी लिंक काम कर रहा है या टूटा हुआ है\n\n"
            votes_line = f"• <b>वोट:</b> {current} / {required} (कुल {required - current} और वोट चाहिए)"
            broken_label = "❌ लिंक नहीं चल रहा"
            ok_label = "🔗 चल रहा है"
        else:
            title = "<b>★ Link Verification Vote</b>"
            body = f"<i>{story_name}</i>\n\n"
            body += f"✧ <b>Purpose:</b> To verify if the story link is working or broken\n\n"
            votes_line = f"• <b>Votes:</b> {current} / {required} ({required - current} more votes needed)"
            broken_label = "🔗 Broken"
            ok_label = "🔗 Working"

        text = f"{title}\n\n{body}{votes_line}"
        # Build vote keyboard with Dismiss button for admins
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(broken_label, callback_data=f"lnwv_broken|{vote_id}"),
                    InlineKeyboardButton(ok_label, callback_data=f"lnwv_ok|{vote_id}"),
                ],
                [
                    InlineKeyboardButton("🚫 Dismiss", callback_data=f"lnwv_dismiss|{vote_id}"),
                    InlineKeyboardButton("🔨 Punish User", callback_data=f"punish|fake|{vote['reporter_id']}")
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
            # auto-unpin and delete vote after 6h if not resolved
            async def _cleanup_vote(m_id, v_id, c_id):
                await asyncio.sleep(21600) # 6 hours
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

    # ── lnwv_dismiss: Admin-only dismiss ──────────────────────────────────────
    if query.data.startswith("lnwv_dismiss|"):
        vote_id = query.data.split("|", 1)[1]
        vote = active_link_votes.get(vote_id)
        if not vote:
            await query.answer()
            return

        # Admin-only check
        if not is_admin(user.id):
            await query.answer("⛔ Only admins can dismiss reports.", show_alert=True)
            return

        chat_id = vote["chat_id"]
        reporter_id = vote.get("reporter_id")
        story_name = vote.get("story_name", "")

        # Remove the vote
        active_link_votes.pop(vote_id, None)

        # Unpin + delete the vote message
        try:
            await context.bot.unpin_chat_message(chat_id=chat_id, message_id=vote["message_id"])
        except Exception:
            pass
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=vote["message_id"])
        except Exception:
            pass

        # Send warning to the reporter using Telegram expandable blockquote
        reporter_mention = _user_mention_by_id(reporter_id, fallback=str(reporter_id)) if reporter_id else "User"
        lang = get_chat_lang(chat_id)
        if lang == "hi":
            warn_text = (
                f"<blockquote expandable>"
                f"⚠️ <b>गलत रिपोर्ट अलर्ट</b>\n\n"
                f"{reporter_mention},\n"
                f"आपने <i>{story_name}</i> के लिए \"Link Not Working\" रिपोर्ट की थी।\n\n"
                f"✅ एडमिन द्वारा जाँच की गई — <b>लिंक सही काम कर रहा है।</b>\n\n"
                f"कृपया रिपोर्ट करने से पहले लिंक को ठीक से चेक करें।\n\n"
                f"⛔ <i>बार-बार गलत रिपोर्ट करने पर भविष्य में चेतावनी या प्रतिबंध लग सकता है।</i>"
                f"</blockquote>"
            )
        else:
            warn_text = (
                f"<blockquote expandable>"
                f"⚠️ <b>False Report Alert</b>\n\n"
                f"{reporter_mention},\n"
                f"You reported the link for <i>{story_name}</i> as not working.\n\n"
                f"✅ Verified by admin — <b>the link is working fine.</b>\n\n"
                f"Please check the link properly before reporting.\n\n"
                f"⛔ <i>Repeated false reports may lead to warnings or restrictions in the future.</i>"
                f"</blockquote>"
            )

        warn_msg = await context.bot.send_message(chat_id=chat_id, text=warn_text, parse_mode="HTML")

        # Auto-delete warning after 2 minutes
        async def _del_dismiss_warn(msg):
            await asyncio.sleep(120)
            try:
                await msg.delete()
            except Exception:
                pass
        asyncio.create_task(_del_dismiss_warn(warn_msg))

        await query.answer("✅ Vote dismissed and reporter warned.", show_alert=False)
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

        # ── "Working" button: immediately stop the vote ──
        if action == "lnwv_ok":
            # Remove from active votes
            active_link_votes.pop(vote_id, None)

            # Unpin + delete the vote message
            try:
                await context.bot.unpin_chat_message(chat_id=chat_id, message_id=vote["message_id"])
            except Exception:
                pass
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=vote["message_id"])
            except Exception:
                pass

            await query.answer("✅ Link marked as working. Vote stopped.", show_alert=True)
            return

        # ── "Broken" button: add vote ──
        if action == "lnwv_broken":
            display_name = user.full_name or user.first_name or str(user.id)
            vote["voters"][user.id] = display_name

        current = len(vote["voters"])
        required = 3

        lang = get_chat_lang(chat_id)
        if lang == "hi":
            title = "<b>★ लिंक वेरीफिकेशन वोट</b>"
            body = f"<i>{story_name}</i>\n\n"
            body += f"✧ <b>उद्देश्य:</b> यह जांचना के लिए कि स्टोरी लिंक काम कर रहा है या टूटा हुआ है\n\n"
            votes_line = f"• <b>वोट:</b> {current} / {required} (कुल {required - current} और वोट चाहिए)"
            broken_label = "❌ लिंक नहीं चल रहा"
            ok_label = "🔗 चल रहा है"
        else:
            title = "<b>★ Link Verification Vote</b>"
            body = f"<i>{story_name}</i>\n\n"
            body += f"✧ <b>Purpose:</b> To verify if the story link is working or broken\n\n"
            votes_line = f"• <b>Votes:</b> {current} / {required} ({required - current} more votes needed)"
            broken_label = "🔗 Broken"
            ok_label = "🔗 Working"

        text = f"{title}\n\n{body}{votes_line}"
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(broken_label, callback_data=f"lnwv_broken|{vote_id}"),
                    InlineKeyboardButton(ok_label, callback_data=f"lnwv_ok|{vote_id}"),
                ],
                [
                    InlineKeyboardButton("🚫 Dismiss", callback_data=f"lnwv_dismiss|{vote_id}"),
                    InlineKeyboardButton("🔨 Punish User", callback_data=f"punish|fake|{vote['reporter_id']}")
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
                    f"<b>✦ लिंक टूटा हुआ कन्फर्म हो गया</b>\n\n"
                    f"{mentions}\n\n"
                    f"<i>{story_name}</i>\n"
                    f"<b>Link:</b> {link}"
                )
            else:
                final_text = (
                    f"<b>✦ Link Confirmed Broken</b>\n\n"
                    f"{mentions}\n\n"
                    f"<i>{story_name}</i>\n"
                    f"<b>Link:</b> {link}"
                )
            try:
                final_msg = await context.bot.send_message(chat_id=chat_id, text=final_text, parse_mode="HTML")
                
                async def _delete_final(msg):
                    await asyncio.sleep(43200) # 12 hours
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                
                asyncio.create_task(_delete_final(final_msg))
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
                    sent_copy = await context.bot.send_message(chat_id=COPYRIGHT_CHANNEL, text=report, parse_mode="HTML")
                    global _copy_msg_cache
                    if "_copy_msg_cache" not in globals():
                        _copy_msg_cache = {}
                    _copy_msg_cache[story_key] = sent_copy.message_id
                except Exception as e:
                    logger.warning("Failed to send link broken report: %s", e)

# clean up vote
            active_link_votes.pop(vote_id, None)

        return


# -----------------------
# inline search
# -----------------------

async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    # Maintenance mode block (non-admins)
    if MAINTENANCE_MODE and not is_admin(update.inline_query.from_user.id):
        if MAINTENANCE_UNTIL > 0 and time.time() >= MAINTENANCE_UNTIL:
            _end_maintenance()
        else:
            await update.inline_query.answer(
                [
                    InlineQueryResultArticle(
                        id="maintenance",
                        title="🔧 Maintenance Mode",
                        description="Riya is currently under maintenance. Please try again later.",
                        input_message_content=InputTextMessageContent(
                            "🔧 Riya is currently under scheduled maintenance. We will be back shortly. Thank you for your patience! 🙏"
                        )
                    )
                ],
                cache_time=30
            )
            return

    if IS_SCANNING:
        await update.inline_query.answer(
            [
                InlineQueryResultArticle(
                    id="scanning",
                    title="⏳ Database Updating",
                    description="The bot is currently scanning for updates. Please try again soon.",
                    input_message_content=InputTextMessageContent("Riya is currently updating the database. The bot will automatically start working once complete.")
                )
            ],
            cache_time=5
        )
        return

    query = update.inline_query.query

    if not query:
        return

    results = fast_search_contains(query, limit=10)

    articles = []

    for story in results:
        res = get_story(clean_story(story).lower())
        link = res.get("link") if res else ""
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("➔ Open Story", url=link)]]) if link else None

        articles.append(
            InlineQueryResultArticle(
                id=clean_story(story).lower(),
                title=clean_story(story),
                input_message_content=InputTextMessageContent(clean_story(story)),
                reply_markup=keyboard
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
            "<b>★ Thanks for adding Riya Bot</b>\n\n"
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
    chats = set(chat_languages.keys())
    if GROUP_ID: chats.add(str(GROUP_ID))
    count = 0
    for cid in chats:
        try:
            await context.bot.send_message(chat_id=int(cid), text=text)
            count += 1
            await asyncio.sleep(0.1) # flood control
        except Exception:
            pass
    lang = get_chat_lang(update.effective_chat.id)
    await update.message.reply_text(f"✦ Announcement sent to {count} chats." if lang != "hi" else f"✦ अनाउंसमेंट {count} चैट्स को भेज दिया गया।")


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
    await update.message.reply_text("✦ Updated." if lang == "en" else "✦ भाषा अपडेट हो गई।")


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


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG MENU SYSTEM
#  Premium inline config panel — all navigation edits the same message.
#  callback_data format: cfg|<section>|<action>|<caller_id>
# ─────────────────────────────────────────────────────────────────────────────

_CFG_DIV = "━━━━━━━━━━━━━━━━"


def _cfg_nav(caller_id: int, back: str = "main") -> list:
    """Standard config nav row: [➦ Back][✖ Close]"""
    return [
        InlineKeyboardButton("➦ Back",    callback_data=f"cfg|{back}||{caller_id}"),
        InlineKeyboardButton("✖ Close",  callback_data=f"cfg|close||{caller_id}"),
    ]


def _cfg_main_panel(caller_id: int, lang: str = "en") -> tuple:
    sources = bot_config.get("sources", [])
    formats = bot_config.get("formats", {})
    timers  = bot_config.get("auto_delete", {})
    cur_lang = "हिन्दी" if lang == "hi" else "English"
    if lang == "hi":
        header = (
            "<b>✺ Configuration Panel</b>\n"
            "<i>▪ Bot settings aur system options manage karein.</i>\n"
            f"{_CFG_DIV}\n\n"
            f"▪ Sources: <b>{len(sources)}</b>  "
            f"▪ Formats: <b>{sum(len(v) for v in formats.values()) if isinstance(formats, dict) else 0}</b>  "
            f"▪ Language: <b>{cur_lang}</b>"
        )
    else:
        header = (
            "<b>✺ Configuration Panel</b>\n"
            "<i>▪ Manage bot settings and system options.</i>\n"
            f"{_CFG_DIV}\n\n"
            f"▪ Sources: <b>{len(sources)}</b>  "
            f"▪ Formats: <b>{sum(len(v) for v in formats.values()) if isinstance(formats, dict) else 0}</b>  "
            f"▫ Language: <b>{cur_lang}</b>"
        )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✦ Language",      callback_data=f"cfg|lang||{caller_id}"),
            InlineKeyboardButton("✦ Timers",         callback_data=f"cfg|timers||{caller_id}"),
        ],
        [
            InlineKeyboardButton("✦ Channels",      callback_data=f"cfg|sources||{caller_id}"),
            InlineKeyboardButton("✦ Formats",        callback_data=f"cfg|formats||{caller_id}"),
        ],
        [
            InlineKeyboardButton("✦ System Info",   callback_data=f"cfg|sysinfo||{caller_id}"),
            InlineKeyboardButton("🔧 Maintenance",   callback_data=f"cfg|maintenance||{caller_id}"),
        ],
        [
            InlineKeyboardButton("✧ Refresh",        callback_data=f"cfg|main||{caller_id}"),
            InlineKeyboardButton("✖ Close",          callback_data=f"cfg|close||{caller_id}"),
        ],
    ])
    return header, markup


def _cfg_lang_panel(caller_id: int, chat_id: int, lang: str = "en") -> tuple:
    cur = get_chat_lang(chat_id)
    en_mark = "★" if cur == "en" else "☆"
    hi_mark  = "★" if cur == "hi" else "☆"
    text = (
        "<b>✦ Language Settings</b>\n"
        "<i>Select the interface language for this chat.</i>\n"
        f"{_CFG_DIV}\n\n"
        f"{en_mark} English  |  {hi_mark} हिन्दी\n\n"
        "<i>Tap a language to activate it.</i>"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{en_mark} English",  callback_data=f"cfg|setlang|en|{caller_id}"),
            InlineKeyboardButton(f"{hi_mark} हिन्दी",    callback_data=f"cfg|setlang|hi|{caller_id}"),
        ],
        _cfg_nav(caller_id, back="main"),
    ])
    return text, markup


def _cfg_timers_panel(caller_id: int, lang: str = "en") -> tuple:
    timers = bot_config.get("auto_delete", {})
    if timers:
        lines = "\n".join(f"✦ <b>{k}</b>  →  <code>{v}s</code>" for k, v in timers.items())
    else:
        body = "<i>✧ No timers configured.</i>"
        lines = body
    text = (
        "<b>✦ Auto-Delete Timers</b>\n"
        "<i>Manage message auto-deletion intervals.</i>\n"
        f"{_CFG_DIV}\n\n"
        f"{lines}\n\n"
        "<i>Use /settimer &lt;key&gt; &lt;seconds&gt; to change a timer.</i>"
    )
    markup = InlineKeyboardMarkup([_cfg_nav(caller_id, back="main")])
    return text, markup


def _cfg_sources_panel(caller_id: int, lang: str = "en") -> tuple:
    sources = bot_config.get("sources", [])
    if sources:
        lines = "\n".join(f"✦ <code>{c}</code>" for c in sources)
        body = f"<b>{len(sources)} configured</b>\n\n{lines}"
    else:
        body = "<i>✧ No source channels configured.</i>"
    text = (
        "<b>✦ Source Channels</b>\n"
        "<i>Channels the bot scans for stories.</i>\n"
        f"{_CFG_DIV}\n\n"
        f"{body}"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✚ Add",    callback_data=f"cfg|add_source||{caller_id}"),
            InlineKeyboardButton("✖ Remove", callback_data=f"cfg|rm_source||{caller_id}"),
        ],
        _cfg_nav(caller_id, back="main"),
    ])
    return text, markup


def _cfg_add_source_panel(caller_id: int) -> tuple:
    text = (
        "<b>✦ Add Source Channel</b>\n"
        "<i>Send the channel ID to add it.</i>\n"
        f"{_CFG_DIV}\n\n"
        "Send a message with the channel ID:\n"
        "<code>-1001234567890</code>\n\n"
        "<i>✧ The bot must be an admin in that channel.</i>"
    )
    markup = InlineKeyboardMarkup([_cfg_nav(caller_id, back="sources")])
    return text, markup


def _cfg_rm_source_panel(caller_id: int, lang: str = "en") -> tuple:
    sources = bot_config.get("sources", [])
    if not sources:
        text = (
            "<b>✦ Remove Channel</b>\n"
            "<i>No channels to remove.</i>\n"
            f"{_CFG_DIV}"
        )
        markup = InlineKeyboardMarkup([_cfg_nav(caller_id, back="sources")])
        return text, markup
    text = (
        "<b>✦ Remove Channel</b>\n"
        "<i>Tap a channel to remove it.</i>\n"
        f"{_CFG_DIV}"
    )
    rows = [[InlineKeyboardButton(f"✕ {c}", callback_data=f"cfg|do_rm_src|{c}|{caller_id}")] for c in sources]
    rows.append(_cfg_nav(caller_id, back="sources"))
    markup = InlineKeyboardMarkup(rows)
    return text, markup


def _cfg_formats_panel(caller_id: int, lang: str = "en") -> tuple:
    """Main Formats Management screen."""
    fmts = learned_formats_db
    total = sum(len(v) for v in fmts.values() if isinstance(v, list))
    if total:
        lines = "\n".join(
            f"✦ <code>{cid}</code>  →  <b>{len(v)} template(s)</b>"
            for cid, v in fmts.items() if isinstance(v, list)
        )
        body = f"<b>{total} learned template(s)</b>\n\n{lines}"
    else:
        body = "<i>☆ No formats learned yet.</i>"

    text = (
        "<b>★ Format Learning System</b>\n"
        "<i>Teach the bot which posts are stories.</i>\n"
        f"{_CFG_DIV}\n\n"
        f"{body}\n\n"
        "<i>✧ Forward an example story post to add a new format template.</i>"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✚ Add Format",   callback_data=f"cfg|fmt_learn||{caller_id}"),
            InlineKeyboardButton("◇ View",          callback_data=f"cfg|fmt_view||{caller_id}"),
        ],
        [
            InlineKeyboardButton("✖ Remove",        callback_data=f"cfg|fmt_rm||{caller_id}"),
            InlineKeyboardButton("⬡ Test",          callback_data=f"cfg|fmt_test||{caller_id}"),
        ],
        _cfg_nav(caller_id, back="main"),
    ])
    return text, markup


def _cfg_fmt_learn_panel(caller_id: int) -> tuple:
    """Prompt admin to forward a sample post, then pick channel from buttons."""
    # Gather all known source channels
    all_sources = []
    from config import CHANNEL_ID as _PRIMARY_CH
    if _PRIMARY_CH:
        all_sources.append(str(_PRIMARY_CH))
    for cid in bot_config.get("sources", []):
        cid_s = str(cid)
        if cid_s not in all_sources:
            all_sources.append(cid_s)

    if all_sources:
        ch_lines = "\n".join(f"• <code>{c}</code>" for c in all_sources)
        body = (
            "<b>◆ Step 1:</b> Forward one example story post from your channel.\n"
            "<b>◆ Step 2:</b> Then tap your channel below.\n\n"
            f"<b>Your source channels:</b>\n{ch_lines}"
        )
        # Build channel picker buttons
        ch_rows = [
            [InlineKeyboardButton(f"◆ {c}", callback_data=f"cfg|fmt_pick_ch|{c}|{caller_id}")]
            for c in all_sources
        ]
    else:
        body = (
            "<b>◆ No source channels configured yet.</b>\n\n"
            "✧ First add a source channel via <b>Sources</b> in /config,\n"
            "then come back here to learn a format."
        )
        ch_rows = []

    text = (
        "<b>★ Add Format — Forward a Sample Post</b>\n"
        f"{_CFG_DIV}\n\n"
        f"{body}\n\n"
        "<i>✧ No regex required — just forward a real story post!</i>"
    )
    rows = ch_rows + [_cfg_nav(caller_id, back="formats")]
    markup = InlineKeyboardMarkup(rows)
    return text, markup


def _cfg_fmt_view_panel(caller_id: int) -> tuple:
    """Show all stored templates with their details."""
    fmts = learned_formats_db
    if not fmts:
        text = (
            "<b>★ View Formats</b>\n"
            "<i>No formats learned yet.</i>\n"
            f"{_CFG_DIV}\n\n"
            "<i>☆ Add a format template first.</i>"
        )
        markup = InlineKeyboardMarkup([_cfg_nav(caller_id, back="formats")])
        return text, markup

    parts = []
    for cid, templates in fmts.items():
        if not isinstance(templates, list):
            continue
        for i, tmpl in enumerate(templates):
            label = tmpl.get("label", f"ch{cid}_{i}")
            has_title = "✦" if tmpl.get("title_pattern") else "✧"
            has_link  = "✦" if tmpl.get("link_pattern")  else "✧"
            has_media = "✦" if tmpl.get("has_media")      else "✧"
            parts.append(
                f"◆ <b>Channel</b> <code>{cid}</code> — <code>{label}</code>\n"
                f"  {has_title} Title  {has_link} Link  {has_media} Media"
            )
    body = "\n\n".join(parts) if parts else "<i>☆ Empty.</i>"
    text = (
        "<b>★ Learned Formats</b>\n"
        "<i>All stored channel format templates.</i>\n"
        f"{_CFG_DIV}\n\n"
        f"{body}"
    )
    markup = InlineKeyboardMarkup([_cfg_nav(caller_id, back="formats")])
    return text, markup


def _cfg_fmt_rm_panel(caller_id: int) -> tuple:
    """List buttons to remove a template."""
    fmts = learned_formats_db
    if not fmts:
        text = (
            "<b>★ Remove Format</b>\n"
            "<i>No formats to remove.</i>\n"
            f"{_CFG_DIV}"
        )
        markup = InlineKeyboardMarkup([_cfg_nav(caller_id, back="formats")])
        return text, markup

    text = (
        "<b>★ Remove Format</b>\n"
        "<i>Tap a channel to remove all its templates.</i>\n"
        f"{_CFG_DIV}"
    )
    rows = [
        [InlineKeyboardButton(f"✖ Channel {cid}", callback_data=f"cfg|fmt_do_rm|{cid}|{caller_id}")]
        for cid in fmts
    ]
    rows.append(_cfg_nav(caller_id, back="formats"))
    markup = InlineKeyboardMarkup(rows)
    return text, markup


def _cfg_fmt_test_panel(caller_id: int) -> tuple:
    """Prompt admin to forward a message for format testing."""
    text = (
        "<b>★ Test Format</b>\n"
        "<i>Check if a message matches a stored template.</i>\n"
        f"{_CFG_DIV}\n\n"
        "<b>◆ Instructions:</b>\n"
        "• <b>Forward</b> any message from a source channel.\n"
        "• The bot will show extracted fields without saving.\n\n"
        "<i>✧ This helps verify the format works before scanning.</i>"
    )
    markup = InlineKeyboardMarkup([_cfg_nav(caller_id, back="formats")])
    return text, markup




def _cfg_sysinfo_panel(caller_id: int) -> tuple:
    import datetime as _dt
    uptime_s = int(time.time() - BOT_START_TS)
    hours, rem = divmod(uptime_s, 3600)
    mins, secs = divmod(rem, 60)
    uptime_str = f"{hours}h {mins}m {secs}s"
    story_count = len(story_index)
    sources = len(bot_config.get("sources", []))
    req_count = len(request_db)
    maint_status = "🔴 ON" if MAINTENANCE_MODE else "🟢 OFF"
    text = (
        "<b>✺ System Information</b>\n"
        "<i>Current runtime statistics.</i>\n"
        f"{_CFG_DIV}\n\n"
        f"▪ <b>Uptime</b>  ➔  <code>{uptime_str}</code>\n"
        f"▪ <b>Stories</b>  ➔  <code>{story_count}</code>\n"
        f"▪ <b>Sources</b>  ➔  <code>{sources}</code>\n"
        f"▫ <b>Requests</b>  ➔  <code>{req_count}</code>\n"
        f"▫ <b>Maintenance</b>  ➔  <code>{maint_status}</code>"
    )
    markup = InlineKeyboardMarkup([_cfg_nav(caller_id, back="main")])
    return text, markup


def _cfg_maintenance_panel(caller_id: int) -> tuple:
    """Build the maintenance mode config panel."""
    if MAINTENANCE_MODE:
        if MAINTENANCE_UNTIL > 0:
            rem = max(0, int(MAINTENANCE_UNTIL - time.time()))
            h, r = divmod(rem, 3600)
            m, _ = divmod(r, 60)
            eta = f"{h}h {m}m remaining" if h > 0 else (f"{m}m remaining" if m > 0 else "ending soon")
        else:
            eta = "indefinite"
        status_line = f"🔴 <b>ACTIVE</b>  —  <code>{eta}</code>"
        action_note = "Tap <b>🟢 Disable</b> to turn off maintenance."
    else:
        status_line = "🟢 <b>INACTIVE</b>"
        action_note = "Tap a duration below to <b>enable</b> maintenance for that period."

    text = (
        "<b>🔧 Maintenance Mode</b>\n"
        "<i>Control bot-wide maintenance and downtime.</i>\n"
        f"{_CFG_DIV}\n\n"
        f"▪ Status: {status_line}\n\n"
        "▫ <i>When enabled, only admins can use the bot.\n"
        "Users receive a polished maintenance notice.</i>\n\n"
        f"<b>{action_note}</b>"
    )

    if MAINTENANCE_MODE:
        # Only show Disable button
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟢 Disable Maintenance", callback_data=f"cfg|maint_off||{caller_id}"),
            ],
            _cfg_nav(caller_id, back="main"),
        ])
    else:
        # Show duration buttons — each tap immediately enables maintenance
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("30 min",   callback_data=f"cfg|maint_on|30|{caller_id}"),
                InlineKeyboardButton("1 hour",   callback_data=f"cfg|maint_on|60|{caller_id}"),
                InlineKeyboardButton("3 hours",  callback_data=f"cfg|maint_on|180|{caller_id}"),
            ],
            [
                InlineKeyboardButton("6 hours",  callback_data=f"cfg|maint_on|360|{caller_id}"),
                InlineKeyboardButton("12 hours", callback_data=f"cfg|maint_on|720|{caller_id}"),
                InlineKeyboardButton("24 hours", callback_data=f"cfg|maint_on|1440|{caller_id}"),
            ],
            [
                InlineKeyboardButton("∞ Indefinite", callback_data=f"cfg|maint_on|0|{caller_id}"),
            ],
            _cfg_nav(caller_id, back="main"),
        ])
    return text, markup


async def _edit_cfg(query, text: str, markup, loading: bool = False):
    """Edit the config message, optionally show a loading state first."""
    if loading:
        try:
            await query.message.edit_text(
                "<i>⟳ Updating menu…</i>", parse_mode="HTML"
            )
        except Exception:
            pass
    try:
        await query.message.edit_text(text=text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass


async def _send_config_panel(message, context, lang, edit=False, caller_id: int = 0):
    """Send or edit the main config panel."""
    text, markup = _cfg_main_panel(caller_id, lang)
    if edit:
        try:
            await message.edit_text(text=text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
    else:
        await message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)


async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the premium inline config panel."""
    if not is_admin(update.effective_user.id):
        lang = get_chat_lang(update.effective_chat.id)
        await update.message.reply_text("⛔ Admin only." if lang != "hi" else "⛔ केवल एडमिन।")
        return
    caller_id = update.effective_user.id
    lang = get_chat_lang(update.effective_chat.id)
    text, markup = _cfg_main_panel(caller_id, lang)
    sent = await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)
    # Auto-delete command message
    cmd_msg = update.message
    async def _del_cmd():
        await asyncio.sleep(5)
        try: await cmd_msg.delete()
        except: pass
    asyncio.create_task(_del_cmd())


async def _handle_config_callback(query, context: ContextTypes.DEFAULT_TYPE):
    """Handle all cfg| callbacks — always edits the same message."""
    global MAINTENANCE_MODE, MAINTENANCE_UNTIL
    data = query.data
    parts = data.split("|")
    # Format: cfg|section|action|caller_id
    section  = parts[1] if len(parts) > 1 else "main"
    action   = parts[2] if len(parts) > 2 else ""
    try:
        caller_id = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        caller_id = 0
    user     = query.from_user
    chat_id  = query.message.chat.id
    lang     = get_chat_lang(chat_id)

    # ── Close / delete ──────────────────────────────────────────────────────
    if section == "close":
        if user.id == caller_id or is_admin(user.id):
            try: await query.message.delete()
            except: await query.answer("✧ Could not delete.", show_alert=True)
        else:
            await query.answer("⛔ Only the admin who opened this menu can close it.", show_alert=True)
        return

    # ── Permission guard ────────────────────────────────────────────────────
    if not is_admin(user.id):
        await query.answer("⛔ Admin only.", show_alert=True)
        return

    # ── Main panel ──────────────────────────────────────────────────────────
    if section == "main":
        text, markup = _cfg_main_panel(caller_id, lang)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    # ── Language ────────────────────────────────────────────────────────────
    if section == "lang":
        text, markup = _cfg_lang_panel(caller_id, chat_id, lang)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "setlang":
        new_lang = action
        set_chat_lang(chat_id, new_lang)
        lang = new_lang
        label = "English" if new_lang == "en" else "हिन्दी"
        await query.answer(f"★ Language set to {label}", show_alert=False)
        text, markup = _cfg_lang_panel(caller_id, chat_id, lang)
        await _edit_cfg(query, text, markup)
        return

    # ── Timers ───────────────────────────────────────────────────────────────
    if section == "timers":
        text, markup = _cfg_timers_panel(caller_id, lang)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    # ── Source channels ──────────────────────────────────────────────────────
    if section == "sources":
        text, markup = _cfg_sources_panel(caller_id, lang)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "add_source":
        text, markup = _cfg_add_source_panel(caller_id)
        context.chat_data["config_state"] = "waiting_source_id"
        context.chat_data["config_caller_id"] = caller_id
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "rm_source":
        text, markup = _cfg_rm_source_panel(caller_id, lang)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "do_rm_src":
        raw = action
        try:
            cid = int(raw) if raw.lstrip("-").isdigit() else raw
        except Exception:
            cid = raw
        sources = bot_config.get("sources", [])
        if cid in sources:
            sources.remove(cid)
            bot_config["sources"] = sources
            save_config(bot_config)
            await query.answer("✦ Channel removed.", show_alert=False)
        else:
            await query.answer("✧ Channel not found.", show_alert=True)
        text, markup = _cfg_sources_panel(caller_id, lang)
        await _edit_cfg(query, text, markup, loading=True)
        return

    # ── Learned Format System ─────────────────────────────────────────────────
    if section == "formats":
        text, markup = _cfg_formats_panel(caller_id, lang)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "fmt_learn":
        # Show instructions + set state to wait for a forwarded sample + channel ID
        text, markup = _cfg_fmt_learn_panel(caller_id)
        context.chat_data["config_state"] = "waiting_fmt_sample"
        context.chat_data["config_caller_id"] = caller_id
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "fmt_view":
        text, markup = _cfg_fmt_view_panel(caller_id)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "fmt_rm":
        text, markup = _cfg_fmt_rm_panel(caller_id)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "fmt_do_rm":
        cid_str = action
        if cid_str in learned_formats_db:
            learned_formats_db.pop(cid_str, None)
            save_learned_formats(learned_formats_db)
            await query.answer("✦ Templates removed.", show_alert=False)
        else:
            await query.answer("✧ Channel not found.", show_alert=True)
        text, markup = _cfg_fmt_rm_panel(caller_id)
        await _edit_cfg(query, text, markup, loading=True)
        return

    if section == "fmt_test":
        # Show instructions + set state to wait for a forwarded message
        text, markup = _cfg_fmt_test_panel(caller_id)
        context.chat_data["config_state"] = "waiting_fmt_test"
        context.chat_data["config_caller_id"] = caller_id
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    if section == "fmt_pick_ch":
        # Admin tapped a channel button — apply sample text to that channel
        cid_str = action
        sample_text = context.chat_data.get("fmt_sample_text", "")
        has_media   = context.chat_data.get("fmt_sample_has_media", False)

        if not sample_text:
            await query.answer("☆ No sample found. Please forward a post first.", show_alert=True)
            return

        # Build fake message wrapper for learn_format
        button_url = context.chat_data.get("fmt_sample_button_url")
        class _FakeBtn:
            def __init__(self, u): self.url = u
        class _FakeRow:
            def __init__(self, u): self.inline_keyboard = [[_FakeBtn(u)]] if u else []
        class _FakeMsg:
            def __init__(self, t, m, bu):
                self.message = t
                self.text    = t
                self.caption = t
                self.photo   = m
                self.id      = 0
                self.reply_markup = _FakeRow(bu)

        try:
            ch_id_int = int(cid_str)
        except ValueError:
            ch_id_int = 0

        tmpl = learn_format(_FakeMsg(sample_text, has_media, button_url), ch_id_int)

        # Normalise key: always store as the exact string that matches the source channel
        key = cid_str if cid_str else str(ch_id_int)
        if key not in learned_formats_db:
            learned_formats_db[key] = []
        learned_formats_db[key].append(tmpl)
        save_learned_formats(learned_formats_db)

        # Clear state
        context.chat_data.pop("config_state", None)
        context.chat_data.pop("fmt_sample_text", None)
        context.chat_data.pop("fmt_sample_has_media", None)
        context.chat_data.pop("fmt_sample_button_url", None)

        preview = build_preview(tmpl, sample_text)
        await query.answer("✦ Format learned!", show_alert=False)
        # Show result by editing the config message
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("◆ View Formats", callback_data=f"cfg|fmt_view||{caller_id}")],
            _cfg_nav(caller_id, back="formats"),
        ])
        await _edit_cfg(
            query,
            f"<b>★ Format Learned!</b>\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"✦ Template saved for channel <code>{key}</code>.\n\n"
            f"{preview}",
            markup
        )
        return

    if section == "fmt_learn":
        # Re-entry: show the Add Format panel (sets state)
        text, markup = _cfg_fmt_learn_panel(caller_id)
        context.chat_data["config_state"] = "waiting_fmt_sample"
        context.chat_data["config_caller_id"] = caller_id
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    # ── System info ──────────────────────────────────────────────────────────

    if section == "sysinfo":
        text, markup = _cfg_sysinfo_panel(caller_id)
        await _edit_cfg(query, text, markup, loading=True)
        await query.answer()
        return

    # ── Maintenance mode ─────────────────────────────────────────────────────
    if section == "maintenance":
        text, markup = _cfg_maintenance_panel(caller_id)
        await _edit_cfg(query, text, markup)
        await query.answer()
        return

    # maint_on: duration tap immediately activates maintenance
    if section == "maint_on":
        try:
            mins_val = int(action)
        except (ValueError, TypeError):
            mins_val = 60
        MAINTENANCE_MODE  = True
        MAINTENANCE_UNTIL = (time.time() + mins_val * 60) if mins_val > 0 else 0.0
        bot_config["maintenance"] = {"enabled": True, "until": MAINTENANCE_UNTIL}
        save_config(bot_config)
        dur_label = f"{mins_val} min(s)" if mins_val > 0 else "indefinitely"
        await query.answer(f"🔴 Maintenance ON — {dur_label}.", show_alert=True)
        # Schedule auto-expiry if duration is finite
        if mins_val > 0:
            async def _auto_off():
                await asyncio.sleep(mins_val * 60)
                if MAINTENANCE_MODE:
                    _end_maintenance()
            asyncio.create_task(_auto_off())
        text, markup = _cfg_maintenance_panel(caller_id)
        await _edit_cfg(query, text, markup, loading=True)
        return

    # maint_off: immediately disables maintenance
    if section == "maint_off":
        _end_maintenance()
        await query.answer("🟢 Maintenance mode disabled.", show_alert=True)
        text, markup = _cfg_maintenance_panel(caller_id)
        await _edit_cfg(query, text, markup, loading=True)
        return

    # Legacy handler names kept for safety
    if section == "maint_dur":
        # Old flow unused but keep for graceful handling
        await query.answer("✧ Please use the new duration buttons above.", show_alert=False)
        text, markup = _cfg_maintenance_panel(caller_id)
        await _edit_cfg(query, text, markup)
        return

    if section == "maint_toggle":
        if MAINTENANCE_MODE:
            _end_maintenance()
            await query.answer("🟢 Maintenance mode disabled.", show_alert=True)
        else:
            MAINTENANCE_MODE  = True
            MAINTENANCE_UNTIL = 0.0
            bot_config["maintenance"] = {"enabled": True, "until": 0}
            save_config(bot_config)
            await query.answer("🔴 Maintenance mode ON (indefinite).", show_alert=True)
        text, markup = _cfg_maintenance_panel(caller_id)
        await _edit_cfg(query, text, markup, loading=True)
        return

    # ── Legacy cfg_main|* compat ──────────────────────────────────────────────
    await query.answer()



async def handle_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for config operations (channel IDs, custom formats)."""
    if not is_admin(update.effective_user.id):
        return
    
    chat_id = update.effective_chat.id
    lang = get_chat_lang(chat_id)
    text = update.message.text.strip()
    
    # Check if we're waiting for config input
    state = context.chat_data.get("config_state")
    
    if state == "waiting_source_id":
        try:
            channel_id = int(text)
            sources = bot_config.get("sources", [])
            if channel_id not in sources:
                sources.append(channel_id)
                bot_config["sources"] = sources
                save_config(bot_config)
                
                if lang == "hi":
                    response = f"<b>✅ सोर्स चैनल जोड़ा गया</b>\n\n<code>{channel_id}</code> को सफलतापूर्वक जोड़ा गया है।"
                else:
                    response = f"<b>✅ Source Channel Added</b>\n\n<code>{channel_id}</code> has been successfully added."
            else:
                if lang == "hi":
                    response = f"<b>⚠️ पहले से मौजूद</b>\n\n<code>{channel_id}</code> पहले से ही सोर्स चैनल में है।"
                else:
                    response = f"<b>⚠️ Already Exists</b>\n\n<code>{channel_id}</code> is already in source channels."
        except ValueError:
            if lang == "hi":
                response = "<b>❌ अमान्य चैनल ID</b>\n\nकृपया एक वैध चैनल ID भेजें।"
            else:
                response = "<b>❌ Invalid Channel ID</b>\n\nPlease send a valid channel ID."
        
        # Clear state
        context.chat_data.pop("config_state", None)
        await update.message.reply_text(response, parse_mode="HTML")
        return
    
    # ── Step 1a: Admin forwards a sample story post ──────────────────────────
    if state == "waiting_fmt_sample":
        msg = update.message
        sample_text = (msg.text or msg.caption or "").strip()

        if not sample_text:
            await msg.reply_text(
                "<b>☆ Empty message.</b>\n\n"
                "Please forward a post that has text or a caption.",
                parse_mode="HTML"
            )
            return

        # Store sample in chat_data and ask admin to pick a channel
        has_media = bool(msg.photo)
        button_url = None
        if getattr(msg, "reply_markup", None) and hasattr(msg.reply_markup, "inline_keyboard"):
            for row in msg.reply_markup.inline_keyboard:
                for btn in row:
                    url = getattr(btn, "url", None)
                    if url and "t.me/" in url:
                        button_url = url
                        break
                if button_url: break

        context.chat_data["fmt_sample_text"]       = sample_text
        context.chat_data["fmt_sample_has_media"]  = has_media
        context.chat_data["fmt_sample_button_url"] = button_url
        context.chat_data["config_state"]          = "waiting_fmt_channel_id"

        # Build inline channel-picker buttons
        from config import CHANNEL_ID as _PCH
        all_sources = []
        if _PCH:
            all_sources.append(str(_PCH))
        for cid in bot_config.get("sources", []):
            cid_s = str(cid)
            if cid_s not in all_sources:
                all_sources.append(cid_s)

        caller_id = context.chat_data.get("config_caller_id", 0)
        if all_sources:
            ch_rows = [
                [InlineKeyboardButton(f"◆ {c}", callback_data=f"cfg|fmt_pick_ch|{c}|{caller_id}")]
                for c in all_sources
            ]
            channel_note = "Tap your channel below:"
        else:
            ch_rows = []
            channel_note = (
                "No source channels configured yet.\n"
                "Add one in Sources first, then try again."
            )

        markup = InlineKeyboardMarkup(ch_rows + [[InlineKeyboardButton("✖ Cancel", callback_data=f"cfg|formats||{caller_id}")]])
        await msg.reply_text(
            "<b>★ Add Format — Step 2</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "✦ Sample post received!\n\n"
            f"<b>◆ {channel_note}</b>",
            parse_mode="HTML",
            reply_markup=markup
        )
        return

    # ── waiting_fmt_channel_id: now handled via callback (fmt_pick_ch), not text ──
    if state == "waiting_fmt_channel_id":
        # If the admin typed a channel ID (fallback for no-button situations)
        msg = update.message
        raw = (msg.text or "").strip()
        if not raw.lstrip("-").isdigit():
            await msg.reply_text("❌ Please tap one of the channel buttons, or enter a valid numeric channel ID.", parse_mode="HTML")
            return
        sample_text = context.chat_data.get("fmt_sample_text", "")
        has_media   = context.chat_data.get("fmt_sample_has_media", False)
        if not sample_text:
            await msg.reply_text("❌ Sample was lost. Please restart the Add Format flow.", parse_mode="HTML")
            context.chat_data.pop("config_state", None)
            return
        try:
            ch_id_int = int((msg.text or "").strip())
        except ValueError:
            await msg.reply_text("❌ Invalid channel ID. Send a numeric ID like <code>-1001234567890</code>.", parse_mode="HTML")
            return

        class _FakeBtn:
            def __init__(self, u): self.url = u
        class _FakeRow:
            def __init__(self, u): self.inline_keyboard = [[_FakeBtn(u)]] if u else []
        class _FakeMsg:
            def __init__(self, t, m, bu):
                self.message = t
                self.text = t
                self.caption = t
                self.photo = m
                self.id = 0
                self.reply_markup = _FakeRow(bu)

        button_url = context.chat_data.get("fmt_sample_button_url")
        tmpl = learn_format(_FakeMsg(sample_text, has_media, button_url), ch_id_int)
        cid_str = str(ch_id_int)
        if cid_str not in learned_formats_db:
            learned_formats_db[cid_str] = []
        learned_formats_db[cid_str].append(tmpl)
        save_learned_formats(learned_formats_db)

        preview = build_preview(tmpl, sample_text)
        context.chat_data.pop("config_state", None)
        context.chat_data.pop("fmt_sample_text", None)
        context.chat_data.pop("fmt_sample_has_media", None)
        context.chat_data.pop("fmt_sample_button_url", None)
        await msg.reply_text(
            f"<b>★ Format Learned!</b>\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"✦ Template saved for channel <code>{ch_id_int}</code>.\n\n"
            f"{preview}",
            parse_mode="HTML"
        )
        return

    # ── Test: Admin forwards a message to check against stored templates ──────
    if state == "waiting_fmt_test":
        msg = update.message
        test_text = msg.text or msg.caption or ""
        if not test_text:
            await msg.reply_text("❌ Could not read the message text. Please forward a text/caption message.", parse_mode="HTML")
            context.chat_data.pop("config_state", None)
            return

        # Try each stored template
        matched_result = None
        matched_label = None
        for cid_str, templates in learned_formats_db.items():
            if not isinstance(templates, list):
                continue
            for tmpl in templates:
                result = build_test_result(msg, tmpl)
                if "★ Test Result: Matched" in result:
                    matched_result = result
                    matched_label = tmpl.get("label", cid_str)
                    break
            if matched_result:
                break

        if matched_result:
            out = f"{matched_result}\n\n✦ <b>Matched template:</b> <code>{matched_label}</code>"
        else:
            out = (
                "<b>☆ Test Result: No Match</b>\n"
                "━━━━━━━━━━━━━━━━\n\n"
                "✧ <i>This message does not match any stored template.</i>\n"
                "✧ <i>It would be ignored during scan.</i>"
            )

        context.chat_data.pop("config_state", None)
        await msg.reply_text(out, parse_mode="HTML")
        return




# -----------------------
# start bot
# -----------------------

async def trending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trending = stats_db.get("trending", {})
    now = time.time()
    week_ago = now - (7 * 24 * 3600)
    
    trending_counts = {}
    for k, v in list(trending.items()):
        if isinstance(v, list):
            valid_times = [t for t in v if t >= week_ago]
            if valid_times:
                trending[k] = valid_times
                trending_counts[k] = len(valid_times)
            else:
                del trending[k]
        else:
            trending[k] = [now]
            trending_counts[k] = 1

    sorted_trend = sorted(trending_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    
    if not sorted_trend:
        await update.message.reply_text("No trending stories yet.")
        return
    text = "🔥 *Trending Stories (This Week)*\n\n"
    for i, (k, v) in enumerate(sorted_trend, 1):
        text += f"{i}. `{k}` — _{v} searches_\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def saved_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id_str = str(update.effective_user.id)
    favs = favorites_db.get(user_id_str, [])
    if not favs:
        await update.message.reply_text("You have no saved stories. ⭐ Use 'Save to Favorites' when you search stories.")
        return
    text = "⭐ *Your Saved Stories*\n\n"
    for story_key in favs:
        res = get_story(story_key)
        if res:
            text += f"• [{story_key}]({res['link']})\n"
        else:
            text += f"• `{story_key}` (link unavailable)\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def browse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    types = set()
    for s in db.values():
        val = s.get("story_type")
        if val: types.add(val)
    if not types:
        await update.message.reply_text("No categories available yet.")
        return
    types = list(types)[:10]
    keyboard = []
    for t in types:
        keyboard.append([InlineKeyboardButton(t.capitalize(), switch_inline_query_current_chat=t)])
    await update.message.reply_text("📑 *Browse by Category*\n\nSelect a category to search:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    all_stories = list(db.values())
    all_stories.sort(key=lambda s: s.get("message_id", 0), reverse=True)
    stories = all_stories[:10]
    if not stories:
        await update.message.reply_text("No new stories.")
        return
    text = "🆕 *Recently Added Stories*\n\n"
    for s in stories:
        name = s.get("name") or s.get("text") or "Story"
        text += f"• [{clean_story(name)}]({s.get('link')})\n"
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def myrequests_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    reqs = []
    for name, req in request_db.items():
        if str(req.get("user_id")) == user_id:
            reqs.append(req)
    if not reqs:
        await update.message.reply_text("You have no pending requests. Use /request <name>")
        return
    text = "📬 *Your Pending Requests*\n\n"
    for req in reqs:
        text += f"• `{req['name']}` (Requested on {req.get('timestamp','Unknown')[:10]})\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def requests_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not request_db:
        await update.message.reply_text("No pending requests.")
        return
    text = "📋 *Pending Requests*\n\n"
    for name, req in list(request_db.items())[:20]:
        text += f"• `{name}` ({req.get('count', 1)} requests)\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def userinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args: return await update.message.reply_text("Usage: /userinfo <user_id>")
    target = context.args[0]
    favs = len(favorites_db.get(target, []))
    searches = stats_db.get("users", {}).get(target, 0)
    reqs = [r for r in request_db.values() if str(r.get("user_id")) == target]
    text = f"🛡️ *User Info for {target}*\n\nSearches: `{searches}`\nSaved: `{favs}`\nPending Requests: `{len(reqs)}`"
    await update.message.reply_text(text, parse_mode="Markdown")

async def settimer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /settimer <search|commands> <seconds>")
    key = context.args[0]
    try: val = int(context.args[1])
    except: return await update.message.reply_text("Seconds must be an integer.")
    bot_config.setdefault("auto_delete", {})
    bot_config["auto_delete"][key] = val
    save_config(bot_config)
    await update.message.reply_text(f"✅ Timer set for '{key}' to {val} seconds.")

async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id_str = str(update.effective_user.id)
    if user_id_str in subs_db:
        subs_db.remove(user_id_str)
        save_subs(subs_db)
        await update.message.reply_text("🔕 You have unregistered from new story notifications.")
    else:
        subs_db.append(user_id_str)
        save_subs(subs_db)
        await update.message.reply_text("🔔 You are now subscribed to new story notifications!")

async def rescan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 1:
        return await update.message.reply_text(
            "Usage: /rescan <channel\_id|username>\n"
            "Example: /rescan -1001234567890",
            parse_mode="Markdown"
        )

    target_channel = context.args[0]
    # Convert to int if it's a numeric channel ID
    if target_channel.lstrip("-").isdigit():
        target_channel = int(target_channel)

    global IS_SCANNING
    if IS_SCANNING:
        return await update.message.reply_text("⏳ A scan is already in progress.")

    IS_SCANNING = True

    # ---- live-UI state (mirrors /scan) ----
    rescan_start_ts = time.time()
    last_ui_update = 0.0
    last_story_name = ""
    last_found = 0
    expected_total = last_scan_count or 0  # best estimate from previous scan

    # Display channel name safely (no backtick, no underscore in Markdown)
    safe_ch = re.sub(r'[*_`\[\]()~]', '', str(target_channel))

    msg = await update.message.reply_text(
        text=(
            "🔎 *Riya Rescan*\n\n"
            f"*Channel:* `{safe_ch}`\n"
            "*Status:* _Initializing scanner..._\n\n"
            "*Progress:* ░░░░░░░░░░ 0%"
        ),
        parse_mode="Markdown"
    )

    await asyncio.sleep(1)
    try:
        await msg.edit_text(
            text=(
                "🔎 *Riya Rescan*\n\n"
                f"*Channel:* `{safe_ch}`\n"
                "*Status:* _Fetching channel messages..._\n\n"
                "*Progress:* ▓▓░░░░░░░░ 20%"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass

    await asyncio.sleep(1)
    try:
        await msg.edit_text(
            text=(
                "🔎 *Riya Rescan*\n\n"
                f"*Channel:* `{safe_ch}`\n"
                "*Status:* _Detecting stories..._\n\n"
                "*Progress:* ▓▓▓▓░░░░░░ 40%"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass

    await asyncio.sleep(1)

    try:
        formats = bot_config.get("formats", {})

        async def _progress_cb(p):
            nonlocal last_ui_update, last_story_name, last_found
            now = time.time()
            last_story_name = (p.get("last_story") or "").strip()
            last_found = int(p.get("stories_found") or 0)

            elapsed = max(now - rescan_start_ts, 1)
            rate = last_found / elapsed
            remaining = max(expected_total - last_found, 0) if expected_total else 0
            eta_s = int(remaining / rate) if (expected_total and rate > 0) else 0

            # Update GLOBAL state
            SCAN_PROGRESS["stories_found"] = last_found
            SCAN_PROGRESS["total_messages"] = p.get("total_messages", 0)
            SCAN_PROGRESS["eta_s"] = eta_s
            SCAN_PROGRESS["last_story"] = last_story_name

            # throttle: update at most once every 2.5 s
            if now - last_ui_update < 2.5:
                return
            last_ui_update = now

            eta_text = f"`~{eta_s//60:02d}:{eta_s%60:02d}`" if eta_s else "`--:--`"

            # strip markdown chars from story name
            safe_story = re.sub(r'[*_`\[\]()~]', '', last_story_name)
            safe_story = (safe_story[:60] + "…") if len(safe_story) > 60 else safe_story

            try:
                await msg.edit_text(
                    text=(
                        "🔎 *Riya Rescan*\n\n"
                        f"*Channel:* `{safe_ch}`\n"
                        "*Status:* _Scanning & adding stories..._\n\n"
                        f"*Last Added:* _{safe_story}_\n"
                        f"*Stories Found:* *{last_found}*\n"
                        f"*Estimated Remaining:* {eta_text}\n\n"
                        "_Please wait until the rescan is complete._"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as parse_err:
                logger.warning(f"Rescan UI update failed: {parse_err}")

        result = await scan_channel(
            channel_id=target_channel,
            bot=context.bot,
            log_channel=LOG_CHANNEL,
            progress_cb=_progress_cb,
            cleanup=False,
            formats_by_channel=formats
        )
        count = result.get("stories", 0)
        try:
            await msg.edit_text(
                text=(
                    "✅ *Rescan Completed*\n\n"
                    f"*Channel:* `{safe_ch}`\n"
                    f"*Stories Indexed:* *{count}*\n\n"
                    "_Your story database has been updated._"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

    except Exception as e:
        safe_err = re.sub(r'[*_`\[\]()~]', '', str(e))
        try:
            await msg.edit_text(
                text=f"❌ *Rescan failed*\n\n`{safe_ch}`\n\n{safe_err}",
                parse_mode="Markdown"
            )
        except Exception:
            await msg.edit_text(text=f"❌ Rescan failed: {e}")
    finally:
        IS_SCANNING = False



async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the inline menu panel directly (no welcome text)."""
    if await _enforce_cooldown(update, context):
        return
    u = update.effective_user
    lang = get_chat_lang(update.effective_chat.id)
    text, markup = _menu_main(u.id, lang)
    await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.chat_data and context.chat_data.get("config_state"):
        return await handle_config_input(update, context)
    return await search(update, context)


async def storylist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    msgs_text = []
    db = load_db()
    lines = []
    for i, name in enumerate(story_index, 1):
        db_story = db.get(name) or {}
        link = db_story.get("link", "")
        safe_name = html.escape(clean_story(name))
        if link:
            lines.append(f"{i}. <a href='{link}'><code>{safe_name}</code></a>")
        else:
            lines.append(f"{i}. <code>{safe_name}</code>")
            
    current_msg = "<b>📚 Complete Story Index</b>\n━━━━━━━━━━━━━━━━\n\n"
    for line in lines:
        if len(current_msg) + len(line) > 3500:
            msgs_text.append(current_msg)
            current_msg = ""
        current_msg += line + "\n"
    if current_msg:
        msgs_text.append(current_msg)
        
    if not msgs_text:
        msgs_text = ["<i>No stories indexed yet.</i>"]
        
    msg_ids = []
    for text in msgs_text:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to send list: {e}")

check_cooldowns = {}

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if await _enforce_cooldown(update, context):
        return
        
    if not is_admin(user.id):
        now = time.time()
        user_history = check_cooldowns.get(user.id, [])
        user_history = [ts for ts in user_history if now - ts < 60]
        if len(user_history) >= 5:
            await update.message.reply_text("⛔ You have reached the limit of 5 checks per minute. Please wait.")
            return
        user_history.append(now)
        check_cooldowns[user.id] = user_history
        
    query = ""
    if context.args:
        query = " ".join(context.args)
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        lines = update.message.reply_to_message.text.split('\n')
        if lines:
            query = lines[0].strip()
            
    if not query:
        lang = get_chat_lang(chat.id)
        if lang == "hi":
            await update.message.reply_text("कृपया /check <स्टोरी का नाम> का उपयोग करें या किसी संदेश का उत्तर दें।")
        else:
            await update.message.reply_text("Please provide a story name:\nUsage: /check <story name>\nOr reply to a message.")
        return
        
    query = clean_story(query)
    if len(query) < 2:
        await update.message.reply_text("Query too short.")
        return
        
    wait_msg = await update.message.reply_text("<i>⏳ Verifying externally...</i>", parse_mode="HTML")
    
    try:
        result = await verify_story_external(query)
        
        if result["status"] == "found":
            source = result.get("source", "")
            source_tag = " ✦ <i>found in bot database</i>" if "local" in source else " ✦ <i>found via platform search</i>"
            resp = (
                "<b>★ Story Verification</b>\n\n"
                f"✦ <b>Title:</b> {result['title']}\n"
                f"✧ <b>Platform:</b> {result['platform']}\n\n"
                f"➤ <b>Link:</b>\n{result['link']}\n\n"
                f"<i>◇ Status: Available{source_tag}</i>"
            )
        elif result["status"] == "not_found":
            resp = (
                "<b>☆ Story Not Found</b>\n\n"
                "✧ This story is not available on supported platforms\n\n"
                "➤ Use /request to request it"
            )
        else:
            err = result.get("message", "Unknown error")
            resp = (
                "<b>☆ Verification Failed</b>\n\n"
                f"✧ {err}\n"
                "➤ Try again later"
            )
            
        await wait_msg.edit_text(text=resp, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"External check error: {e}")
        await wait_msg.edit_text(text="<b>☆ Verification Failed</b>\n\n✧ Check command encountered an error.\n➤ Try again later", parse_mode="HTML")

def start_bot():

    global app

    init_search_index()

    async def _post_init(application):
        if str(AUTO_SCAN).lower() == "true" and CHANNEL_ID:
            asyncio.create_task(auto_scan_loop(application.bot))
        # Start background link checker
        asyncio.create_task(start_link_checker())
        # Start background voting poll timeout manager
        asyncio.create_task(poll_timeout_manager(application))

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(PollAnswerHandler(poll_answer_handler))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("stories", stories))
    # /storylist command removed — access via WTF button only
    app.add_handler(CommandHandler("request", request_story))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("how", how))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("config", config_cmd))

    # Premium feature commands
    app.add_handler(CommandHandler("trending", trending_cmd))
    app.add_handler(CommandHandler("saved", saved_cmd))
    app.add_handler(CommandHandler("browse", browse_cmd))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    
    # User / requests commands
    app.add_handler(CommandHandler("myrequests", myrequests_cmd))

    # Admin-only utility commands
    app.add_handler(CommandHandler("settimer", settimer_cmd))
    app.add_handler(CommandHandler("requests", requests_cmd))
    app.add_handler(CommandHandler("userinfo", userinfo_cmd))
    app.add_handler(CommandHandler("unwarn", unwarn_cmd))
    app.add_handler(CommandHandler("cleardata", cleardata_cmd))
    app.add_handler(CommandHandler("rescan", rescan_cmd))
    app.add_handler(CommandHandler("announce", announce_cmd))
    app.add_handler(CommandHandler("copyright_mute", copyright_mute_cmd))
    app.add_handler(CommandHandler("setlang", setlang_cmd))
    app.add_handler(CommandHandler("addsource", addsource_cmd))
    app.add_handler(CommandHandler("removesource", removesource_cmd))
    app.add_handler(CommandHandler("addalias", addalias_cmd))
    app.add_handler(CommandHandler("removealias", removealias_cmd))
    app.add_handler(CommandHandler("listalias", listalias_cmd))

    # react when bot is added/removed
    app.add_handler(ChatMemberHandler(chat_member_update))

    app.add_handler(InlineQueryHandler(inline_search))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))
    
    # PB Hook
    app.add_handler(post_builder_handler)

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    # ── Group Cleanup: auto-delete commands & @botusername messages ──────────
    # Commands in groups (any /command sent in the group chat)
    app.add_handler(
        MessageHandler(
            filters.COMMAND & filters.ChatType.GROUPS,
            group_cleanup_handler,
        ),
        group=10,   # lower priority than command handlers so they still fire first
    )
    # Text messages that @mention the bot in a group (catches "@botname ..." style messages)
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.GROUPS & filters.Entity("mention"),
            group_cleanup_handler,
        ),
        group=10,
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


async def addalias_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: add an alias to a story by replying to a wrong search message."""
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Admin only.")
        
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to the wrong search message with /addalias <Story Name>")
        
    if not context.args:
        return await update.message.reply_text("Usage: /addalias <Story Name>")
        
    # The alias is the WRONG name (what the admin replies to — the user's search message)
    # The story target is what the admin types after /addalias
    replied_text = (update.message.reply_to_message.text or update.message.reply_to_message.caption or "").strip()
    if not replied_text:
        return await update.message.reply_text("❌ Replied message has no text to use as alias.")

    # Strip any bot-formatting that might have wrapped the query (e.g. the bot echoes queries)
    # Keep only the first non-empty line as the alias search text
    alias_name = replied_text.splitlines()[0].strip()
    # Remove common prefixes like "Search:" or similar wrapping
    alias_name = re.sub(r'^[^a-zA-Z\u0900-\u097F0-9]+', '', alias_name).strip()
    if not alias_name:
        return await update.message.reply_text("❌ Could not extract alias text. Make sure you reply to the user's search message.")

    story_raw = " ".join(context.args).strip()
    story_cleaned = clean_story(story_raw).lower()

    db = load_db()
    if story_cleaned not in db:
        return await update.message.reply_text(f"❌ Story '{story_raw}' not found in database. Check the exact name.")

    story_data = db[story_cleaned]
    aliases = story_data.get("aliases", [])
    alias_clean = clean_story(alias_name).lower()
    if not any(clean_story(a).lower() == alias_clean for a in aliases):
        aliases.append(alias_name)
        story_data["aliases"] = aliases
        db[story_cleaned] = story_data
        save_db(db)
        await update.message.reply_text(
            f"✅ Alias added!\n"
            f"<b>Alias:</b> <code>{alias_name}</code>\n"
            f"<b>Story:</b> <i>{story_data.get('text', story_raw)}</i>",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(f"ℹ️ The alias <code>{alias_name}</code> already exists for this story.", parse_mode="HTML")

async def removealias_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: remove an alias."""
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Admin only.")
        
    if not context.args:
        return await update.message.reply_text("Usage: /removealias <Alias>")
        
    alias_to_remove = " ".join(context.args).strip()
    alias_cleaned = clean_story(alias_to_remove).lower()
    
    db = load_db()
    found = False
    for k, data in db.items():
        aliases = data.get("aliases", [])
        if any(clean_story(a).lower() == alias_cleaned for a in aliases):
            # remove it
            data["aliases"] = [a for a in aliases if clean_story(a).lower() != alias_cleaned]
            db[k] = data
            found = True
            
    if found:
        save_db(db)
        await update.message.reply_text(f"✅ Alias '{alias_to_remove}' removed globally.")
    else:
        await update.message.reply_text("Alias not found.")

async def listalias_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: list aliases for a story."""
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Admin only.")
        
    if not context.args:
        return await update.message.reply_text("Usage: /listalias <Story Name>")
        
    story_raw = " ".join(context.args).strip()
    story_cleaned = clean_story(story_raw).lower()
    
    db = load_db()
    if story_cleaned not in db:
        return await update.message.reply_text("Story not found.")
        
    aliases = db[story_cleaned].get("aliases", [])
    if aliases:
        await update.message.reply_text("\n".join(["Aliases:"] + [f"- {a}" for a in aliases]))
    else:
        await update.message.reply_text("No aliases found for this story.")


async def unwarn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to remove a user's cooldown."""
    if not is_admin(update.effective_user.id):
        return
        
    if not context.args:
        await update.message.reply_text("<b>Usage:</b> <code>/unwarn &lt;user_id&gt;</code>", parse_mode="HTML")
        return
        
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid User ID.")
        return
        
    _clear_cooldown(target_id)
    await update.message.reply_text(f"✅ Cooldown cleared for user <a href='tg://user?id={target_id}'>{target_id}</a>.", parse_mode="HTML")


async def cleardata_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: clear all requests, link reports, and voting loops."""
    global request_db, voting_queue, active_polls, link_flags, active_link_votes, spam_requests_count
    
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Admin only.")
        
    try:
        # Erase Telegram Channel Messages (if cached)
        deleted_reqs = 0
        deleted_copies = 0
        
        global _req_msg_cache
        if "_req_msg_cache" in globals() and REQUEST_GROUP:
            for req_msg_id in list(_req_msg_cache.values()):
                try:
                    await context.bot.delete_message(chat_id=REQUEST_GROUP, message_id=req_msg_id)
                    deleted_reqs += 1
                except Exception:
                    pass
            _req_msg_cache.clear()
            
        global _copy_msg_cache
        if "_copy_msg_cache" in globals() and COPYRIGHT_CHANNEL:
            for copy_msg_id in list(_copy_msg_cache.values()):
                try:
                    await context.bot.delete_message(chat_id=COPYRIGHT_CHANNEL, message_id=copy_msg_id)
                    deleted_copies += 1
                except Exception:
                    pass
            _copy_msg_cache.clear()

        # Clear request mapping DB
        request_db.clear()
        save_requests({"requests": request_db})
        
        # Clear voting queue
        voting_queue.clear()
        active_polls.clear()
        save_voting_db({"queue": voting_queue, "polls": active_polls})
        
        # Clear link flags (broken report votes)
        link_flags.clear()
        save_link_flags(link_flags)
        
        # Clear in-memory caches
        if "active_link_votes" in globals():
            active_link_votes.clear()
        if "spam_requests_count" in globals():
            spam_requests_count.clear()
            
        await update.message.reply_text(f"✅ All temporary queues cleared!\n\n- Story requests fully reset ({deleted_reqs} msgs deleted).\n- Voting queue & polls reset.\n- Link Not Working reports reset ({deleted_copies} msgs deleted).", parse_mode="HTML")
        await log(context, f"ADMIN CLEARDATA | user_id={update.effective_user.id}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error clearing data: {e}")

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
