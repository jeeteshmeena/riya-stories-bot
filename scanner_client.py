from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_STRING
from telethon.sessions import StringSession

from parser import parse_story
from database import add_story


async def scan_channel(channel_id):

    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH
    )

    await client.start()

    total_messages = 0
    stories_found = 0
    names = []

    async for msg in client.iter_messages(channel_id):

        total_messages += 1

        story = parse_story(msg)

        if not story:
            continue

        add_story(story)
        names.append(story["text"])

        stories_found += 1

    await client.disconnect()

    # de-duplicate and keep stable order
    seen = set()
    unique_names = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)

    return {
        "messages": total_messages,
        "stories": stories_found,
        "names": unique_names
    }
