#!/bin/sh
# Run from the repo root (Git Bash): sh deploy/nas/update_app.sh
# Copies server/ and the web files to the NAS, swaps /volume1/docker/meetings/app, restarts the container.
set -e
NAS=${NAS:-nas}
REMOTE=/volume1/docker/meetings
TS=$(date +%Y%m%d-%H%M%S)
STAGE=$(mktemp -d)
mkdir -p "$STAGE/server" "$STAGE/web"
cp -r server/. "$STAGE/server/"
rm -rf "$STAGE/server/tests" "$STAGE/server/__pycache__"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
cp index.html sw.js manifest.webmanifest "$STAGE/web/"
cp -r css js icons "$STAGE/web/"

ssh "$NAS" "rm -rf $REMOTE/app.new; mkdir -p $REMOTE/app.new" 2>&1 | grep -v "post-quantum\|store now\|openssh.com" || true
tar -cf - -C "$STAGE" server web | ssh "$NAS" "tar -xf - -C $REMOTE/app.new" 2>&1 | grep -v "post-quantum\|store now\|openssh.com" || true
ssh "$NAS" "cd $REMOTE && [ -f app.new/server/main.py ] && [ -f app.new/web/index.html ] && { [ ! -d app ] || mv app app.old-$TS; } && mv app.new app && mkdir -p /volume1/projects/meetings && touch /volume1/projects/meetings/.restart && echo swapped" 2>&1 | grep -v "post-quantum\|store now\|openssh.com"
rm -rf "$STAGE"
echo "done; old app kept as app.old-$TS; container restarts itself within seconds"
