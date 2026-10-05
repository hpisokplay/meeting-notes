#!/bin/sh
# Run from the repo root (Git Bash): sh deploy/nas/update_app.sh
# Copies server/ and the web files to the NAS, swaps /volume1/docker/meetings/app, restarts the container.
# Keeps the 3 newest app.old-* copies (old *code* only; never touches /volume1/projects/meetings data).
set -e
NAS=${NAS:-nas}
REMOTE=/volume1/docker/meetings
TS=$(date +%Y%m%d-%H%M%S)
STAGE=$(mktemp -d)
TARBALL="$STAGE.tar"
ERR="$STAGE.err"
cleanup() { rm -rf "$STAGE" "$TARBALL" "$ERR"; }
trap cleanup EXIT

die() { echo "FAILED: $*" >&2; exit 1; }

# ssh with the noisy key-exchange warnings filtered out; keeps ssh's own exit code
# (stderr goes to a temp file instead of a pipe, so no PIPESTATUS is needed).
rssh() {
  if ssh "$NAS" "$@" 2>"$ERR"; then rc=0; else rc=$?; fi
  grep -v "post-quantum\|store now\|openssh.com" "$ERR" >&2 || true
  return $rc
}

# 1. stage: server code without venv / caches / tests, plus the web files
mkdir -p "$STAGE/web"
tar -cf "$STAGE/server.tar" --exclude='server/.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='server/tests' --exclude='*.pyc' server || die "staging server/ (tar)"
tar -xf "$STAGE/server.tar" -C "$STAGE" || die "staging server/ (untar)"
rm -f "$STAGE/server.tar"
cp index.html sw.js manifest.webmanifest "$STAGE/web/" || die "copying web files"
cp -r css js icons "$STAGE/web/" || die "copying web dirs"
[ -f "$STAGE/server/main.py" ] && [ ! -e "$STAGE/server/.venv" ] && [ ! -e "$STAGE/server/tests" ] \
  || die "staged tree looks wrong"

# 2. upload into app.new (tarball file, no pipeline: every exit code is checked)
tar -cf "$TARBALL" -C "$STAGE" server web || die "building the upload tarball"
rssh "rm -rf $REMOTE/app.new && mkdir -p $REMOTE/app.new" || die "preparing app.new on the NAS"
rssh "tar -xf - -C $REMOTE/app.new" < "$TARBALL" || die "uploading/extracting on the NAS"

# 3. swap + restart marker
rssh "cd $REMOTE && [ -f app.new/server/main.py ] && [ -f app.new/web/index.html ] && { [ ! -d app ] || mv app app.old-$TS; } && mv app.new app && mkdir -p /volume1/projects/meetings && touch /volume1/projects/meetings/.restart && echo swapped" \
  || die "swapping app.new into place"

# 4. prune old code copies: keep the 3 newest app.old-* (names sort by timestamp)
rssh "cd $REMOTE && ls -1d app.old-* 2>/dev/null | sort -r | tail -n +4 | while read -r d; do case \"\$d\" in app.old-*) rm -rf \"./\$d\" && echo \"removed \$d\";; esac; done" \
  || echo "WARN: pruning old app copies failed (harmless)" >&2

echo "done; old app kept as app.old-$TS; container restarts itself within seconds"
