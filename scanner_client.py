import asyncio
import tempfile
import os
import logging
import time

from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_STRING
from telethon.sessions import StringSession

from parser import parse_story
from database import add_story, remove_stories_not_in, load_db

logger = logging.getLogger(__name__)


async def scan_channel(channel_id, bot=None, log_channel=None):
    """
    Scan channel for stories. If bot and log_channel are provided, extract and store
    photo file_ids for stories that have images.
    Returns names (display), keys (DB keys for cleanup), stories count.
    """
    from database import load_scan_state, save_scan_state
    
    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
        timeout=30,
        connection_retries=3,
        retry_delay=5
    )

    try:
        await client.start()
    except Exception as e:
        logger.error(f"Failed to start Telegram client: {e}")
        raise

    # Load scan state for incremental scanning
    scan_state = load_scan_state()
    last_message_id = scan_state.get("last_message_id", 0)
    incremental = last_message_id > 0

    total_messages = 0
    stories_found = 0
    names = []
    keys_seen = []
    max_message_id = 0

    try:
        # If incremental, only get messages newer than last scan
        if incremental:
            logger.info(f"Performing incremental scan from message ID {last_message_id}")
            async for msg in client.iter_messages(channel_id, min_id=last_message_id):
                max_message_id = max(max_message_id, msg.id)
                total_messages += 1

                story = parse_story(msg)

                if not story:
                    continue

                # Try to get photo file_id if message has photo and we have bot + log channel
                if bot and log_channel and msg.photo:
                    try:
                        # Rate limit photo uploads to prevent 429 errors
                        await asyncio.sleep(2)  # Increased to 2 seconds between uploads
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

                # Yield to event loop every 50 messages so bot stays responsive
                if stories_found % 50 == 0:
                    await asyncio.sleep(0)
        else:
            # Full scan for first time
            logger.info("Performing full scan")
            async for msg in client.iter_messages(channel_id):
                max_message_id = max(max_message_id, msg.id)
                total_messages += 1

                story = parse_story(msg)

                if not story:
                    continue

                # Try to get photo file_id if message has photo and we have bot + log channel
                if bot and log_channel and msg.photo:
                    try:
                        # Rate limit photo uploads to prevent 429 errors
                        await asyncio.sleep(2)  # Increased to 2 seconds between uploads
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

                # Yield to event loop every 50 messages so bot stays responsive
                if stories_found % 50 == 0:
                    await asyncio.sleep(0)

    except Exception as e:
        logger.error(f"Error during channel scan: {e}")
        raise
    finally:
        await client.disconnect()

    # Save scan state for next incremental scan
    if max_message_id > 0:
        save_scan_state({
            "last_message_id": max_message_id,
            "last_scan_time": time.time()
        })

    # Only a full scan can safely detect deleted posts.
    # Incremental scans only contain new/edited items and must not remove older entries.
    if not incremental:
        remove_stories_not_in(keys_seen)

    # Build names from complete DB so search index remains complete after incremental runs.
    current_db = load_db()
    all_names = []
    for story in current_db.values():
        text = story.get("text")
        if text:
            all_names.append(text)

    # de-duplicate names and keep stable order
    seen = set()
    unique_names = []
    for name in all_names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)

    total_stories = len(current_db)
    total_seen_keys = list(current_db.keys())

    return {
        "messages": total_messages,
        "stories": total_stories,
        "names": unique_names,
        "keys": total_seen_keys,
        "incremental": incremental
    }
