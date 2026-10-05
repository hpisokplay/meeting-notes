"""Final whole-branch review fixes: C1, I4, I5, I7, M3, M4, M5."""
import json
import os
import shutil
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from server import fallback as F
from server import main as MAIN
from server import uploads as up
from server.app import create_app
from server.fallback import FallbackThread, transcribe_windows
from server.jobs import JobQueue
from server.storage import Store

KEY = "secret-key"
H = {"X-Worker-Key": KEY}


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _old_job(store, jobs, mid="m1", transcript=None):
    jid = jobs.enqueue("transcribe", mid, {})
    p = jobs.dirs["queued"] / f"{jid}.json"
    j = json.loads(p.read_text("utf-8"))
    j["created"] = time.time() - 3600
    p.write_text(json.dumps(j), "utf-8")
    store.save({"id": mid, "title": "t", "createdAt": 1, "status": "queued", "jobId": jid,
                "audio": [{"file": "0-a.m4a", "name": "a.m4a", "size": 1}],
                "transcript": transcript or []})
    return jid


@pytest.fixture
def env(tmp_path):
    s = Store(tmp_path)
    return s, JobQueue(s)


# ---------------------------------------------------------------- C1 keepalive / window size
def test_window_is_five_minutes():
    assert F.WINDOW_S == 5 * 60 and F.OVERLAP_S == 2


def test_keepalive_reposts_heartbeat_while_transcriber_is_busy(env):
    s, jobs = env
    jid = _old_job(s, jobs)
    seen = []

    def slow(paths, progress):
        assert progress(42, "NAS 備援轉錄中…42%")
        hb0 = jobs.get(jid)["heartbeat"]
        # no progress() calls from the transcriber now; only the keepalive thread may beat
        ok = _wait(lambda: jobs.get(jid)["heartbeat"] > hb0 + 0.05)
        seen.append(ok)
        seen.append(jobs.get(jid)["progress"])
        assert jobs.requeue_stale(timeout_s=0.5) == []  # still fresh
        return {"segments": [], "durationSec": 1.0, "engine": "nas-cpu"}
    FallbackThread(s, jobs, slow, keepalive_s=0.02).tick()
    assert seen[0] is True
    assert seen[1] == {"stage": "transcribe", "pct": 42, "msg": "NAS 備援轉錄中…42%"}
    assert jobs.get(jid)["state"] == "done"
    assert not [t for t in threading.enumerate() if t.name.startswith("fallback-keepalive")]


def test_keepalive_sets_cancel_flag_when_job_cancelled(env):
    s, jobs = env
    jid = _old_job(s, jobs)
    seen = {}

    def slow(paths, progress):
        jobs.cancel(jid)
        seen["flag"] = _wait(progress.cancelled)  # set by the keepalive thread, no progress() call
        return None
    FallbackThread(s, jobs, slow, keepalive_s=0.02).tick()
    assert seen["flag"] is True
    assert jobs.get(jid)["state"] == "cancelled" and s.get("m1")["transcript"] == []


def test_cancel_flag_checked_inside_window_segment_loop():
    W = F.WINDOW_S
    flag = {"v": False}
    consumed = []

    def model(audio):
        for i in range(10):
            consumed.append(i)
            if i == 2:
                flag["v"] = True
            yield (float(i), f"s{i}")

    def prog(p, m):
        return True
    prog.cancelled = lambda: flag["v"]
    r = transcribe_windows(["a"], prog, lambda p: W * 2, lambda p, o, l: b"x", model)
    assert r is None and len(consumed) == 3  # stopped mid-window, second window never decoded


# ---------------------------------------------------------------- M3 unknown duration
class _Arr:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


def test_unknown_duration_decodes_until_empty():
    W = F.WINDOW_S
    real = W * 2 + 100.0  # seconds of audio actually in the file
    calls = []

    def decode(p, off, length):
        calls.append((off, length))
        n = max(0.0, min(length, real - off))
        return _Arr(int(n * F.SAMPLE_RATE))

    def model(audio):
        return [(1.0, "x")] if len(audio) else []
    prog = []
    r = transcribe_windows(["a.webm"], lambda p, m: prog.append((p, m)) or True,
                           lambda p: None, decode, model)
    assert [c[0] for c in calls] == [0.0, W, 2 * W]  # third window is short -> done
    assert [s["t"] for s in r["segments"]] == [1.0, W + 1.0, 2 * W + 1.0]
    assert r["durationSec"] == real
    assert all(0 <= p <= 99 for p, _ in prog) and "分鐘" in prog[-1][1]


def test_unknown_duration_exact_multiple_stops_on_empty_and_offsets_next_file():
    W = F.WINDOW_S
    lens = {"a": float(W), "b": 50.0}
    calls = []

    def decode(p, off, length):
        calls.append((str(p), off))
        n = max(0.0, min(length, lens[str(p)] - off))
        return _Arr(int(n * F.SAMPLE_RATE))

    r = transcribe_windows(["a", "b"], lambda p, m: True, lambda p: None if str(p) == "a" else 50.0,
                           decode, lambda a: [(2.0, "y")] if len(a) else [])
    assert calls == [("a", 0.0), ("a", float(W)), ("b", 0.0)]
    assert [s["t"] for s in r["segments"]] == [2.0, W + 2.0]


