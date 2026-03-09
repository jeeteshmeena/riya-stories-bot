from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("Enter API_ID: "))
api_hash = input("Enter API_HASH: ")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nYour SESSION_STRING:\n")
    print(client.session.save())
