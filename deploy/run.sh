#!/bin/bash
# Run Riya Bot on Google Cloud VPS
# Usage: ./run.sh  (or run in background: nohup ./run.sh >> bot.log 2>&1 &)

set -e
cd "$(dirname "$0")/.."

# Create venv if missing
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

# Create data dir if using DATA_DIR
if [ -n "$DATA_DIR" ] && [ "$DATA_DIR" != "." ]; then
    mkdir -p "$DATA_DIR"
fi

echo "Starting Riya Bot..."
exec python stories_bot.py