def test_probe_duration_na_returns_none(monkeypatch):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, b"N/A\n", b"")
    monkeypatch.setattr(F.subprocess, "run", run)
    assert F.probe_duration("x.webm") is None

    def bad(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, b"", b"No such file")
    monkeypatch.setattr(F.subprocess, "run", bad)
    with pytest.raises(RuntimeError):
        F.probe_duration("x.webm")


# ---------------------------------------------------------------- I7 GPU-only re-transcribe
def test_fallback_skips_retranscribe_of_existing_transcript(env):
    s, jobs = env
    jid = _old_job(s, jobs, transcript=[{"speaker": "A", "text": "舊的", "t": 0}])
    called = []
    ft = FallbackThread(s, jobs, lambda p, pr: called.append(1))
    assert ft.tick() is None
    assert jobs.get(jid)["state"] == "queued" and not called


# ---------------------------------------------------------------- I4 worker lastSeen
@pytest.fixture
def api(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, worker_key=KEY)
    return TestClient(app), app.state.store, app.state.jobs


def _last_seen(store):
    return json.loads(store.path("jobs", "worker.json").read_text("utf-8"))["lastSeen"]


@pytest.mark.parametrize("endpoint,body", [
    ("progress", {"stage": "diar", "pct": 0}),
    ("result", {"segments": []}),
    ("fail", {"error": "x"}),
])
def test_worker_calls_refresh_last_seen(api, endpoint, body):
    c, store, jobs = api
    jid = jobs.enqueue("transcribe", "m1", {})
    store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "queued", "jobId": jid,
                "audio": [{"file": "0-a.m4a", "name": "a.m4a", "size": 1}], "transcript": []})
    c.get("/api/worker/next", headers=H)
    store.path("jobs", "worker.json").write_text(json.dumps({"lastSeen": 1.0}), "utf-8")
    r = c.post(f"/api/worker/jobs/{jid}/{endpoint}", headers=H, json=body)
    assert r.status_code == 200
    assert _last_seen(store) > time.time() - 5


# ---------------------------------------------------------------- I5 extensions
def test_inbox_accepts_oga_and_new_exts(client, data_dir):
    store, jobs = client.app.state.store, client.app.state.jobs
    p = data_dir / "inbox" / "語音.oga"
    p.write_bytes(b"hello")
    old = time.time() - 60
    os.utime(p, (old, old))
    ids = up.scan_inbox(store, jobs)
    assert len(ids) == 1 and store.get(ids[0])["title"] == "語音"
    for ext in (".oga", ".opus", ".mov", ".m4v", ".3gp", ".amr", ".wma"):
        assert ext in up.AUDIO_EXTS and ext in up._MIME


# ---------------------------------------------------------------- M4 upload TTL sweep
def test_sweep_uploads_removes_only_abandoned(tmp_path):
    s = Store(tmp_path)
    now = time.time()
    old_uid, new_uid = "a" * 32, "b" * 32
    for uid in (old_uid, new_uid):
        d = s.path("uploads", uid)
        d.mkdir(parents=True)
        (d / "meta.json").write_text("{}", "utf-8")
        (d / "0.part").write_bytes(b"x")
    stale = now - 4 * 86400
    for f in list(s.path("uploads", old_uid).iterdir()) + [s.path("uploads", old_uid)]:
        os.utime(f, (stale, stale))
    s.path("uploads", "not-a-uid").mkdir()
    os.utime(s.path("uploads", "not-a-uid"), (stale, stale))
    assert up.sweep_uploads(s, now) == [old_uid]
    assert not s.path("uploads", old_uid).exists() and s.path("uploads", new_uid).exists()
    assert s.path("uploads", "not-a-uid").exists()


def test_inbox_tick_sweeps_at_most_hourly(tmp_path, monkeypatch):
    s = Store(tmp_path)
    calls = []
    monkeypatch.setattr(MAIN, "scan_inbox", lambda st, j: calls.append("scan"))
    monkeypatch.setattr(MAIN, "sweep_uploads", lambda st, now: calls.append("sweep") or [])
    last = MAIN._inbox_tick(s, None, 10_000.0, 0.0)
    assert last == 10_000.0 and calls == ["scan", "sweep"]
    last = MAIN._inbox_tick(s, None, 10_030.0, last)
    assert last == 10_000.0 and calls == ["scan", "sweep", "scan"]
    assert MAIN._inbox_tick(s, None, 10_000.0 + MAIN.UPLOAD_SWEEP_S, last) == 10_000.0 + MAIN.UPLOAD_SWEEP_S


# ---------------------------------------------------------------- M5 inbox rollback
def test_inbox_rollback_keeps_audio_dir_when_move_back_fails(client, data_dir, monkeypatch):
    store, jobs = client.app.state.store, client.app.state.jobs
    p = data_dir / "inbox" / "a.m4a"
    p.write_bytes(b"precious")
    old = time.time() - 60
    os.utime(p, (old, old))

    def boom(m):
        raise OSError("save failed")
    monkeypatch.setattr(store, "save", boom)
    real_move = shutil.move

    def move(src, dst):
        if str(dst).endswith(os.path.join("inbox", "a.m4a")):
            raise OSError("inbox read-only")
        return real_move(src, dst)
    monkeypatch.setattr(up.shutil, "move", move)
    assert up.scan_inbox(store, jobs) == []
    kept = list((data_dir / "audio").rglob("*a.m4a"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"precious"
    assert not list(jobs.dirs["queued"].glob("*.json"))
