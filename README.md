# Riya Bot v10 (Telegram Story Finder)

Riya is a Telegram bot that scans a source channel, indexes story posts, and lets users search instantly.

## What was hardened for VPS deployment

- Added **config validation** at startup so missing required env vars fail fast with clear error messages.
- Fixed scanner behavior so **incremental scans no longer wipe old stories**.
- Added safer handling for optional channels/groups (`LOG_CHANNEL`, `REQUEST_GROUP`) so the bot keeps running even if those are not configured yet.
- Added deployment assets for Linux VPS:
  - `.env.example`
  - `deploy/riya-bot.service` (systemd)

---

## 1) Server requirements

- Ubuntu 22.04+ (or similar Linux distro)
- Python 3.10+
- A bot token from BotFather
- Telegram API credentials (`API_ID`, `API_HASH`) and a `SESSION_STRING`

---

## 2) Clone and setup

```bash
git clone <your-repo-url> riya-stories-bot
cd riya-stories-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

---

## 3) Configure environment

```bash
cp .env.example .env
nano .env
```

Fill all required variables:

- `BOT_TOKEN`
- `CHANNEL_ID`
- `API_ID`
- `API_HASH`
- `SESSION_STRING`

Optional but recommended:

- `REQUEST_GROUP`
- `LOG_CHANNEL`
- `COPYRIGHT_CHANNEL`
- `ADMIN_ID`, `OWNER_ID`

---

## 4) Quick test run

```bash
source .venv/bin/activate
set -a && source .env && set +a
python stories_bot.py
```

If startup configuration is incomplete, the bot now exits with a clear error listing missing env variables.

---

## 5) Run 24/7 with systemd

Update service paths/user in `deploy/riya-bot.service` if needed, then install:

```bash
sudo cp deploy/riya-bot.service /etc/systemd/system/riya-bot.service
sudo systemctl daemon-reload
sudo systemctl enable riya-bot
sudo systemctl start riya-bot
```

Check status/logs:

```bash
sudo systemctl status riya-bot
sudo journalctl -u riya-bot -f
```

This setup auto-restarts on crash (`Restart=always`) to keep the bot alive continuously.

---

## 6) Updating bot after code changes

```bash
cd /opt/riya-stories-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart riya-bot
```

---

## Notes

- `AUTO_SCAN=true` runs scheduled rescans every 10 minutes.
- First scan can take time depending on channel size.
- Incremental scanning is now safe and won't delete old index entries by mistake.
