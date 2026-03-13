import asyncio
import tempfile
import os
import re

from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_STRING
from telethon.sessions import StringSession

from parser import parse_story
from database import add_story, remove_stories_not_in


def _parse_with_formats(channel_id, message, formats_by_channel):
    """Try custom regex formats for a channel when default parser fails."""
    if not formats_by_channel:
        return None
    fmts = formats_by_channel.get(str(channel_id)) or []
    if not fmts:
        return None
    from parser import get_text  # avoid circular import issues at top level
    text = get_text(message)
    if not text:
        return None
    for fmt in fmts:
        name_re = fmt.get("name_re")
        link_re = fmt.get("link_re")
        if not name_re and not link_re:
            continue
        name = None
        link = None
        if name_re:
            m = re.search(name_re, text, re.IGNORECASE | re.MULTILINE)
            if m:
                name = m.group(1).strip() if m.groups() else m.group(0).strip()
        if link_re:
            m2 = re.search(link_re, text, re.IGNORECASE | re.MULTILINE)
            if m2:
                link = m2.group(1).strip() if m2.groups() else m2.group(0).strip()
        if not name or not link:
            continue
        key = re.sub(r"\s+", " ", name).strip().lower()
        return {
            "name": key,
            "text": name,
            "link": link,
            "message_id": message.id,
            "caption": text,
            "story_type": None,
        }
    return None


async def scan_channel(channel_id, bot=None, log_channel=None, progress_cb=None, cleanup=True, formats_by_channel=None):
    """
    Scan channel for stories. If bot and log_channel are provided, extract and store
    photo file_ids for stories that have images.
    Returns names (display), keys (DB keys for cleanup), stories count.
    """
    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH
    )

    await client.start()

    total_messages = 0
    stories_found = 0
    names = []
    keys_seen = []

    scan_start = asyncio.get_event_loop().time()

    # CRITICAL: For StringSession, integer IDs require accessing the dialogs first
    # to populate the internal entity cache, otherwise "Could not find input entity" occurs
    try:
        entity = await client.get_entity(channel_id)
    except ValueError:
        try:
            await client.get_dialogs()
            entity = await client.get_entity(channel_id)
        except Exception:
            entity = channel_id # fallback to raw id if still failing, will probably raise

    async for msg in client.iter_messages(entity, limit=None, reverse=True):

        total_messages += 1

        story = parse_story(msg)

        # fallback to custom formats if default parser fails
        if not story:
            story = _parse_with_formats(channel_id, msg, formats_by_channel)

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
        if stories_found % 50 == 0:
            await asyncio.sleep(0)

    await client.disconnect()

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
