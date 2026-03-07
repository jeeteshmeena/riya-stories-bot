import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))
GROUP_ID = int(os.getenv("GROUP_ID", 0))
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

REQUEST_GROUP = int(os.getenv("REQUEST_GROUP", 0))
COPYRIGHT_CHANNEL = int(os.getenv("COPYRIGHT_CHANNEL", 0))
