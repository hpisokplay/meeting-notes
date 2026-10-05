"""C2 keepalive during silent stages, I6 result POST retry, I1 base path."""
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

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


class Session:
    """post(url) -> next item of script[endpoint] (int status or Exception); default 200."""

    def __init__(self, script=None, jobs=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.posts = []
        self.jobs = list(jobs or [])
        self.lock = threading.Lock()

    def get(self, url, **kw):
        if self.jobs:
            return Resp(200, self.jobs.pop(0))
        return Resp(204)

    def post(self, url, json=None, **kw):
        ep = url.rsplit("/", 1)[-1]
        with self.lock:
            self.posts.append((ep, json))
            q = self.script.get(ep)
            item = q.pop(0) if q and len(q) > 1 else (q[0] if q else 200)
        if isinstance(item, Exception):
            raise item
        return Resp(item, {"ok": True})

    def count(self, ep):
        with self.lock:
            return sum(1 for e, _ in self.posts if e == ep)


CFG = {"server": "http://nas", "key": "K"}


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _no_keepalive_threads():
    return _wait(lambda: not [t for t in threading.enumerate() if t.name == "keepalive"], 2)


# ---------------------------------------------------------------- I1
def test_base_paths_under_user_profile():
    assert W.BASE == Path.home() / "MeetingWorker"
    src = (Path(__file__).parents[1] / "diar.py").read_text(encoding="utf-8")
    assert 'Path.home() / "MeetingWorker"' in src and "LOCALAPPDATA" not in src
    ps = (Path(__file__).parents[1] / "install.ps1").read_text(encoding="utf-8")
    assert "Join-Path $env:USERPROFILE 'MeetingWorker'" in ps and "Stop-ScheduledTask" in ps


# ---------------------------------------------------------------- C2 keepalive
def test_keepalive_reposts_last_stage_unthrottled():
    s = Session()
    api = W.Api(CFG, s, "j1")
    api.progress("diar", 0, "")
    with W.Keepalive(api, interval=0.01):
        assert _wait(lambda: s.count("progress") >= 4)
    assert all(b == {"stage": "diar", "pct": 0, "msg": ""} for e, b in s.posts if e == "progress")
    assert _no_keepalive_threads()


def test_keepalive_409_sets_cancel_flag_and_survives_network_errors():
    s = Session({"progress": [ConnectionError("blip"), 409]})
    api = W.Api(CFG, s, "j1")
    with W.Keepalive(api, interval=0.01):
        assert _wait(api.cancelled.is_set)
    with pytest.raises(W.Cancelled):
        api.check()
    with pytest.raises(W.Cancelled):
        api.progress("upload", 0, "")  # no further posts once cancelled
    assert _no_keepalive_threads()


def _fake_modules(monkeypatch, diarize):
    np = pytest.importorskip("numpy")
    asr = types.SimpleNamespace(load_audio=lambda p: np.zeros(16000, dtype="float32"),
                                transcribe_words=lambda wave, cb: (cb(1.0), [])[1],
                                release=lambda: None)
    diar = types.SimpleNamespace(diarize=diarize)
    opencc = types.SimpleNamespace(OpenCC=lambda cfg: types.SimpleNamespace(convert=lambda t: t))
    pipeline = types.SimpleNamespace(PROMPT="", align_words=lambda w, t: [], build_segments=lambda x: [],
                                     filter_hallucinations=lambda s, p: s, name_speakers=lambda s: s)
    for name, mod in (("asr", asr), ("diar", diar), ("opencc", opencc), ("pipeline", pipeline)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(W, "download", lambda api, item, dest: None)
    monkeypatch.setattr(W, "KEEPALIVE_S", 0.01)


JOB = {"job": {"id": "j1", "meeting": "m1", "kind": "transcribe", "payload": {}},
       "audio": [{"url": "/api/meetings/m1/audio/0", "name": "a.m4a", "size": 3}]}


def test_gpu_process_beats_during_diarization(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "BASE", tmp_path)
    s = Session(jobs=[JOB])

    def diarize(wave):  # silent for a "long" time: only the keepalive posts
        n = s.count("progress")
        assert _wait(lambda: s.count("progress") >= n + 3)
        return []
    _fake_modules(monkeypatch, diarize)
    w = W.Worker(CFG, s)
    assert w.run_once() is True
    beats = [b for e, b in s.posts if e == "progress" and b["stage"] == "diar"]
    assert len(beats) >= 3
    assert s.count("result") == 1 and s.count("fail") == 0
    assert _no_keepalive_threads()


def test_gpu_process_409_during_diarization_discards_without_result(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "BASE", tmp_path)
    s = Session(jobs=[JOB])

    def diarize(wave):
        s.script["progress"] = [409]  # server requeued / cancelled the job meanwhile
        assert _wait(lambda: False, 0.2) is False
        return []
    _fake_modules(monkeypatch, diarize)
    w = W.Worker(CFG, s)
    assert w.run_once() is True
    assert s.count("result") == 0 and s.count("fail") == 0
    assert _no_keepalive_threads()


# ---------------------------------------------------------------- I6 result retry
def test_result_retries_network_errors_and_5xx_then_succeeds():
    s = Session({"result": [ConnectionError("reset"), 502, 503, 200]})
    slept = []
    W.Api(CFG, s, "j1").result({"segments": []}, sleep=slept.append)
    assert s.count("result") == 4 and slept == list(W.RESULT_BACKOFF[:3])


def test_result_409_stops_immediately():
    s = Session({"result": [409]})
    slept = []
    with pytest.raises(W.Cancelled):
        W.Api(CFG, s, "j1").result({"segments": []}, sleep=slept.append)
    assert s.count("result") == 1 and slept == []


def test_result_gives_up_after_five_tries_then_worker_fails_job(monkeypatch):
    s = Session({"result": [503]}, jobs=[JOB])
    slept = []
    monkeypatch.setattr(W.time, "sleep", slept.append)
    w = W.Worker(CFG, s, lambda api, job, audio: {"segments": [], "durationSec": 1.0, "engine": "gpu"})
    assert w.run_once() is True
    assert s.count("result") == 5 and len(slept) == 4 and sum(slept) == 300
    assert s.count("fail") == 1


def test_result_4xx_not_retried():
    s = Session({"result": [400]})
    with pytest.raises(RuntimeError):
        W.Api(CFG, s, "j1").result({"segments": []}, sleep=lambda x: None)
    assert s.count("result") == 1
