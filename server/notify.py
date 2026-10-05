"""Telegram notifications (Bot API via urllib). Never raises; no-op without a token."""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from pathlib import Path

from .storage import BadId, Store

log = logging.getLogger(__name__)

BASE_URL = "https://ddys.tail95e8ec.ts.net:8443/#/m/"
TIMEOUT_S = 10


def _tg_dir() -> Path:
    return Path(os.environ.get("MEETINGS_TG_DIR") or "/run/tg")


def notify(text: str, store: Store | None = None) -> bool:
    """Send `text` to the first allowed id. True when the request was accepted."""
    try:
        if store is not None and not store.get_settings().get("tgNotify", True):
            return False
        d = _tg_dir()
        token = (d / "token").read_text(encoding="utf-8").strip()
        ids = (d / "allowed_ids").read_text(encoding="utf-8").split()
        if not token or not ids:
            return False
        chat_id = ids[0].strip(",;")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text,
                             "disable_web_page_preview": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return 200 <= r.status < 300
    except Exception as e:  # missing files, network, bad token: all silent
        log.debug("notify skipped: %s", type(e).__name__)
        return False


def done_text(m: dict) -> str:
    return f"✅《{m.get('title') or '未命名'}》轉好了\n{BASE_URL}{m.get('id')}"


def failed_text(m: dict, error) -> str:
    return f"❌《{m.get('title') or '未命名'}》轉錄失敗：{error or '未知錯誤'}"


def _meeting(store: Store, job: dict):
    try:
        return store.get(job.get("meeting")) if job.get("meeting") else None
    except BadId:
        return None


def on_job_done(store: Store, job: dict, send=notify) -> bool:
    """Done listener: the pipeline summary finished -> meeting is done."""
    if job.get("kind") != "ai":
        return False
    m = _meeting(store, job)
    if m and m.get("aiJobId") == job.get("id") and m.get("status") == "done":
        send(done_text(m), store)
        return True
    return False


def on_job_terminal(store: Store, job: dict, state: str, send=notify) -> bool:
    """Terminal listener (register AFTER ai.on_ai_job_terminal so the meeting is already "done")."""
    if state != "failed":
        return False
    m = _meeting(store, job)
    if not m:
        return False
    if job.get("kind") == "transcribe" and m.get("jobId") == job.get("id"):
        send(failed_text(m, job.get("error")), store)
        return True
    if job.get("kind") == "ai" and m.get("aiJobId") == job.get("id") and m.get("status") == "done":
        send(done_text(m), store)  # summary failed permanently; transcript is usable
        return True
    return False


def _bg(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True, name="tg-notify").start()


def install(store: Store, jobs) -> None:
    """Hook the notifications onto a JobQueue; sends happen on a background thread."""
    jobs.add_done_listener(lambda job: _bg(on_job_done, store, job))
    jobs.add_terminal_listener(lambda job, state: _bg(on_job_terminal, store, job, state))
