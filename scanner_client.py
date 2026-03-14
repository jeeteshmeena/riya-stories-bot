import asyncio
import tempfile
import os
import re
import logging

from telethon import TelegramClient
from telethon.tl.types import PeerChannel
from config import API_ID, API_HASH, SESSION_STRING
from telethon.sessions import StringSession

from parser import parse_story, get_text
from database import add_story, remove_stories_not_in, load_learned_formats
from format_learner import extract_with_template

logger = logging.getLogger(__name__)


def _apply_learned_formats(channel_id, message, learned_formats_by_channel):
    """
    Try every learned template stored for *channel_id*.
    Returns a story dict or None.
    """
    if not learned_formats_by_channel:
        return None

    templates = (
        learned_formats_by_channel.get(str(channel_id))
        or learned_formats_by_channel.get(str(abs(int(channel_id))))
        or []
    )
    if not templates:
        return None

    text = get_text(message)
    if not text:
        return None

    for tmpl in templates:
        result = extract_with_template(text, tmpl)
        if result:
            result["message_id"] = message.id
            result["caption"]    = text
            result["story_type"] = result.pop("status", None)
            result["source_channel"] = str(channel_id)
            if not result.get("name"):
                continue
            return result

    return None


async def _resolve_entity(client, channel_id):
    """
    Robustly resolve a channel entity for Telethon StringSession.

    StringSession has NO persistent entity cache (unlike SqliteSession).
    So client.get_entity(int_id) will almost always fail with ValueError
    because there is no cached mapping of int -> access_hash.

    The solution:
    1. First try get_entity() directly (works if entity is in session cache from get_dialogs).
    2. If that fails, call get_dialogs() to populate the in-memory cache with all
       channels/groups the user account is a member of, then retry.
    3. If that also fails (e.g. user is admin via bot but not a member with the session account),
       try constructing PeerChannel directly (only works for channels where no access_hash is needed).
    4. As a last resort for string usernames like @channelname, try direct resolution.
    """
    # If it's a string (username), resolve directly
    if isinstance(channel_id, str):
        if channel_id.startswith("@") or not channel_id.lstrip("-").isdigit():
            try:
                return await client.get_entity(channel_id)
            except Exception as e:
                logger.error(f"Cannot resolve username '{channel_id}': {e}")
                raise
        else:
            # It's a numeric string like "-1001234567890"
            channel_id = int(channel_id)

    # Now channel_id is an integer
    # Step 1: Direct attempt (rarely works with StringSession)
    try:
        entity = await client.get_entity(channel_id)
        logger.info(f"Resolved entity for {channel_id} directly")
        return entity
    except (ValueError, Exception) as e:
        logger.info(f"Direct get_entity({channel_id}) failed: {e}, trying get_dialogs...")

    # Step 2: Populate cache via get_dialogs(), then retry
    try:
        dialogs = await client.get_dialogs()
        logger.info(f"Loaded {len(dialogs)} dialogs into entity cache")
    except Exception as e:
        logger.warning(f"get_dialogs() failed: {e}")

    try:
        entity = await client.get_entity(channel_id)
        logger.info(f"Resolved entity for {channel_id} after get_dialogs()")
        return entity
    except (ValueError, Exception) as e:
        logger.info(f"get_entity({channel_id}) still failed after get_dialogs: {e}, trying PeerChannel...")

    # Step 3: Construct PeerChannel from the raw ID
    raw_id = channel_id
    if raw_id < 0:
        raw_id_str = str(abs(raw_id))
        if raw_id_str.startswith("100") and len(raw_id_str) > 10:
            raw_id = int(raw_id_str[3:])
        else:
            raw_id = abs(raw_id)

    try:
        peer = PeerChannel(channel_id=raw_id)
        entity = await client.get_entity(peer)
        logger.info(f"Resolved entity for {channel_id} via PeerChannel({raw_id})")
        return entity
    except Exception as e:
        logger.error(f"All entity resolution methods failed for {channel_id}: {e}")
        raise ValueError(
            f"Could not resolve channel {channel_id}. "
            f"Make sure the Telethon session account has joined/is admin in this channel. "
            f"Original error: {e}"
        )


async def scan_channel(channel_id, bot=None, log_channel=None, progress_cb=None, cleanup=True, formats_by_channel=None):
    """
    Scan channel for stories using the LEARNED FORMAT SYSTEM.

    Parsing priority:
      1. If a learned template exists for this channel  →  use it (strict match)
      2. Else fall back to the legacy regex/keyword parser

    If the message matches neither, it is IGNORED — this prevents random
    channel posts (announcements, copyright notices…) from entering the DB.

    Returns dict with: messages, stories, names, keys.
    """
    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH
    )

    await client.start()

    # Load the learned formats fresh each scan
    learned = load_learned_formats()
    has_learned_template = bool(
        learned.get(str(channel_id))
        or learned.get(str(abs(int(channel_id) if str(channel_id).lstrip("-").isdigit() else 0)))
    )

    total_messages = 0
    stories_found = 0
    names = []
    keys_seen = []

    scan_start = asyncio.get_event_loop().time()

    try:
        entity = await _resolve_entity(client, channel_id)
        logger.info(f"Starting scan of channel {channel_id} (has_learned={has_learned_template})")

        async for msg in client.iter_messages(entity, limit=None, reverse=True):

            total_messages += 1
            story = None

            if has_learned_template:
                # STRICT mode — only learned-format posts are indexed
                story = _apply_learned_formats(channel_id, msg, learned)
            else:
                # Fallback legacy path (no template configured yet)
                story = parse_story(msg)

            if not story:
                continue

            # Try to get photo file_id if message has photo and we have bot + log channel
            if bot and log_channel and msg.photo:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                        path = tmp.name
                    await client.download_media(msg.photo, path)
                    try:
                        with open(path, "rb") as f:
                            sent = await bot.send_photo(chat_id=log_channel, photo=f)
                        if sent and sent.photo:
                            fid = sent.photo[-1].file_id
                            story["photo"] = fid
                        try:
                            await sent.delete()
                        except Exception:
                            pass
                    finally:
                        if os.path.exists(path):
                            os.unlink(path)
                except Exception:
                    pass

            add_story(story)
            names.append(story["text"])
            keys_seen.append(story["name"])
            stories_found += 1

            if progress_cb:
                try:
                    elapsed = asyncio.get_event_loop().time() - scan_start
                    await progress_cb(
                        {
                            "stories_found": stories_found,
                            "total_messages": total_messages,
                            "last_story": story.get("text") or "",
                            "elapsed_s": elapsed,
                        }
                    )
                except Exception:
                    pass

            # Yield to event loop every 50 messages so bot stays responsive
            if total_messages % 50 == 0:
                await asyncio.sleep(0)

    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    # Remove stories that no longer exist in channel (deleted posts)
    if cleanup:
        remove_stories_not_in(keys_seen)

    # de-duplicate names and keep stable order
    seen = set()
    unique_names = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)

    return {
        "messages": total_messages,
        "stories": stories_found,
        "names": unique_names,
        "keys": list(set(keys_seen))
    }
