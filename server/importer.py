"""Legacy (GitHub backup) import and export. Import only ever adds / merges: never deletes."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

from .storage import BadId, Store, read_json

log = logging.getLogger(__name__)

SERVER_OWNED = ("status", "progress", "jobId", "aiJobId", "audio", "error")
KEEP_IF_EITHER = ("notes", "alt", "translations")


def _ts(m: dict) -> float:
    v = m.get("editedAt") or m.get("updatedAt") or 0
    return v if isinstance(v, (int, float)) else 0


def _union_chat(a, b) -> list:
    out, seen = [], set()
    for c in list(a or []) + list(b or []):
        if not isinstance(c, dict):
            continue
        k = c.get("at")
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        out.append(c)
    out.sort(key=lambda c: c.get("at") if isinstance(c.get("at"), (int, float)) else 0)
    return out


def _merge_one(local: dict, inc: dict) -> tuple[dict, bool]:
    """Returns (merged, incoming_won)."""
    won = _ts(inc) > _ts(local)
    base, other = (dict(inc), local) if won else (dict(local), inc)
    if won:
        for k in SERVER_OWNED:  # job state belongs to this server
            if k in local:
                base[k] = local[k]
            else:
                base.pop(k, None)
    base.setdefault("status", "done")
    chat = _union_chat(local.get("chat"), inc.get("chat"))
    if chat:
        base["chat"] = chat
    for k in KEEP_IF_EITHER:
        if not base.get(k) and other.get(k):
            base[k] = other[k]
    return base, won


def merge_legacy(store: Store, data: dict) -> dict:
    report = {"added": 0, "updated": 0, "kept": 0, "groups": 0}
    if not isinstance(data, dict):
        return report
    for inc in data.get("meetings") or []:
        if not isinstance(inc, dict) or not isinstance(inc.get("id"), str):
            continue
        mid = inc["id"]
        try:
            with store._lock(mid):
                local = store.get(mid)
                if local is None:
                    m = dict(inc)
                    m.setdefault("status", "done")
                    store.save(m, touch=False)
                    report["added"] += 1
                    continue
                merged, won = _merge_one(local, inc)
                if merged != local:
                    store.save(merged, touch=False)
                report["updated" if won else "kept"] += 1
        except BadId:
            log.warning("import: skipped bad meeting id %r", mid)
    # legacy "deleted" / "deletedAt" / "groupsDeleted" are deliberately ignored
    groups = store.get_groups()
    have = {g.get("id") for g in groups if isinstance(g, dict)}
    for g in data.get("groups") or []:
        if isinstance(g, dict) and g.get("id") and g["id"] not in have:
            groups.append(g)
            have.add(g["id"])
            report["groups"] += 1
    if report["groups"]:
        store.set_groups(groups)
    return report


def import_dir(store: Store) -> list[str]:
    """Import every import/*.json under the data root, then move it to import/done/ (never delete)."""
    d = store.path("import")
    if not d.is_dir():
        return []
    done = d / "done"
    out = []
    for p in sorted(d.glob("*.json")):
        data = read_json(p)
        if not isinstance(data, dict):
            log.error("import: %s is not a valid backup, left in place", p)
            continue
        try:
            rep = merge_legacy(store, data)
        except Exception:
            log.exception("import failed for %s", p)
            continue
        done.mkdir(parents=True, exist_ok=True)
        dst = done / p.name
        if dst.exists():
            dst = done / f"{p.stem}.{int(time.time())}.json"
        shutil.move(str(p), str(dst))
        log.info("imported %s: %s", p.name, rep)
        out.append(p.name)
    return out


def build_router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/export")
    def export():
        return {"meetings": store._all(), "groups": store.get_groups(), "deleted": [],
                "exportedAt": int(time.time() * 1000)}

    @r.post("/import")
    def import_(body: dict = Body(...)):
        if not isinstance(body.get("meetings"), list):
            raise HTTPException(400, "備份格式不正確")
        return merge_legacy(store, body)

    return r
