#!/bin/bash
# Deploy Hugo static site directly to NAS via NFS (local network, fast)
# evconduit.com serves from the same NAS mount at /mnt/nas-backup/evconduit-news/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PUBLIC_DIR="$SCRIPT_DIR/output/public"

# Local NFS mount of the NAS
NFS_MOUNT="${NFS_MOUNT:-$HOME/nas-evconduit}"
NFS_TARGET="${NFS_TARGET:-$NFS_MOUNT/evconduit-news}"
NFS_VOLUME="${NFS_VOLUME:-/volume/4ec2cde2-4416-4372-b4f7-2adc8a4f0ea0/.srv/.unifi-drive/evconduit/.data}"

# Mount NFS if not already mounted
if ! mount | grep -q "$NFS_MOUNT"; then
    echo "Mounting NFS..."
    mkdir -p "$NFS_MOUNT"
    sudo mount -t nfs -o resvport,nolocks,locallocks 192.168.1.235:"$NFS_VOLUME" "$NFS_MOUNT"
fi

# Always rebuild Hugo in production mode (via Docker, no local install needed)
echo "Building Hugo site (production, baseURL: /news/)..."
docker run --rm \
    --user "$(id -u):$(id -g)" \
    -v "$SCRIPT_DIR/output:/src" \
    -v "$SCRIPT_DIR/hugo/themes:/src/themes" \
    hugomods/hugo:latest \
    hugo --minify --environment production --baseURL "https://www.evconduit.com/news/"
echo "Build complete."

# Sync to NAS via local NFS
echo "Syncing $PUBLIC_DIR/ → $NFS_TARGET/"
mkdir -p "$NFS_TARGET"
rsync -rltDv --delete --no-perms --no-owner --no-group "$PUBLIC_DIR/" "$NFS_TARGET/"

# Cleanup old images locally (keep 90 days)
find "$SCRIPT_DIR/output/static/images" -type f -mtime +90 -delete 2>/dev/null || true

FILE_COUNT=$(find "$NFS_TARGET" -type f | wc -l | tr -d ' ')
SIZE=$(du -sh "$NFS_TARGET" | awk '{print $1}')
echo "Deployed $FILE_COUNT files ($SIZE) → https://www.evconduit.com/news/"
