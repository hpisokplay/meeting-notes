#!/bin/sh
# One-time install on the NAS. Run: sudo sh /volume1/docker/meetings/install.sh
# (compose.yaml, Dockerfile, claude-wrapper.sh, requirements.txt, install.sh must be in /volume1/docker/meetings/)
set -e
BASE=/volume1/docker/meetings
DOCKER=/usr/local/bin/docker
TS=/var/packages/Tailscale/target/bin/tailscale

# Synology does not create missing bind-mount source dirs
mkdir -p "$BASE/app" "$BASE/claude-config" \
  /volume1/projects/meetings /volume1/docker/backups/meetings \
  /volume1/docker/worker/home /volume1/docker/tgbridge
# data dir owned by uid 1026 (hpisokplay) / gid 100 (users): the ssh user must be able to touch
# .restart (update_app.sh) and the TG bot must be able to write into inbox/
chown -R 1026:100 /volume1/projects/meetings || true
# bind-mounted files must exist
[ -e /volume1/docker/worker/home/.claude-token ] || touch /volume1/docker/worker/home/.claude-token
[ -e /volume1/docker/tgbridge/token ] || touch /volume1/docker/tgbridge/token
[ -e /volume1/docker/tgbridge/allowed_ids ] || touch /volume1/docker/tgbridge/allowed_ids

# worker key (mode 600, owned by uid 1026 = hpisokplay so ssh user can read it)
if [ ! -s "$BASE/worker.key" ]; then
  ( umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$BASE/worker.key" )
fi
chown 1026 "$BASE/worker.key" 2>/dev/null || true
chmod 600 "$BASE/worker.key"

cd "$BASE"
"$DOCKER" compose up -d --build

if [ -x "$TS" ]; then
  "$TS" serve --bg --https=8443 http://127.0.0.1:8778 || echo "WARN: tailscale serve failed"
else
  echo "WARN: tailscale binary not found at $TS"
fi

echo "---- status ----"
"$DOCKER" ps --filter name=meetings
[ -x "$TS" ] && "$TS" serve status || true
echo "worker key file: $BASE/worker.key"
echo "URL: https://ddys.tail95e8ec.ts.net:8443/"
