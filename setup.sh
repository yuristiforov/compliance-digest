#!/bin/bash
# Run this once on the server after first deploy
# Usage: bash /opt/compliance-digest/setup.sh

set -e

cd /opt/compliance-digest

echo "[1/4] Installing system packages..."
apt-get update -qq
apt-get install -y python3-pip python3-venv

echo "[2/4] Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "[3/4] Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "[4/4] Creating data directory..."
mkdir -p data

echo ""
echo "Setup complete."
echo ""
echo "Next step: create the .env file on the server:"
echo "  nano /opt/compliance-digest/.env"
echo ""
echo "Required contents:"
echo "  ANTHROPIC_API_KEY=sk-ant-api03-..."
echo "  GMAIL_USER=your@gmail.com"
echo "  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx"
