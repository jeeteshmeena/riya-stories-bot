# Riya Stories Bot v10

Telegram story finder bot with AI fuzzy search, inline buttons, and admin stats.

## Features

- AI fuzzy search
- Progress bar replies
- Spam filter
- Language system
- Inline buttons
- JSON database (stories, claims, requests)
- Admin stats, /scan, /request

## Quick Start

1. Copy `.env.example` to `.env`
2. Set `BOT_TOKEN` (required) and other variables
3. Run: `python stories_bot.py`

## Google Cloud VPS Deployment

### 1. Clone and configure

```bash
git clone https://github.com/jeeteshmeena/riya-stories-bot.git
cd riya-stories-bot
cp .env.example .env
nano .env   # Add BOT_TOKEN, CHANNEL_ID, etc.
```

### 2. Run with install script (recommended)

```bash
chmod +x deploy/install-vps.sh deploy/run.sh
./deploy/install-vps.sh
./deploy/run.sh
```

### 3. Run as systemd service (survives reboot)

```bash
./deploy/install-vps.sh
sudo cp /tmp/riya-bot.service /etc/systemd/system/
sudo sed -i "s|YOUR_USER|$(whoami)|g" /etc/systemd/system/riya-bot.service
sudo sed -i "s|/home/YOUR_USER/riya-stories-bot|$(pwd)|g" /etc/systemd/system/riya-bot.service
sudo systemctl daemon-reload
sudo systemctl enable riya-bot
sudo systemctl start riya-bot
sudo systemctl status riya-bot
```

### 4. Run in background (no systemd)

```bash
nohup ./deploy/run.sh >> bot.log 2>&1 &
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| BOT_TOKEN | Yes | Telegram bot token from @BotFather |
| CHANNEL_ID | Yes | Channel to scan for stories |
| ADMIN_ID / OWNER_ID | For /scan, /stats | Your Telegram user ID |
| REQUEST_GROUP | For /request | Group to post requests |
| LOG_CHANNEL | Optional | Channel for logs |
| API_ID, API_HASH, SESSION_STRING | For /scan | From my.telegram.org; run `generate_session.py` |
| RUN_HTTP_SERVER | false | Set `true` only for Render |
| DATA_DIR | . | Directory for JSON DB files |
| AUTO_SCAN | true | Auto-rescan channel every 10 min |

## Generate Session String

Run locally (once):

```bash
python generate_session.py
# Enter API_ID, API_HASH from https://my.telegram.org
# Copy SESSION_STRING to .env
```

## Deploy on Render

1. Connect GitHub repo
2. Add `BOT_TOKEN` and other env vars
3. Set `RUN_HTTP_SERVER=true`
4. Deploy

---

By @MeJeetX
