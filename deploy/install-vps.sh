#!/bin/bash
# Install Riya Bot on Google Cloud VPS (Ubuntu/Debian)
# Run: chmod +x install-vps.sh && ./install-vps.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_DIR="${BOT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
USER_NAME="${USER_NAME:-$(whoami)}"

echo "=== Riya Bot VPS Installer ==="
echo "Install directory: $BOT_DIR"
echo "User: $USER_NAME"
echo ""

# Ensure directory exists
mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

# Create .env if missing
if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.example .env 2>/dev/null || true
    echo ""
    echo "IMPORTANT: Edit .env and add your tokens:"
    echo "  nano .env"
    echo ""
    echo "Required: BOT_TOKEN, CHANNEL_ID"
    echo "For /scan: API_ID, API_HASH, SESSION_STRING (run generate_session.py locally first)"
    echo ""
fi

# Create venv
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Install systemd service (optional)
SVC_FILE="$BOT_DIR/deploy/riya-bot.service"
if [ -f "$SVC_FILE" ]; then
    SED_DIR=$(echo "$BOT_DIR" | sed 's/[\/&]/\\&/g')
    SED_USER=$(echo "$USER_NAME" | sed 's/[\/&]/\\&/g')
    sed -e "s|/home/YOUR_USER/riya-stories-bot|$BOT_DIR|g" \
        -e "s|YOUR_USER|$USER_NAME|g" \
        "$SVC_FILE" > /tmp/riya-bot.service
    echo ""
    echo "To run as systemd service (survives reboot, auto-restart):"
    echo "  sudo cp /tmp/riya-bot.service /etc/systemd/system/"
    echo "  sudo systemctl daemon-reload"
    echo "  sudo systemctl enable riya-bot"
    echo "  sudo systemctl start riya-bot"
    echo "  sudo systemctl status riya-bot"
    echo ""
fi

echo "=== Done ==="
echo "To run manually: ./deploy/run.sh"
echo "Or in background: nohup ./deploy/run.sh >> bot.log 2>&1 &"
