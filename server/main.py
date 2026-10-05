"""Entrypoint: python -m server.main (Docker CMD)."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import uvicorn

from .app import create_app
from .backup import BackupThread
from .fallback import FallbackThread
from .importer import import_dir
from .uploads import scan_inbox, sweep_uploads

log = logging.getLogger("server.main")

RESTART_POLL_S = 2
RESTART_GRACE_S = 3  # lets update_app.sh finish its two-rename swap
INBOX_SCAN_S = 30
UPLOAD_SWEEP_S = 3600  # abandoned-upload TTL sweep cadence (inside the inbox loop)


def restart_requested(marker: Path, started_at: float) -> bool:
    """True when `.restart` exists and was touched at/after this process started
    (an older marker is a leftover from before this boot)."""
    try:
        return marker.stat().st_mtime >= started_at
    except OSError:
        return False


def _restart_watcher(marker: Path, started_at: float) -> None:
    while True:
        time.sleep(RESTART_POLL_S)
        try:
            if restart_requested(marker, started_at):
                time.sleep(RESTART_GRACE_S)
                marker.unlink(missing_ok=True)
                log.warning("restart requested via %s; exiting for Docker to restart", marker)
                logging.shutdown()
                os._exit(0)
        except Exception:
            log.exception("restart watcher error")


def _inbox_tick(store, jobs, now: float, last_sweep: float) -> float:
    """One inbox-loop round: scan the inbox; at most hourly also sweep abandoned uploads.
    Returns the new last_sweep time."""
    try:
        scan_inbox(store, jobs)
    except Exception:
        log.exception("inbox scan failed")
    if now - last_sweep >= UPLOAD_SWEEP_S:
        last_sweep = now
        try:
            gone = sweep_uploads(store, now)
            if gone:
                log.info("swept %d abandoned uploads", len(gone))
        except Exception:
            log.exception("upload sweep failed")
    return last_sweep


def _inbox_loop(store, jobs) -> None:
    last_sweep = 0.0
    while True:
        time.sleep(INBOX_SCAN_S)
        last_sweep = _inbox_tick(store, jobs, time.time(), last_sweep)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    started_at = time.time()
    data = Path(os.environ.get("MEETINGS_DATA", "/data"))
    # create_app starts the AI thread and the 60 s stale-requeue loop
    app = create_app(data)
    store, jobs = app.state.store, app.state.jobs
    try:
        import_dir(store)
    except Exception:
        log.exception("startup import failed")
    FallbackThread(store, jobs).start()
    BackupThread(store).start()
    threading.Thread(target=_inbox_loop, args=(store, jobs), daemon=True, name="inbox-scan").start()
    threading.Thread(target=_restart_watcher, args=(data / ".restart", started_at), daemon=True,
                     name="restart-watch").start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8778")))


if __name__ == "__main__":
    main()
