from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_NAME

client = None

def get_client():
    global client

    if client is None:
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    return client


async def scan_channel(channel):

    client = get_client()

    await client.start()

    async for msg in client.iter_messages(channel, limit=None):

        print(msg.id)
