"""Minimal file-based job queue. Task 3 extends this class (claim/progress/finish/fail)."""
from __future__ import annotations

import time
from datetime import datetime

from .storage import Store, atomic_write_json


class JobQueue:
    def __init__(self, store: Store):
        self.store = store
        self.queue_dir = store.path("jobs", "queue")

    def _new_id(self, kind: str) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + kind

    def enqueue(self, kind: str, meeting_id: str, payload: dict) -> str:
        jid = self._new_id(kind)
        job = {"id": jid, "kind": kind, "meeting": meeting_id, "payload": payload,
               "state": "queued", "created": time.time(), "claimedAt": None, "owner": None,
               "heartbeat": None, "progress": {"stage": "", "pct": 0, "msg": ""},
               "error": None, "retries": 0, "notBefore": None}
        atomic_write_json(self.queue_dir / f"{jid}.json", job)
        return jid
