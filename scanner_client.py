import asyncio
from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME
from parser import parse_story
from database import add_story

# Telethon Client
client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH
)


async def scan_channel(channel_id):

    print("Scanner starting...")

    await client.start()

    total_messages = 0
    stories_found = 0

    async for message in client.iter_messages(channel_id):

        total_messages += 1

        story = parse_story(message)

        if story:

            add_story(story)

            stories_found += 1

            print("Story added:", story["name"])

        # speed optimization
        if total_messages % 100 == 0:

            print(
                f"Scanned {total_messages} messages | Stories: {stories_found}"
            )

    print("Scan completed")
    print("Total messages:", total_messages)
    print("Stories found:", stories_found)

    await client.disconnect()
