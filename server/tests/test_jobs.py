import json
import time

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.jobs import JobQueue, apply_transcript_result
from server.storage import Store

KEY = "secret-key"
H = {"X-Worker-Key": KEY}


@pytest.fixture
def env(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, worker_key=KEY)
    return app, TestClient(app), app.state.store, app.state.jobs


def mk_meeting(store, jobs, mid="m1", status="queued"):
    jid = jobs.enqueue("transcribe", mid, {})
    store.save({"id": mid, "title": "t", "createdAt": 1, "status": status, "jobId": jid,
                "audio": [{"file": "0-a.m4a", "name": "a.m4a", "size": 5}], "transcript": []})
    return jid


def rewrite(path, **kw):
    j = json.loads(path.read_text(encoding="utf-8"))
    j.update(kw)
    path.write_text(json.dumps(j), encoding="utf-8")


def test_claim_oldest_first(tmp_path):
    q = JobQueue(Store(tmp_path))
    a = q.enqueue("transcribe", "m1", {})
    time.sleep(0.01)
    b = q.enqueue("transcribe", "m2", {})
    j = q.claim(["transcribe"], "w")
    assert j["id"] == a and j["state"] == "running" and j["owner"] == "w"
    assert j["claimedAt"] and j["heartbeat"]
    assert q.claim(["transcribe"], "w")["id"] == b
    assert q.claim(["transcribe"], "w") is None
    assert q.claim(["ai"], "w") is None


def test_worker_key_required(env):
    app, c, *_ = env
    assert c.get("/api/worker/next").status_code == 401
    assert c.get("/api/worker/next", headers={"X-Worker-Key": "bad"}).status_code == 401
    assert c.post("/api/worker/jobs/x/fail", json={"error": "e"}).status_code == 401


