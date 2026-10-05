"""Daily backup: tar.gz of meetings/ groups.json settings.json index.json (audio excluded)."""
from __future__ import annotations

import logging
import os
import tarfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .storage import Store

log = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Taipei")
HOUR, MINUTE = 3, 40
KEEP = 14
PREFIX = "meetings-"
SUFFIX = ".tar.gz"
ITEMS = ("meetings", "groups.json", "settings.json", "index.json")


def backup_dir() -> Path:
    return Path(os.environ.get("MEETINGS_BACKUP") or "/backup")


def run_backup(store: Store, dest: Path | None = None, now: float | None = None) -> Path:
    dest = Path(dest) if dest else backup_dir()
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(time.time() if now is None else now, TZ).strftime("%Y%m%d-%H%M")
    out = dest / f"{PREFIX}{stamp}{SUFFIX}"
    tmp = out.with_name(out.name + ".tmp")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            for name in ITEMS:
                p = store.path(name)
                if p.exists():
                    tar.add(p, arcname=name)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    prune(dest)
    return out


def prune(dest: Path, keep: int = KEEP) -> list[Path]:
    files = sorted(p for p in dest.glob(f"{PREFIX}*{SUFFIX}") if p.is_file())  # stamp sorts by time
    old = files[:-keep] if keep > 0 else files
    for p in old:
        try:
            p.unlink()
        except OSError:
            log.warning("could not prune %s", p)
    return old


def seconds_until_next(now: float) -> float:
    """Seconds from `now` to the next 03:40 Asia/Taipei."""
    t = datetime.fromtimestamp(now, TZ)
    nxt = t.replace(hour=HOUR, minute=MINUTE, second=0, microsecond=0)
    if nxt <= t:
        nxt += timedelta(days=1)
    return (nxt - t).total_seconds()


class BackupThread(threading.Thread):
    def __init__(self, store: Store, dest: Path | None = None, clock=time.time):
        super().__init__(daemon=True, name="backup")
        self.store, self.dest, self.clock = store, dest, clock
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.wait(seconds_until_next(self.clock())):
            try:
                log.info("backup written: %s", run_backup(self.store, self.dest, self.clock()))
            except Exception:
                log.exception("backup failed")
            self._halt.wait(61)  # never fire twice for the same minute
