#!/bin/bash
# Deploy Hugo static site directly to evconduit.com via SSH (Tailscale)
# evconduit.com serves from /mnt/volume-ssd/evconduit-news/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PUBLIC_DIR="$SCRIPT_DIR/output/public"

# evconduit.com server over Tailscale
EV_HOST="${EV_HOST:-100.113.60.59}"
EV_USER="${EV_USER:-root}"
EV_PATH="${EV_PATH:-/mnt/volume-ssd/evconduit-news}"

# Always rebuild Hugo in production mode (via Docker, no local install needed)
echo "Building Hugo site (production, baseURL: /news/)..."
docker run --rm \
    --user "$(id -u):$(id -g)" \
    -v "$SCRIPT_DIR/output:/src" \
    -v "$SCRIPT_DIR/hugo/themes:/src/themes" \
    hugomods/hugo:latest \
    hugo --minify --environment production --baseURL "https://www.evconduit.com/news/"
echo "Build complete."

# Sync to evconduit.com via SSH
echo "Syncing $PUBLIC_DIR/ → $EV_USER@$EV_HOST:$EV_PATH/"
ssh -o StrictHostKeyChecking=no "$EV_USER@$EV_HOST" "mkdir -p $EV_PATH"
rsync -avz --delete \
    -e "ssh -o StrictHostKeyChecking=no" \
    "$PUBLIC_DIR/" \
    "$EV_USER@$EV_HOST:$EV_PATH/"

# Warm Cloudflare cache
echo "Warming Cloudflare cache..."
find "$PUBLIC_DIR/posts" -name "index.html" -mmin -15 | while read -r post; do
    rel_url="/news${post#$PUBLIC_DIR}"
    rel_url="${rel_url%/index.html}/"
    curl -s -o /dev/null "https://www.evconduit.com${rel_url}" &
done
curl -s -o /dev/null "https://www.evconduit.com/news/" &
wait
echo "Cache warmed."

echo "Deployed → https://www.evconduit.com/news/"