def test_missing_key_gives_503(data_dir, static_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    monkeypatch.setenv("MEETINGS_WORKER_KEY_FILE", str(tmp_path / "nokey"))
    c = TestClient(create_app(data_dir, start_threads=False))
    r = c.get("/api/worker/next", headers=H)
    assert r.status_code == 503 and "金鑰" in r.json()["detail"]


def test_key_file_loaded(data_dir, static_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    kf = tmp_path / "k"
    kf.write_text("abc\n", encoding="utf-8")
    monkeypatch.setenv("MEETINGS_WORKER_KEY_FILE", str(kf))
    c = TestClient(create_app(data_dir, start_threads=False))
    assert c.get("/api/worker/next", headers={"X-Worker-Key": "abc"}).status_code == 204


def test_next_returns_204_when_empty_and_updates_heartbeat(env):
    app, c, store, jobs = env
    assert c.get("/api/status").json()["worker"]["online"] is False
    assert c.get("/api/worker/next", headers=H).status_code == 204
    assert c.get("/api/status").json()["worker"]["online"] is True


def test_next_returns_job_and_audio(env):
    app, c, store, jobs = env
    jid = mk_meeting(store, jobs)
    d = c.get("/api/worker/next", headers=H).json()
    assert d["job"]["id"] == jid and d["job"]["meeting"] == "m1" and d["job"]["kind"] == "transcribe"
    assert d["audio"] == [{"url": "/api/meetings/m1/audio/0", "name": "a.m4a", "size": 5}]
    assert jobs.get(jid)["state"] == "running"


def test_next_skips_young_job_without_meeting_and_fails_old(env):
    app, c, store, jobs = env
    jid = jobs.enqueue("transcribe", "ghost", {})
    assert c.get("/api/worker/next", headers=H).status_code == 204
    assert jobs.get(jid)["state"] == "queued"
    rewrite(jobs.dirs["queued"] / f"{jid}.json", created=time.time() - 120)
    assert c.get("/api/worker/next", headers=H).status_code == 204
    g = jobs.get(jid)
    assert g["state"] == "failed" and g["error"] == "會議不存在"


def test_progress_updates_meeting_throttled_and_409_when_cancelled(env):
    app, c, store, jobs = env
    jid = mk_meeting(store, jobs)
    c.get("/api/worker/next", headers=H)
    r = c.post(f"/api/worker/jobs/{jid}/progress", headers=H, json={"stage": "asr", "pct": 10, "msg": "x"})
    assert r.status_code == 200
    m = store.get("m1")
    assert m["status"] == "transcribing"
    assert m["progress"] == {"stage": "asr", "pct": 10, "msg": "x", "engine": "gpu"}
    c.post(f"/api/worker/jobs/{jid}/progress", headers=H, json={"stage": "asr", "pct": 20, "msg": ""})
    assert store.get("m1")["progress"]["pct"] == 10  # throttled
    c.post(f"/api/worker/jobs/{jid}/progress", headers=H, json={"stage": "upload", "pct": 100, "msg": ""})
    assert store.get("m1")["progress"]["pct"] == 100
    assert jobs.get(jid)["progress"]["pct"] == 100
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200
    r = c.post(f"/api/worker/jobs/{jid}/progress", headers=H, json={"stage": "asr", "pct": 5, "msg": ""})
    assert r.status_code == 409
    assert c.post(f"/api/worker/jobs/{jid}/result", headers=H, json={"segments": []}).status_code == 409


def test_result_writes_transcript_and_enqueues_summary(env):
    app, c, store, jobs = env
    jid = mk_meeting(store, jobs)
    c.get("/api/worker/next", headers=H)
    segs = [{"speaker": "A", "text": "你好", "t": 1.5}]
    r = c.post(f"/api/worker/jobs/{jid}/result", headers=H,
               json={"segments": segs, "durationSec": 12.5, "engine": "gpu"})
    assert r.status_code == 200
    m = store.get("m1")
    assert m["transcript"] == segs and m["durationSec"] == 12.5
    assert m["status"] == "summarizing" and m["progress"]["stage"] == "summarizing"
    assert m["progress"]["engine"] == "gpu" and m["error"] is None
    assert jobs.get(jid)["state"] == "done"
    ai = jobs.get(m["aiJobId"])
    assert ai["kind"] == "ai" and ai["payload"] == {"action": "summary"} and ai["state"] == "queued"


def test_result_bad_body_400(env):
    app, c, store, jobs = env
    jid = mk_meeting(store, jobs)
    c.get("/api/worker/next", headers=H)
    assert c.post(f"/api/worker/jobs/{jid}/result", headers=H, json={"segments": "x"}).status_code == 400


def test_result_for_trashed_meeting_just_finishes(env):
    app, c, store, jobs = env
    jid = mk_meeting(store, jobs)
    c.get("/api/worker/next", headers=H)
    store.trash("m1")
    r = c.post(f"/api/worker/jobs/{jid}/result", headers=H, json={"segments": [], "engine": "gpu"})
    assert r.status_code == 200
    assert jobs.get(jid)["state"] == "done"
    assert not list(jobs.dirs["queued"].glob("*.json"))


def test_apply_transcript_result_direct(env):
    app, c, store, jobs = env
    mk_meeting(store, jobs)
    job = jobs.claim(["transcribe"], "nas")
    apply_transcript_result(store, jobs, job, {"segments": [], "durationSec": 1, "engine": "cpu"})
    assert store.get("m1")["progress"]["engine"] == "cpu"


def test_worker_fail_marks_meeting(env):
    app, c, store, jobs = env
    jid = mk_meeting(store, jobs)
    c.get("/api/worker/next", headers=H)
    assert c.post(f"/api/worker/jobs/{jid}/fail", headers=H, json={"error": "轉錄失敗：x"}).status_code == 200
    m = store.get("m1")
    assert m["status"] == "failed" and m["error"] == "轉錄失敗：x"
    assert jobs.get(jid)["state"] == "failed"
    assert (jobs.dirs["failed"] / f"{jid}.json").exists()


def test_stale_running_job_requeued(tmp_path):
    q = JobQueue(Store(tmp_path))
    jid = q.enqueue("transcribe", "m1", {})
    q.claim(["transcribe"], "w")
    old = time.time() - 660
    rewrite(q.dirs["running"] / f"{jid}.json", claimedAt=old, heartbeat=old)
    assert q.requeue_stale() == [jid]
    g = q.get(jid)
    assert g["state"] == "queued" and g["retries"] == 1 and g["owner"] is None
    assert (q.dirs["queued"] / f"{jid}.json").exists()


def test_fresh_running_job_not_requeued(tmp_path):
    q = JobQueue(Store(tmp_path))
    q.enqueue("transcribe", "m1", {})
    q.claim(["transcribe"], "w")
    assert q.requeue_stale() == []


def test_fail_with_retry_at_hidden_until_time(tmp_path):
    q = JobQueue(Store(tmp_path))
    jid = q.enqueue("transcribe", "m1", {})
    q.claim(["transcribe"], "w")
    q.fail(jid, "boom", retry_at=time.time() + 3600)
    assert q.get(jid)["state"] == "queued" and q.get(jid)["retries"] == 1
    assert q.claim(["transcribe"], "w") is None
    rewrite(q.dirs["queued"] / f"{jid}.json", notBefore=time.time() - 1)
    assert q.claim(["transcribe"], "w")["id"] == jid


def test_cancel_marks_meeting(env):
    app, c, store, jobs = env
    jid = mk_meeting(store, jobs)
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200
    m = store.get("m1")
    assert m["status"] == "failed" and m["error"] == "已取消"
    assert jobs.get(jid)["state"] == "cancelled"
    assert (jobs.dirs["cancelled"] / f"{jid}.json").exists()
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 409
    assert c.post("/api/jobs/nope/cancel").status_code == 404
    assert c.get(f"/api/jobs/{jid}").json()["state"] == "cancelled"


def test_queued_age(tmp_path):
    q = JobQueue(Store(tmp_path))
    jid = q.enqueue("transcribe", "m1", {})
    assert 0 <= q.queued_age(jid) < 5
