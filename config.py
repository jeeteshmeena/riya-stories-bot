import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
OWNER_ID = int(os.getenv("OWNER_ID", 0))

REQUEST_GROUP = int(os.getenv("REQUEST_GROUP", 0))
COPYRIGHT_CHANNEL = int(os.getenv("COPYRIGHT_CHANNEL", 0))
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", 0))

BOT_LANGUAGE = os.getenv("BOT_LANGUAGE", "english")
AUTO_SCAN = os.getenv("AUTO_SCAN", "true")
SEARCH_LIMIT = int(os.getenv("SEARCH_LIMIT", 5))
