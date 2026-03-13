# Google Cloud VPS Deployment Guide - Riya Stories Bot

## Overview
This guide provides step-by-step instructions to deploy the Riya Stories Bot on Google Cloud VPS (Ubuntu/Debian) with systemd service for automatic startup and restart.

## Prerequisites
- Google Cloud VPS instance (Ubuntu 22.04+ recommended)
- SSH access to the VPS
- Telegram Bot Token from @BotFather
- Channel ID to scan for stories
- (Optional) API credentials from https://my.telegram.org for scanning

## Step 1: Connect to VPS and Setup

```bash
# Connect to your Google Cloud VPS
gcloud compute ssh --zone "YOUR_ZONE" "YOUR_INSTANCE_NAME" --project "YOUR_PROJECT_ID"

# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y python3 python3-venv python3-pip git
```

## Step 2: Clone and Configure Bot

```bash
# Clone the repository
git clone https://github.com/jeeteshmeena/riya-stories-bot.git
cd riya-stories-bot

# Copy environment template
cp .env.example .env

# Edit environment file
nano .env
```

### Required Environment Variables

Add these minimum required values to `.env`:

```bash
# Required for bot to work
BOT_TOKEN=8383666609:AAGQeBaXTYrt7WFoBpRNMdO0EgeHIK1Ro9M
CHANNEL_ID=-1003097953020

# Required for admin commands (/scan, /stats)
ADMIN_ID=5123283499
OWNER_ID=5123283499

# Optional but recommended
REQUEST_GROUP=-1003886314549
LOG_CHANNEL=-1003596534878
COPYRIGHT_CHANNEL=-1003860190119

# For scanning functionality
API_ID=27720240
API_HASH=006c9de3f9413c37318df8fa005f2799
SESSION_STRING=1BVtsOJwBu6tf1rOht1lYbDE-KFJWEjPBKev9JOJMv0EqEZw3TSoc2ZTqtV8pbKkqVXeATKJUj6ktJnQx7xszN_98kMfvghTyVIU5EKxWv5Vf6TfnIVH7BWifZI6D1XwHfwv_9n6PFPrhQC_2rreSK1gBwyIA0FeiUa-DEgXrhAVgmtD4BKyXexrPprQqaWq8hyJKqQ5widccvTQCkUNc8si7IRNQrSPCug7cYi6tJvfkhcsr6ypojh7uR5XGxjApjNA_yrPh-giJWdxd8BDBH5koiGWUogy8ZuBfqhX6IvbUEGLcgQ9t-UmiBHZN0en7QX0_C9b1Gi1E-05KMwGVQ4O7uPoTt_o=

# Deployment settings
AUTO_SCAN=true
RUN_HTTP_SERVER=false
DATA_DIR=.
```

## Step 3: Automated Installation

The repository includes deployment scripts for easy setup:

```bash
# Make scripts executable
chmod +x deploy/install-vps.sh deploy/run.sh

# Run the installer (creates venv, installs dependencies, sets up systemd)
./deploy/install-vps.sh
```

## Step 4: Run as Systemd Service (Recommended)

```bash
# Copy the systemd service file
sudo cp /tmp/riya-bot.service /etc/systemd/system/

# Replace placeholders with actual paths and user
sudo sed -i "s|YOUR_USER|$(whoami)|g" /etc/systemd/system/riya-bot.service
sudo sed -i "s|/home/YOUR_USER/riya-stories-bot|$(pwd)|g" /etc/systemd/system/riya-bot.service

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable riya-bot
sudo systemctl start riya-bot

# Check status
sudo systemctl status riya-bot
```

## Step 5: Alternative - Run in Background (No systemd)

If you don't want to use systemd:

```bash
# Run in background with logging
nohup ./deploy/run.sh >> bot.log 2>&1 &

# Check logs
tail -f bot.log

# Stop the bot
pkill -f "python stories_bot.py"
```

## Step 6: Test the Bot

1. Send `/start` to your bot in Telegram
2. Try searching for a story name
3. Admin commands:
   - `/scan` - Scan the channel for stories
   - `/stats` - View bot statistics

## Troubleshooting

### Bot doesn't start
```bash
# Check service status
sudo systemctl status riya-bot

# View logs
sudo journalctl -u riya-bot -f

# Check environment variables
cat .env
```

### Scan returns 0 stories
1. Verify `SESSION_STRING` is valid
2. Check if `CHANNEL_ID` is correct
3. Ensure bot has access to the channel

### Common Issues
- **Permission denied**: Ensure `.env` file permissions are correct
- **Module not found**: Run `./deploy/install-vps.sh` to reinstall dependencies
- **Bot token invalid**: Get new token from @BotFather

## Maintenance

### Update the bot
```bash
# Pull latest changes
git pull

# Restart service
sudo systemctl restart riya-bot
```

### Backup data
```bash
# Backup JSON databases
cp *.json backup/$(date +%Y%m%d)/
```

### Monitor logs
```bash
# Real-time logs
sudo journalctl -u riya-bot -f

# Service status
sudo systemctl status riya-bot
```

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| BOT_TOKEN | Yes | Telegram bot token from @BotFather |
| CHANNEL_ID | Yes | Channel to scan for stories |
| ADMIN_ID | For /scan | Your Telegram user ID |
| OWNER_ID | For /scan | Your Telegram user ID |
| REQUEST_GROUP | For /request | Group to post requests |
| LOG_CHANNEL | Optional | Channel for logs |
| API_ID | For /scan | From my.telegram.org |
| API_HASH | For /scan | From my.telegram.org |
| SESSION_STRING | For /scan | Generated session string |
| AUTO_SCAN | Optional | Auto-rescan channel (default: true) |
| RUN_HTTP_SERVER | false | Set true only for Render |
| DATA_DIR | . | Directory for JSON DB files |

## Generate Session String

If you need to generate a new session string:

```bash
# Run locally (not on VPS)
python generate_session.py

# Enter your API_ID and API_HASH from https://my.telegram.org
# Copy the output SESSION_STRING to your .env file
```

## Security Notes

1. Never commit `.env` file to version control
2. Use strong, unique bot tokens
3. Restrict access to the VPS (firewall, SSH keys)
4. Regularly update dependencies

---

**Bot by @MeJeetX | Version v10**
