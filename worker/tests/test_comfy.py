"""free_comfyui outcomes and VramGate's use of it (fake urlopen, injectable clock/sleep)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import comfy
import worker as W
from test_keepalive_retry import CFG, Session
from test_vram import Clock


class Resp:
    def __init__(self, body=b"{}", status=200):
        self._b, self.status = body, status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(queue, sent, post_status=200):
    def f(req, timeout=None):
        assert timeout is not None and timeout <= 5
        if isinstance(req, str):
            sent.append(("GET", req, None))
            return Resp(json.dumps(queue).encode())
        sent.append((req.get_method(), req.full_url, req.data))
        return Resp(status=post_status)
    return f


def test_freed_posts_exact_body_to_free():
    sent = []
    out = comfy.free_comfyui("http://127.0.0.1:8188", fake_urlopen(
        {"queue_running": [], "queue_pending": []}, sent))
    assert out == "freed"
    m, url, data = sent[-1]
    assert (m, url) == ("POST", "http://127.0.0.1:8188/free")
    assert json.loads(data) == {"unload_models": True, "free_memory": True}


@pytest.mark.parametrize("q", [{"queue_running": [[1]], "queue_pending": []},
                               {"queue_running": [], "queue_pending": [[2]]}])
def test_busy_sends_no_post(q):
    sent = []
    assert comfy.free_comfyui("http://x", fake_urlopen(q, sent)) == "busy"
    assert all(m == "GET" for m, _, _ in sent)


@pytest.mark.parametrize("q", [{}, {"queue_running": []}, {"queue_pending": []}, [],
                               {"queue_running": None, "queue_pending": None},
                               {"queue_running": [], "queue_pending": None},
                               {"error": "x"}])
def test_unclear_queue_answer_never_frees(q):
    sent = []
    assert comfy.free_comfyui("http://x", fake_urlopen(q, sent)) == "unreachable"
    assert all(m == "GET" for m, _, _ in sent)


def test_unreachable_never_raises():
    def boom(*a, **k):
        raise ConnectionRefusedError()
    assert comfy.free_comfyui("http://x", boom) == "unreachable"
    assert comfy.free_comfyui("http://x", lambda *a, **k: Resp(b"not json")) == "unreachable"


@pytest.mark.parametrize("v", ["", None])
def test_disabled(v):
    def boom(*a, **k):
        raise AssertionError("no network when disabled")
    assert comfy.free_comfyui(v, boom) == "disabled"


def test_url_from_config():
    assert comfy.url_from_config({}) == "http://127.0.0.1:8188"
    assert comfy.url_from_config({"comfyui": ""}) == ""
    assert comfy.url_from_config({"comfyui": False}) == ""
    assert comfy.url_from_config({"comfyui": "http://h:1/"}) == "http://h:1"
    assert comfy.url_from_config({"comfyui": "  http://h:1/ \n"}) == "http://h:1"
    assert comfy.url_from_config({"comfyui": "   "}) == ""
    assert comfy.url_from_config({"comfyui": None}) == ""
    assert comfy.url_from_config({"comfyui": True}) == "http://127.0.0.1:8188"


# ---------------------------------------------------------------- gate
def gate(outcomes, clock=None, s=None):
    s = s or Session()
    api = W.Api(CFG, s, "j1")
    clock = clock or Clock()
    calls = []

    def fc():
        calls.append(clock.t)
        return outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
    return W.VramGate(api, sleep=clock.sleep, clock=clock, free_comfy=fc), clock, calls, s


def test_freed_rechecks_without_retry_wait():
    g, clock, calls, s = gate(["freed"])
    g.wait("short")
    assert len(calls) == 1 and clock.sleeps == [W.VRAM_FREE_SETTLE_S]
    assert W.VRAM_RETRY_S not in clock.sleeps
    assert W.MSG_FREEING in [b["msg"] for e, b in s.posts if e == "progress"]


@pytest.mark.parametrize("res", ["busy", "unreachable", "disabled"])
def test_not_freed_waits_as_before(res):
    g, clock, calls, s = gate([res])
    g.wait("short")
    assert clock.sleeps == [W.VRAM_RETRY_S]
    assert W.MSG_WAIT in [b["msg"] for e, b in s.posts if e == "progress"]


def test_cooldown_120s():
    g, clock, calls, _ = gate(["busy"])
    g.wait()
    g.wait()  # 60 s later: inside the cooldown
    assert len(calls) == 1
    g.wait()  # 120 s after the first call
    assert len(calls) == 2


def test_cancel_during_settle_aborts():
    s = Session()

    class C(Clock):
        def sleep(self, sec):
            super().sleep(sec)
            s.script["progress"] = [409]
    g, clock, calls, _ = gate(["freed"], clock=C(), s=s)
    with pytest.raises(W.Cancelled):
        g.wait()
    assert clock.sleeps == [W.VRAM_FREE_SETTLE_S]


def test_run_whisper_short_then_freed_loads_without_60s_wait():
    import types
    free = iter([1000, 9000])
    asr = types.SimpleNamespace(free_vram_mb=lambda: next(free))
    ran = []
    runner = types.SimpleNamespace(whisper=lambda cb, **kw: ran.append(kw) or [], release=lambda: None)
    g, clock, calls, s = gate(["freed"])
    W.run_whisper(W.Api(CFG, s, "j1"), asr, runner, g)
    assert len(ran) == 1 and clock.sleeps == [W.VRAM_FREE_SETTLE_S]


def test_run_diar_short_then_freed():
    import types
    free = iter([100, 9000])
    asr = types.SimpleNamespace(free_vram_mb=lambda: next(free))
    runner = types.SimpleNamespace(diarize=lambda **kw: [])
    g, clock, calls, s = gate(["freed"])
    W.run_diar(W.Api(CFG, s, "j1"), asr, runner, g)
    assert clock.sleeps == [W.VRAM_FREE_SETTLE_S]
