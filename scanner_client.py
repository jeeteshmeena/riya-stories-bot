import asyncio
import tempfile
import os

from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_STRING
from telethon.sessions import StringSession

from parser import parse_story
from database import add_story, remove_stories_not_in


async def scan_channel(channel_id, bot=None, log_channel=None, progress_cb=None, cleanup=True):
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

    async for msg in client.iter_messages(channel_id):

        total_messages += 1

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
