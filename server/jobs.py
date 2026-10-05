"""File-based job queue.

Layout: jobs/{queue,running,done,failed,cancelled}/<jobId>.json. A job's state is the
directory it lives in (queued/running/done/failed/cancelled); state moves are os.replace
under one process-wide lock. Cancelled jobs go to their own jobs/cancelled/ directory so
`failed/` only holds genuine failures.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response

from .storage import BadId, Store, atomic_write_json, read_json

log = logging.getLogger(__name__)

# state -> directory name
STATE_DIRS = {"queued": "queue", "running": "running", "done": "done",
              "failed": "failed", "cancelled": "cancelled"}
ACTIVE_STATES = ("queued", "running")
MAX_STALE_REQUEUES = 3
STALE_FAIL_MSG = "轉錄器多次中斷，請按「重新轉錄」再試一次"
STALE_AI_FAIL_MSG = "AI 工作多次中斷，請再試一次"


class _Skip(Exception):
    """Raised inside a store.update fn to abort the write (meeting no longer owned by the job)."""


def update_meeting_if_job(store: Store, job: dict, fn, statuses=None) -> bool:
    """store.update(meeting) only when meeting.jobId == job id (and status in `statuses`)."""
    def apply(m):
        if m.get("jobId") != job.get("id") or (statuses and m.get("status") not in statuses):
            raise _Skip()
        fn(m)
    try:
        store.update(job.get("meeting"), apply)
        return True
    except (_Skip, KeyError, BadId):
        return False
_LOCK = threading.RLock()  # process-wide: claim and every state move


class JobQueue:
    def __init__(self, store: Store):
        self.store = store
        self.queue_dir = store.path("jobs", "queue")
        self.dirs = {s: store.path("jobs", d) for s, d in STATE_DIRS.items()}
        self._listeners: list = []
        self._done_listeners: list = []

    def add_terminal_listener(self, fn) -> None:
        """fn(job, state) is called (outside the lock) after a job ends cancelled or failed
        (permanent failure only; a retry_at fail does not fire)."""
        self._listeners.append(fn)

    def add_done_listener(self, fn) -> None:
        """fn(job) is called (outside the lock) after finish() moved a job to done."""
        self._done_listeners.append(fn)

    def _notify_done(self, job) -> None:
        for fn in list(self._done_listeners):
            try:
                fn(job)
            except Exception:
                log.exception("done listener failed")

    def _notify(self, job, state) -> None:
        for fn in list(self._listeners):
            try:
                fn(job, state)
            except Exception:
                log.exception("terminal listener failed")

    def _new_id(self, kind: str) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + secrets.token_hex(3) + "-" + kind

    @staticmethod
    def _valid(jid) -> bool:
        return isinstance(jid, str) and bool(jid) and "/" not in jid and "\\" not in jid \
            and not jid.startswith(".")

    # ---- low-level
    def _find(self, jid):
        """(state, path) of the dir holding the job, or None."""
        if not self._valid(jid):
            return None
        for state, d in self.dirs.items():
            p = d / f"{jid}.json"
            if p.exists():
                return state, p
        return None

    def _load(self, jid):
        f = self._find(jid)
        if not f:
            return None, None
        job = read_json(f[1])
        return (job, f[1]) if isinstance(job, dict) else (None, None)

    def _move(self, job: dict, src: Path, state: str) -> dict:
        """Write the updated job next to its old file, then os.replace into the target dir."""
        job["state"] = state
        atomic_write_json(src, job)  # rewrite in place atomically, then move the file
        dst = self.dirs[state] / f"{job['id']}.json"
        if dst != src:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
        return job

    def _save(self, job: dict, path: Path) -> dict:
        atomic_write_json(path, job)
        return job

    # ---- public API
    def enqueue(self, kind: str, meeting_id: str, payload: dict) -> str:
        jid = self._new_id(kind)
        job = {"id": jid, "kind": kind, "meeting": meeting_id, "payload": payload,
               "state": "queued", "created": time.time(), "claimedAt": None, "owner": None,
               "heartbeat": None, "progress": {"stage": "", "pct": 0, "msg": ""},
               "error": None, "retries": 0, "notBefore": None}
        with _LOCK:
            atomic_write_json(self.dirs["queued"] / f"{jid}.json", job)
        return jid

    def read(self, jid):
        """Job dict from whichever state dir holds it (None if unknown / unreadable)."""
        return self.get(jid)

    def get(self, jid):
        job, _ = self._load(jid)
        return job

    def claim(self, kinds: list[str], owner: str, check=None):
        """Oldest queued job of one of `kinds` (notBefore in the future is skipped) -> running.

        check(job) may return True (claim it), False (leave it queued, try the next one) or a
        str (move the job to failed/ with that error, try the next one)."""
        check_failed = []
        try:
            return self._claim(kinds, owner, check, check_failed)
        finally:
            for j in check_failed:
                self._notify(j, "failed")

    def _claim(self, kinds, owner, check, check_failed):
        with _LOCK:
            now = time.time()
            cands = []
            for p in self.dirs["queued"].glob("*.json"):
                job = read_json(p)
                if not isinstance(job, dict) or job.get("kind") not in kinds:
                    continue
                nb = job.get("notBefore")
                if nb and nb > now:
                    continue
                cands.append((job.get("created") or 0, job.get("id") or "", job, p))
            cands.sort(key=lambda c: (c[0], c[1]))
            for _, _, job, p in cands:
                if check is not None:
                    verdict = check(job)
                    if isinstance(verdict, str):
                        job["error"] = verdict
                        check_failed.append(self._move(job, p, "failed"))
                        continue
                    if not verdict:
                        continue
                job.update(claimedAt=now, heartbeat=now, owner=owner, notBefore=None)
                return self._move(job, p, "running")
            return None

    def progress(self, jid, stage, pct, msg=""):
        """Update progress + heartbeat of a RUNNING job. Returns the job, or None if it is not running."""
        with _LOCK:
            job, path = self._load(jid)
            if job is None or job.get("state") != "running":
                return None
            job["progress"] = {"stage": stage, "pct": pct, "msg": msg}
            job["heartbeat"] = time.time()
            return self._save(job, path)

    def finish(self, jid, result=None):
        with _LOCK:
            job, path = self._load(jid)
            if job is None or job.get("state") not in ACTIVE_STATES:
                return None
            if result is not None:
                job["result"] = result
            job["error"] = None
            job = self._move(job, path, "done")
        self._notify_done(job)
        return job

    def fail(self, jid, error, retry_at: float | None = None):
        """retry_at -> back to queue hidden until then (retries+1); else -> failed/."""
        with _LOCK:
            job, path = self._load(jid)
            if job is None or job.get("state") not in ACTIVE_STATES:
                return None
            job["error"] = str(error)
            if retry_at is not None:
                job.update(retries=(job.get("retries") or 0) + 1, notBefore=retry_at,
                           owner=None, claimedAt=None, heartbeat=None)
                return self._move(job, path, "queued")
            job = self._move(job, path, "failed")
        self._notify(job, "failed")
        return job

    def cancel(self, jid, notify: bool = True):
        """queued/running -> cancelled/. Returns the job, or None if unknown or already finished."""
        with _LOCK:
            job, path = self._load(jid)
            if job is None or job.get("state") not in ACTIVE_STATES:
                return None
            job = self._move(job, path, "cancelled")
        if notify:
            self._notify(job, "cancelled")
        return job

    def requeue_stale(self, timeout_s: float = 600) -> list[str]:
        """Running jobs whose heartbeat (else claimedAt) is older than timeout_s go back to the queue
        (retries+1). A transcribe job already requeued MAX_STALE_REQUEUES times is failed instead and
        its meeting marked failed; otherwise the meeting is reset to "queued"."""
        out, dead, requeued = [], [], []
        with _LOCK:
            now = time.time()
            for p in list(self.dirs["running"].glob("*.json")):
                job = read_json(p)
                if not isinstance(job, dict):
                    continue
                last = max(job.get("heartbeat") or 0, job.get("claimedAt") or 0)
                if last and now - last <= timeout_s:
                    continue
                if job.get("kind") in ("transcribe", "ai") and (job.get("retries") or 0) >= MAX_STALE_REQUEUES:
                    job["error"] = STALE_FAIL_MSG if job.get("kind") == "transcribe" else STALE_AI_FAIL_MSG
                    dead.append(self._move(job, p, "failed"))
                    continue
                job.update(retries=(job.get("retries") or 0) + 1, owner=None, claimedAt=None,
                           heartbeat=None)
                requeued.append(self._move(job, p, "queued"))
                out.append(job["id"])
        for job in requeued:
            if job.get("kind") == "transcribe":
                update_meeting_if_job(self.store, job, lambda m: m.update(
                    status="queued", progress={"stage": "queued", "pct": 0, "msg": "", "engine": ""}),
                    statuses=("queued", "transcribing"))
        for job in dead:
            update_meeting_if_job(self.store, job, lambda m: m.update(status="failed", error=job["error"]),
                                  statuses=("queued", "transcribing"))
            self._notify(job, "failed")
        return out

    def queued_age(self, jid) -> float:
        """Seconds a queued job has been waiting (0 if not queued)."""
        job = self.get(jid)
        if not job or job.get("state") != "queued":
            return 0.0
        return max(0.0, time.time() - (job.get("created") or time.time()))


def apply_transcript_result(store: Store, jobs: JobQueue, job: dict, result: dict) -> bool:
    """Shared by the worker result endpoint and the NAS CPU fallback.

    The transcribe job is finished FIRST (under the queue lock); only if that succeeds (job still
    active, i.e. not cancelled / not already resulted) are the meeting changes applied and the "ai"
    summary job enqueued (stored as meeting.aiJobId). Returns False when the job was already ended.
    If the meeting was deleted (trashed) meanwhile, the job is just finished.
    """
    if jobs.finish(job["id"]) is None:
        return False
    mid = job.get("meeting")
    engine = result.get("engine") or ""
    segments = result.get("segments") or []
    dur = result.get("durationSec")
    ai_jid = None
    try:
        if mid and store.get(mid) is not None:
            ai_jid = jobs.enqueue("ai", mid, {"action": "summary"})

            def apply(m):
                m["transcript"] = segments
                if dur is not None:
                    m["durationSec"] = dur
                m["status"] = "summarizing"
                m["progress"] = {"stage": "summarizing", "pct": 0, "msg": "", "engine": engine}
                m["error"] = None
                m["aiJobId"] = ai_jid
            if not update_meeting_if_job(store, job, apply):
                jobs.cancel(ai_jid, notify=False)
    except BaseException:
        if ai_jid:
            jobs.cancel(ai_jid, notify=False)
        raise
    return True


# ---------------------------------------------------------------- HTTP API
WORKER_OWNER = "gpu-worker"
MEETING_WRITE_INTERVAL = 5.0  # seconds between meeting.json progress writes (pct 100 exempt)
NEW_MEETING_GRACE = 60.0  # a transcribe job may be enqueued just before its meeting is saved


def load_worker_key(path=None):
    """Read the worker key once; None when the file is missing/empty."""
    path = path or os.environ.get("MEETINGS_WORKER_KEY_FILE") or "/run/meetings/worker.key"
    try:
        key = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


def build_jobs_router(store: Store, get_jobs, worker_key) -> APIRouter:
    r = APIRouter(prefix="/api")
    last_write: dict[str, float] = {}

    def auth(x_worker_key: str | None = Header(default=None)):
        if not worker_key:
            raise HTTPException(503, "轉錄器金鑰未設定")
        if not x_worker_key or not hmac.compare_digest(x_worker_key.encode("utf-8"),
                                                        worker_key.encode("utf-8")):
            raise HTTPException(401, "轉錄器金鑰錯誤")

    def owned_running(jid):
        job = get_jobs().get(jid)
        if job is None:
            raise HTTPException(404, "找不到這個工作")
        if job.get("state") != "running" or job.get("owner") != WORKER_OWNER:
            raise HTTPException(409, "工作已取消或不再由轉錄器處理")
        return job

    def check_meeting_exists(job):
        if job.get("kind") != "transcribe":
            return True
        try:
            if store.get(job.get("meeting")) is not None:
                return True
        except BadId:
            return "會議不存在"
        if time.time() - (job.get("created") or 0) < NEW_MEETING_GRACE:
            return False  # meeting json may not be saved yet: leave queued
        return "會議不存在"

    @r.get("/worker/next", dependencies=[Depends(auth)])
    def worker_next():
        atomic_write_json(store.path("jobs", "worker.json"), {"lastSeen": time.time()})
        job = get_jobs().claim(["transcribe"], WORKER_OWNER, check=check_meeting_exists)
        if job is None:
            return Response(status_code=204)
        m = store.get(job["meeting"]) or {}
        audio = []
        for n, a in enumerate(m.get("audio") or []):
            audio.append({"url": f"/api/meetings/{job['meeting']}/audio/{n}",
                          "name": a.get("name"), "size": a.get("size")})
        return {"job": {"id": job["id"], "meeting": job["meeting"], "kind": job["kind"],
                        "payload": job.get("payload") or {}}, "audio": audio}

    @r.post("/worker/jobs/{jid}/progress", dependencies=[Depends(auth)])
    def worker_progress(jid: str, body: dict = Body(...)):
        owned_running(jid)
        stage = str(body.get("stage") or "")
        try:
            pct = max(0, min(100, int(body.get("pct") or 0)))
        except (TypeError, ValueError):
            pct = 0
        msg = str(body.get("msg") or "")
        job = get_jobs().progress(jid, stage, pct, msg)
        if job is None:
            raise HTTPException(409, "工作已取消或不再由轉錄器處理")
        mid = job.get("meeting")
        now = time.monotonic()
        if pct >= 100 or now - last_write.get(mid, -1e9) >= MEETING_WRITE_INTERVAL:
            last_write[mid] = now
            for k in [k for k, t in list(last_write.items()) if now - t > 3600]:
                del last_write[k]

            def apply(m):
                m["status"] = "transcribing"
                m["progress"] = {"stage": stage, "pct": pct, "msg": msg, "engine": "gpu"}
            update_meeting_if_job(store, job, apply, statuses=("queued", "transcribing"))
        return {"ok": True}

    @r.post("/worker/jobs/{jid}/result", dependencies=[Depends(auth)])
    def worker_result(jid: str, body: dict = Body(...)):
        job = owned_running(jid)
        segs = body.get("segments")
        if not isinstance(segs, list) or not all(isinstance(s, dict) for s in segs):
            raise HTTPException(400, "segments 格式不正確")
        if not apply_transcript_result(store, get_jobs(), job, body):
            raise HTTPException(409, "工作已取消或已完成")
        return {"ok": True}

    @r.post("/worker/jobs/{jid}/fail", dependencies=[Depends(auth)])
    def worker_fail(jid: str, body: dict = Body(...)):
        job = owned_running(jid)
        err = str(body.get("error") or "轉錄失敗")
        if get_jobs().fail(jid, err) is None:
            raise HTTPException(409, "工作已取消或已完成")

        def apply(m):
            m["status"] = "failed"
            m["error"] = err
        update_meeting_if_job(store, job, apply)
        return {"ok": True}

    @r.get("/jobs/{jid}")
    def job_status(jid: str):
        job = get_jobs().get(jid)
        if job is None:
            raise HTTPException(404, "找不到這個工作")
        return {"id": job["id"], "kind": job.get("kind"), "meeting": job.get("meeting"),
                "state": job.get("state"), "progress": job.get("progress"),
                "error": job.get("error")}

    @r.post("/jobs/{jid}/cancel")
    def job_cancel(jid: str):
        jobs = get_jobs()
        if jobs.get(jid) is None:
            raise HTTPException(404, "找不到這個工作")
        job = jobs.cancel(jid)
        if job is None:
            raise HTTPException(409, "工作已結束，無法取消")
        if job.get("kind") == "transcribe":
            def apply(m):
                m["status"] = "failed"
                m["error"] = "已取消"
            update_meeting_if_job(store, job, apply)
        return {"ok": True, "state": "cancelled"}

    return r
