import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import worker as W


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._d = data

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http %d" % self.status_code)


class FakeSession:
    def __init__(self, jobs, progress_status=200):
        self.jobs = list(jobs)
        self.posts = []
        self.progress_status = progress_status

    def get(self, url, **kw):
        if url.endswith("/api/worker/next"):
            if self.jobs:
                return Resp(200, self.jobs.pop(0))
            return Resp(204)
        raise AssertionError(url)

    def post(self, url, json=None, **kw):
        self.posts.append((url, json, kw))
        if url.endswith("/progress"):
            return Resp(self.progress_status)
        return Resp(200, {"ok": True})


CFG = {"server": "http://nas", "key": "K", "poll": 10}
JOB = {"job": {"id": "j1", "meeting": "m1", "kind": "transcribe", "payload": {}},
       "audio": [{"url": "/api/meetings/m1/audio/0", "name": "a.m4a", "size": 3}]}


def test_loop_posts_fail_on_exception():
    s = FakeSession([JOB])

    def boom(api, job, audio):
        raise ValueError("音檔壞了")

    w = W.Worker(CFG, s, boom)
    assert w.run_once() is True
    url, body, kw = s.posts[-1]
    assert url == "http://nas/api/worker/jobs/j1/fail"
    assert body["error"] == "轉錄失敗：音檔壞了"  # Traditional Chinese, no exception class name
    assert kw["headers"]["X-Worker-Key"] == "K"
    assert w.run_once() is False  # 204 -> nothing


def test_result_posted_on_success():
    s = FakeSession([JOB])
    w = W.Worker(CFG, s, lambda api, job, audio: {"segments": [], "durationSec": 1.0, "engine": "gpu"})
    w.run_once()
    assert s.posts[-1][0].endswith("/jobs/j1/result")
    assert s.posts[-1][1]["engine"] == "gpu"


def test_cancel_409_aborts_without_fail():
    s = FakeSession([JOB], progress_status=409)

    def proc(api, job, audio):
        api.progress("asr", 10, "x")
        raise AssertionError("should not reach")

    w = W.Worker(CFG, s, proc)
    assert w.run_once() is True
    assert not any(p[0].endswith("/fail") or p[0].endswith("/result") for p in s.posts)
