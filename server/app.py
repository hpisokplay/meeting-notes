from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from .ai import AIThread, build_ai_router, on_ai_job_terminal
from .importer import build_router as build_import_router
from .review import build_review_router
from .jobs import JobQueue, build_jobs_router, load_worker_key
from . import notify as tg_notify
from .storage import BadId, Store, read_json
from .uploads import build_router as build_uploads_router

VERSION = "v100"
REPO_ROOT = Path(__file__).resolve().parent.parent

STATIC_FILES = {"index.html", "sw.js", "manifest.webmanifest"}
STATIC_DIRS = {"css", "js", "icons"}
# owned by the server (job pipeline): a client PUT never changes them
SERVER_OWNED = ("status", "progress", "jobId", "aiJobId", "audio", "error",
                "transcriptRaw", "corrections", "review", "mode", "warnings")


def _guard(fn):
    """Bad meeting id (BadId from Store id validation) -> 400."""
    try:
        return fn()
    except BadId:
        raise HTTPException(400, "會議 id 不合法")


def mount_static(app: FastAPI) -> None:
    """Serve the PWA allowlist only. Routers must be included BEFORE calling this."""
    static_dir = Path(os.environ.get("MEETINGS_STATIC") or REPO_ROOT).resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def static(path: str):
        rel = unquote(path) or "index.html"
        if "\\" in rel or "\x00" in rel or ".." in rel.split("/"):
            raise HTTPException(404, "找不到")
        p = (static_dir / rel).resolve()
        try:
            parts = p.relative_to(static_dir).parts
        except ValueError:
            raise HTTPException(404, "找不到")
        ok = (len(parts) == 1 and parts[0] in STATIC_FILES) or \
             (len(parts) > 1 and parts[0] in STATIC_DIRS)
        if not ok or not p.is_file():
            raise HTTPException(404, "找不到")
        return FileResponse(p)


def _worker_status(store: Store) -> dict:
    w = read_json(store.path("jobs", "worker.json"), {})
    last = w.get("lastSeen") if isinstance(w, dict) else None
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
        m = _guard(lambda: store.get(mid))
        if m is None:
            raise HTTPException(404, "找不到這場會議")
        return m

    @r.put("/meetings/{mid}")
    def put_meeting(mid: str, body: dict = Body(...)):
        if body.get("id") != mid:
            raise HTTPException(400, "會議 id 與網址不一致")

        def save():
            with store._lock(mid):  # same lock as store.update: no lost job-state write
                stored = store.get(mid)
                if stored is not None:
                    for k in SERVER_OWNED:
                        if k in stored:
                            body[k] = stored[k]
                        else:
                            body.pop(k, None)
                return store.save(body)
        return _guard(save)

    @r.delete("/meetings/{mid}")
    def delete_meeting(mid: str):
        try:
            _guard(lambda: store.trash(mid))
        except KeyError:
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
               start_threads: bool = True, worker_key: str | None = None) -> FastAPI:
    app = FastAPI(title="DD會議紀錄")
    store = Store(Path(data_dir))
    app.state.store = store
    app.state.jobs = JobQueue(store)
    app.state.claude_cmd = list(claude_cmd) if claude_cmd else ["claude"]
    app.state.start_threads = start_threads

    @app.exception_handler(BadId)
    def _bad_id(request, exc):
        return JSONResponse({"detail": "會議 id 不合法"}, status_code=400)

    app.include_router(build_router(store))
    app.include_router(build_uploads_router(store, lambda: app.state.jobs))
    app.include_router(build_jobs_router(store, lambda: app.state.jobs,
                                         worker_key if worker_key is not None else load_worker_key()))
    app.include_router(build_ai_router(store, lambda: app.state.jobs))
    app.include_router(build_import_router(store))
    app.include_router(build_review_router(store, lambda: app.state.jobs))
    # cancelled / permanently failed pipeline summary -> meeting back to "done"
    if hasattr(app.state.jobs, "add_terminal_listener"):
        app.state.jobs.add_terminal_listener(lambda job, state: on_ai_job_terminal(store, job, state))
        tg_notify.install(store, app.state.jobs)  # after on_ai_job_terminal: meeting is already done

    if start_threads:
        def _stale_loop():
            while True:
                time.sleep(60)
                try:
                    app.state.jobs.requeue_stale()
                except Exception:
                    pass
        threading.Thread(target=_stale_loop, daemon=True, name="requeue-stale").start()
        app.state.ai_thread = AIThread(store, app.state.jobs, app.state.claude_cmd)
        app.state.ai_thread.start()

    mount_static(app)  # keep last: routers must be included before this line
    return app
