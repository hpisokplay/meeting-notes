"""File-based storage for meetings. Every write is atomic (.tmp + os.replace)."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from .render import meeting_to_docx, meeting_to_markdown

DEFAULT_SETTINGS = {"claudeModel": "sonnet", "fallbackEnabled": True,
                    "fallbackMinutes": 20, "tgNotify": True}
LIST_FIELDS = ("id", "title", "createdAt", "updatedAt", "group", "durationSec", "status", "progress")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{secrets.token_hex(3)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        for d in ("meetings", "exports", "trash", "audio", "uploads", "inbox"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._index_lock = threading.Lock()

    # ---- helpers
    def path(self, *parts) -> Path:
        return self.root.joinpath(*parts)

    def _lock(self, mid: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(mid, threading.RLock())

    def new_id(self) -> str:
        return f"{int(time.time() * 1000)}{secrets.token_hex(2)}"

    def _mpath(self, mid: str) -> Path:
        if not mid or "/" in mid or "\\" in mid or mid.startswith("."):
            raise ValueError("bad meeting id")
        return self.path("meetings", f"{mid}.json")

    # ---- meetings
    def get(self, mid: str) -> dict | None:
        try:
            return read_json(self._mpath(mid))
        except ValueError:
            return None

    def _all(self) -> list[dict]:
        out = []
        for p in self.path("meetings").glob("*.json"):
            m = read_json(p)
            if isinstance(m, dict):
                out.append(m)
        out.sort(key=lambda m: m.get("createdAt") or 0, reverse=True)
        return out

    def list_meetings(self) -> list[dict]:
        return [{k: m.get(k) for k in LIST_FIELDS} for m in self._all()]

    def _write_index(self) -> None:
        with self._index_lock:
            idx = [{"id": m.get("id"), "title": m.get("title"), "createdAt": m.get("createdAt"),
                    "group": m.get("group"), "durationSec": m.get("durationSec"),
                    "status": m.get("status")} for m in self._all()]
            atomic_write_json(self.path("index.json"), idx)

    def save(self, m: dict) -> dict:
        mid = m["id"]
        with self._lock(mid):
            m["updatedAt"] = int(time.time() * 1000)
            atomic_write_json(self._mpath(mid), m)
            atomic_write_bytes(self.path("meetings", f"{mid}.md"),
                               meeting_to_markdown(m).encode("utf-8"))
            docx_tmp = self.path("exports", f"{mid}.docx.{secrets.token_hex(3)}.tmp")
            try:
                meeting_to_docx(m, docx_tmp)
                os.replace(docx_tmp, self.path("exports", f"{mid}.docx"))
            finally:
                if docx_tmp.exists():
                    docx_tmp.unlink()
        self._write_index()
        return m

    def update(self, mid: str, fn: Callable[[dict], None]) -> dict:
        with self._lock(mid):
            m = self.get(mid)
            if m is None:
                raise KeyError(mid)
            fn(m)
            return self.save(m)

    def trash(self, mid: str) -> None:
        with self._lock(mid):
            src = self._mpath(mid)
            if not src.exists():
                raise KeyError(mid)
            dst = self.path("trash", f"{mid}.json")
            if dst.exists():
                dst = self.path("trash", f"{mid}.{int(time.time() * 1000)}.json")
            shutil.move(str(src), str(dst))
            # derived files are regenerated from the json, so only these two are dropped
            for extra in (self.path("meetings", f"{mid}.md"), self.path("exports", f"{mid}.docx")):
                extra.unlink(missing_ok=True)
        self._write_index()

    # ---- groups / settings
    def get_groups(self) -> list:
        return read_json(self.path("groups.json"), [])

    def set_groups(self, groups: list) -> None:
        atomic_write_json(self.path("groups.json"), groups)

    def get_settings(self) -> dict:
        s = dict(DEFAULT_SETTINGS)
        s.update(read_json(self.path("settings.json"), {}) or {})
        return s

    def set_settings(self, patch: dict) -> dict:
        s = self.get_settings()
        s.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
        atomic_write_json(self.path("settings.json"), s)
        return s
