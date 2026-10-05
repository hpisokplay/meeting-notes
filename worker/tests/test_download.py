import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asr
import worker as W

DATA = bytes(range(256)) * 40  # 10240 bytes


class R:
    def __init__(self, status, body=b"", cut=None):
        self.status_code = status
        self.body = body
        self.cut = cut

    def iter_content(self, n):
        body = self.body if self.cut is None else self.body[:self.cut]
        for i in range(0, len(body), 1000):
            yield body[i:i + 1000]
        if self.cut is not None and self.cut < 0:
            raise ConnectionError("boom")


class Sess:
    """Fake server. behaviours: list consumed per request: None | 'cut:N' | 'drop:N' (error after N bytes)."""

    def __init__(self, ignore_range=False, plan=()):
        self.ignore_range = ignore_range
        self.plan = list(plan)
        self.ranges = []

    def get(self, url, headers=None, stream=False, timeout=None):
        rng = (headers or {}).get("Range")
        self.ranges.append(rng)
        start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        if start >= len(DATA) and not self.ignore_range:
            return R(416)
        step = self.plan.pop(0) if self.plan else None
        if self.ignore_range or not start:
            status, body = 200, DATA
        else:
            status, body = 206, DATA[start:]
        if step and step.startswith("cut:"):
            return R(status, body, cut=int(step[4:]))
        if step and step.startswith("drop:"):
            r = R(status, body[:int(step[5:])])
            orig = r.iter_content

            def gen(n):
                yield from orig(n)
                raise ConnectionError("boom")
            r.iter_content = gen
            return r
        return R(status, body)


class A:
    server = "http://nas"
    headers = {"X-Worker-Key": "K"}

    def __init__(self, s):
        self.s = s
        self.pcts = []

    def progress(self, stage, pct, msg=""):
        self.pcts.append(pct)


ITEM = {"url": "/a/0", "name": "a.m4a", "size": len(DATA)}


@pytest.fixture(autouse=True)
def nosleep(monkeypatch):
    monkeypatch.setattr(W.time, "sleep", lambda s: None)


def test_fresh_download(tmp_path):
    s = Sess()
    d = tmp_path / "a.m4a"
    W.download(A(s), ITEM, d)
    assert d.read_bytes() == DATA and s.ranges == [None]


def test_resume_206_from_partial(tmp_path):
    d = tmp_path / "a.m4a"
    d.write_bytes(DATA[:3000])
    s = Sess()
    W.download(A(s), ITEM, d)
    assert d.read_bytes() == DATA and s.ranges == ["bytes=3000-"]


def test_server_ignores_range_restarts(tmp_path):
    d = tmp_path / "a.m4a"
    d.write_bytes(DATA[:3000])
    s = Sess(ignore_range=True)
    W.download(A(s), ITEM, d)
    assert d.read_bytes() == DATA


def test_short_read_then_resume(tmp_path):
    d = tmp_path / "a.m4a"
    s = Sess(plan=["cut:4000"])
    W.download(A(s), ITEM, d)
    assert d.read_bytes() == DATA
    assert s.ranges == [None, "bytes=4000-"]


def test_416_when_complete(tmp_path):
    d = tmp_path / "a.m4a"
    d.write_bytes(DATA)
    s = Sess()
    W.download(A(s), {**ITEM, "size": 0}, d)  # size unknown: 416 after non-empty partial = done
    assert d.read_bytes() == DATA and s.ranges == ["bytes=%d-" % len(DATA)]
    s2 = Sess()
    W.download(A(s2), ITEM, d)  # size known and equal: no request at all
    assert s2.ranges == []


def test_retry_budget_resets_when_bytes_gained(tmp_path):
    d = tmp_path / "a.m4a"
    # 6 consecutive drops, each gaining 1000 bytes, with tries=2: only OK if budget resets
    s = Sess(plan=["drop:1000"] * 6)
    W.download(A(s), ITEM, d, tries=2)
    assert d.read_bytes() == DATA


def test_gives_up_without_progress(tmp_path):
    d = tmp_path / "a.m4a"
    s = Sess(plan=["drop:0"] * 10)
    with pytest.raises(RuntimeError, match="下載錄音失敗"):
        W.download(A(s), ITEM, d, tries=3)
    assert len(s.ranges) == 3


def test_ffmpeg_missing_message(monkeypatch):
    def nf(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(asr.subprocess, "run", nf)
    with pytest.raises(RuntimeError, match="找不到 ffmpeg，請先安裝"):
        asr.load_audio("x.m4a")


def test_startup_failure_is_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "BASE", tmp_path)
    monkeypatch.setattr(W.log, "handlers", [])
    with pytest.raises(SystemExit):
        W.main()  # no config.json
    for h in W.log.handlers:
        h.flush()
    assert "startup failed" in (tmp_path / "worker.log").read_text(encoding="utf-8")
    for h in list(W.log.handlers):
        h.close()
        W.log.removeHandler(h)
