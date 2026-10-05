import json
import time

import pytest

from server.fallback import FallbackThread
from server.jobs import JobQueue
from server.storage import Store


def age_job(jobs, jid, age):
    p = jobs.dirs["queued"] / f"{jid}.json"
    j = json.loads(p.read_text("utf-8"))
    j["created"] = time.time() - age
    p.write_text(json.dumps(j), "utf-8")


def mk(store, jobs, age=0.0, mid="m1"):
    jid = jobs.enqueue("transcribe", mid, {})
    age_job(jobs, jid, age)
    store.save({"id": mid, "title": "t", "createdAt": 1, "status": "queued", "jobId": jid,
                "audio": [{"file": "0-a.m4a", "name": "a.m4a", "size": 1}], "transcript": []})
    return jid


def ok_transcriber(paths, progress):
    assert progress(50, "half")
    return {"segments": [{"speaker": "說話者", "text": "你好", "t": 0.0}], "durationSec": 12.0,
            "engine": "nas-cpu"}


@pytest.fixture
def env(tmp_path):
    s = Store(tmp_path)
    return s, JobQueue(s)


def test_fallback_claims_only_after_wait(env):
    s, jobs = env
    jid = mk(s, jobs, age=60 * 5)  # 5 min old, wait is 20 min
    ft = FallbackThread(s, jobs, ok_transcriber)
    assert ft.tick() is None and jobs.get(jid)["state"] == "queued"
    age_job(jobs, jid, 21 * 60)
    assert ft.tick() == jid
    m = s.get("m1")
    assert m["status"] == "summarizing" and m["transcript"][0]["text"] == "你好"
    assert m["durationSec"] == 12.0 and m["progress"]["engine"] == "nas-cpu"
    assert jobs.get(jid)["state"] == "done" and jobs.get(m["aiJobId"])["kind"] == "ai"


def test_fallback_respects_disabled_and_online_worker(env):
    s, jobs = env
    jid = mk(s, jobs, age=3600)
    ft = FallbackThread(s, jobs, ok_transcriber)
    s.set_settings({"fallbackEnabled": False})
    assert ft.tick() is None
    s.set_settings({"fallbackEnabled": True})
    (s.path("jobs") / "worker.json").write_text(json.dumps({"lastSeen": time.time() - 30}), "utf-8")
    assert ft.tick() is None
    (s.path("jobs") / "worker.json").write_text(json.dumps({"lastSeen": time.time() - 300}), "utf-8")
    assert ft.tick() == jid


def test_fallback_discards_result_when_cancelled_midway(env):
    s, jobs = env
    jid = mk(s, jobs, age=3600)

    def cancelling(paths, progress):
        jobs.cancel(jid)
        return ok_transcriber(paths, lambda *a: True)  # result arrives after the cancel
    FallbackThread(s, jobs, cancelling).tick()
    m = s.get("m1")
    assert m["transcript"] == [] and "aiJobId" not in m
    assert not list(jobs.dirs["queued"].glob("*.json"))


def test_fallback_stops_between_segments_when_job_gone(env):
    s, jobs = env
    jid = mk(s, jobs, age=3600)
    calls = []

    def t(paths, progress):
        jobs.cancel(jid)
        calls.append(progress(10, "x"))
        return None
    FallbackThread(s, jobs, t).tick()
    assert calls == [False] and s.get("m1")["transcript"] == []


def test_fallback_failure_marks_meeting_failed(env):
    s, jobs = env
    jid = mk(s, jobs, age=3600)

    def boom(paths, progress):
        raise RuntimeError("ffmpeg 壞了")
    FallbackThread(s, jobs, boom).tick()
    assert jobs.get(jid)["state"] == "failed"
    m = s.get("m1")
    assert m["status"] == "failed" and "ffmpeg 壞了" in m["error"]
