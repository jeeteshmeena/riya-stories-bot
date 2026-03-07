import os
from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME
from parser import parse_story
from database import add_story


client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH
)


async def scan_channel(channel_id):

    await client.start()

    print("Telethon scanner started")

    async for message in client.iter_messages(channel_id, limit=None):

        story = parse_story(message)

        if story:

            add_story(story)

            print("Story added:", story["name"])

    print("Scan finished")
