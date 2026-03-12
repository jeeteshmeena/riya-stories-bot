#!/bin/bash
# Quick Deploy Script for Riya Stories Bot on Google Cloud VPS
# Usage: chmod +x QUICK_DEPLOY.sh && ./QUICK_DEPLOY.sh

set -e

echo "=== Riya Stories Bot - Quick Deploy ==="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    print_error "Please don't run this script as root. Run as regular user."
    exit 1
fi

# Update system
print_status "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install required packages
print_status "Installing Python and Git..."
sudo apt install -y python3 python3-venv python3-pip git

# Clone repository if not exists
if [ ! -d "riya-stories-bot" ]; then
    print_status "Cloning repository..."
    git clone https://github.com/jeeteshmeena/riya-stories-bot.git
    cd riya-stories-bot
else
    print_status "Repository exists, updating..."
    cd riya-stories-bot
    git pull
fi

# Check if .env exists
if [ ! -f .env ]; then
    print_warning ".env file not found. Creating from template..."
    cp .env.example .env
    print_warning "Please edit .env file with your credentials:"
    echo "  nano .env"
    echo ""
    echo "Required variables:"
    echo "  - BOT_TOKEN (from @BotFather)"
    echo "  - CHANNEL_ID (channel to scan)"
    echo "  - ADMIN_ID, OWNER_ID (your Telegram ID)"
    echo ""
    read -p "Press Enter after editing .env file to continue..."
fi

# Install dependencies and setup
print_status "Setting up bot environment..."
chmod +x deploy/install-vps.sh deploy/run.sh
./deploy/install-vps.sh

# Setup systemd service
print_status "Setting up systemd service..."
sudo cp /tmp/riya-bot.service /etc/systemd/system/
sudo sed -i "s|YOUR_USER|$(whoami)|g" /etc/systemd/system/riya-bot.service
sudo sed -i "s|/home/YOUR_USER/riya-stories-bot|$(pwd)|g" /etc/systemd/system/riya-bot.service

# Start the service
print_status "Starting bot service..."
sudo systemctl daemon-reload
sudo systemctl enable riya-bot
sudo systemctl start riya-bot

# Check status
echo ""
print_status "Checking bot status..."
sudo systemctl status riya-bot --no-pager

echo ""
echo "=== Deployment Complete ==="
echo ""
print_status "Bot is now running as a systemd service!"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status riya-bot     # Check status"
echo "  sudo journalctl -u riya-bot -f    # View logs"
echo "  sudo systemctl restart riya-bot   # Restart bot"
echo "  sudo systemctl stop riya-bot      # Stop bot"
echo ""
print_status "Test your bot in Telegram with /start command"
echo ""
print_warning "If bot doesn't respond, check:"
echo "  1. .env file has correct BOT_TOKEN"
echo "  2. Bot has permission to access the channel"
echo "  3. SESSION_STRING is valid (for /scan command)"
