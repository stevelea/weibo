#!/bin/bash
# Deploy Hugo static site to evconduit.com over SSH (Tailscale)
# Syncs directly to evconduit.com's NAS mount at /mnt/nas-backup/evconduit-news/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PUBLIC_DIR="$SCRIPT_DIR/output/public"

# evconduit.com server (use Tailscale IP or hostname)
EV_HOST="${EV_HOST:-100.113.60.59}"
EV_USER="${EV_USER:-root}"
EV_PATH="${EV_PATH:-/mnt/nas-backup/evconduit-news}"

# Always rebuild Hugo in production mode (via Docker, no local install needed)
echo "Building Hugo site (production, baseURL: /news/)..."
docker run --rm \
    --user "$(id -u):$(id -g)" \
    -v "$SCRIPT_DIR/output:/src" \
    -v "$SCRIPT_DIR/hugo/themes:/src/themes" \
    hugomods/hugo:latest \
    hugo --minify --environment production --baseURL "https://www.evconduit.com/news/"
echo "Build complete."

# Create target directory on evconduit.com
ssh -o StrictHostKeyChecking=no "$EV_USER@$EV_HOST" "mkdir -p $EV_PATH"

# Rsync over SSH to evconduit.com
echo "Syncing $PUBLIC_DIR/ → $EV_USER@$EV_HOST:$EV_PATH/"
rsync -avz --delete \
    -e "ssh -o StrictHostKeyChecking=no" \
    "$PUBLIC_DIR/" \
    "$EV_USER@$EV_HOST:$EV_PATH/"

# Cleanup old images locally (keep 90 days)
find "$SCRIPT_DIR/output/static/images" -type f -mtime +90 -delete 2>/dev/null || true

echo "Deployed to https://www.evconduit.com/news/"
