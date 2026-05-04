#!/bin/bash
# Run this locally to push the project to the server
# Usage: bash deploy.sh user@your-server-ip

set -e

SERVER=$1

if [ -z "$SERVER" ]; then
  echo "Usage: bash deploy.sh user@your-server-ip"
  exit 1
fi

REMOTE_DIR="/opt/compliance-digest"

echo "Deploying to $SERVER..."
ssh "$SERVER" "mkdir -p $REMOTE_DIR"

rsync -avz \
  --exclude='.env' \
  --exclude='data/*.db' \
  --exclude='data/*.html' \
  --exclude='data/*.log' \
  --exclude='__pycache__' \
  --exclude='.git' \
  ./ "$SERVER:$REMOTE_DIR/"

echo ""
echo "Files synced. Now run setup on server:"
echo "  ssh $SERVER 'bash $REMOTE_DIR/setup.sh'"
