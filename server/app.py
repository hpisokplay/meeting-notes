from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

from .jobs import JobQueue
from .storage import Store, read_json
from .uploads import build_router as build_uploads_router

VERSION = "v100"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _worker_status(store: Store) -> dict:
    w = read_json(store.path("jobs", "worker.json"), {}) or {}
    last = w.get("lastSeen")
    online = isinstance(last, (int, float)) and (time.time() - last) <= 60
    return {"online": bool(online), "lastSeen": last}


def build_router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/status")
    def status():
        q = store.path("jobs", "queue")
        queue = len(list(q.glob("*.json"))) if q.exists() else 0
        return {"version": VERSION, "worker": _worker_status(store), "queue": queue,
                "claudeModel": store.get_settings()["claudeModel"],
                "diskFreeGB": round(shutil.disk_usage(store.root).free / 1024**3, 1)}

    @r.get("/meetings")
    def list_meetings():
        return store.list_meetings()

    @r.get("/meetings/{mid}")
    def get_meeting(mid: str):
        m = store.get(mid)
        if m is None:
            raise HTTPException(404, "找不到這場會議")
        return m

    @r.put("/meetings/{mid}")
    def put_meeting(mid: str, body: dict = Body(...)):
        if body.get("id") != mid:
            raise HTTPException(400, "會議 id 與網址不一致")
        return store.save(body)

    @r.delete("/meetings/{mid}")
    def delete_meeting(mid: str):
        try:
            store.trash(mid)
        except (KeyError, ValueError):
            raise HTTPException(404, "找不到這場會議")
        return {"ok": True}

    @r.get("/groups")
    def get_groups():
        return store.get_groups()

    @r.put("/groups")
    def put_groups(body: list = Body(...)):
        store.set_groups(body)
        return {"ok": True}

    @r.get("/settings")
    def get_settings():
        return store.get_settings()

    @r.put("/settings")
    def put_settings(body: dict = Body(...)):
        return store.set_settings(body)

    return r


def create_app(data_dir: Path, *, claude_cmd: list[str] | None = None,
               start_threads: bool = True) -> FastAPI:
    app = FastAPI(title="DD會議紀錄")
    store = Store(Path(data_dir))
    app.state.store = store
    app.state.jobs = JobQueue(store)
    app.state.claude_cmd = claude_cmd
    app.state.start_threads = start_threads
    app.include_router(build_router(store))
    app.include_router(build_uploads_router(store, lambda: app.state.jobs))

    static_dir = Path(os.environ.get("MEETINGS_STATIC") or REPO_ROOT).resolve()

    # registered last so every /api route (including later routers) wins
    @app.get("/{path:path}", include_in_schema=False)
    def static(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "找不到")
        p = (static_dir / (path or "index.html")).resolve()
        if p.is_dir():
            p = p / "index.html"
        if static_dir not in p.parents or not p.is_file():
            raise HTTPException(404, "找不到")
        return FileResponse(p)

    return app
