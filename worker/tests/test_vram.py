"""GPU memory shared with other programs (ComfyUI): wait / degrade / give up instead of failing at once."""
import functools
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import worker as W
from test_keepalive_retry import CFG, JOB, Session, _no_keepalive_threads

OOM = "CUDA failed with error out of memory"


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _fake(monkeypatch, transcribe, diarize=None, free=lambda: None):
    np = pytest.importorskip("numpy")
    calls = {"release": 0, "asr": [], "diar": []}

    def tw(wave, cb, **kw):
        calls["asr"].append(kw)
        out = transcribe(kw)
        cb(1.0)
        return out

    def rel():
        calls["release"] += 1

    def di(wave, **kw):
        calls["diar"].append(kw)
        return diarize(kw) if diarize else []

    asr = types.SimpleNamespace(load_audio=lambda p: np.zeros(16000, dtype="float32"),
                                transcribe_words=tw, release=rel, free_vram_mb=free)
    diar = types.SimpleNamespace(diarize=di)
    opencc = types.SimpleNamespace(OpenCC=lambda cfg: types.SimpleNamespace(convert=lambda t: t))
    pipeline = types.SimpleNamespace(PROMPT="", align_words=lambda w, t: [], build_segments=lambda x: [],
                                     filter_hallucinations=lambda s, p: s, name_speakers=lambda s: s,
                                     fix_traditional=lambda t: t, smooth_speakers=lambda s: s)
    for name, mod in (("asr", asr), ("diar", diar), ("opencc", opencc), ("pipeline", pipeline)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(W, "download", lambda api, item, dest: None)
    monkeypatch.setattr(W, "KEEPALIVE_S", 0.01)
    monkeypatch.setattr(W, "Runner", W.InProcessRunner)
    return calls


def _run(monkeypatch, tmp_path, clock, s=None):
    monkeypatch.setattr(W, "BASE", tmp_path)
    s = s or Session(jobs=[JOB])
    proc = functools.partial(W.gpu_process, sleep=clock.sleep, clock=clock)
    w = W.Worker(CFG, s, proc)
    assert w.run_once() is True
    assert _no_keepalive_threads()
    return s


def _msgs(s, stage):
    return [b["msg"] for e, b in s.posts if e == "progress" and b["stage"] == stage]


def test_oom_on_first_load_waits_then_completes(monkeypatch, tmp_path):
    n = {"i": 0}

    def transcribe(kw):
        n["i"] += 1
        if n["i"] <= 2:  # 1st: retried at once in a fresh process; 2nd: wait
            raise RuntimeError(OOM)
        return []
    calls = _fake(monkeypatch, transcribe)
    clock = Clock()
    s = _run(monkeypatch, tmp_path, clock)
    assert s.count("result") == 1 and s.count("fail") == 0
    assert W.MSG_WAIT in _msgs(s, "wait")
    assert calls["release"] >= 1 and clock.sleeps[0] == W.VRAM_RETRY_S
    assert calls["asr"][-1].get("compute_type", "float16") == "float16"


def test_insufficient_free_vram_waits_before_loading(monkeypatch, tmp_path):
    free = iter([1000, 1000, 8000, 8000, 8000, 8000])
    calls = _fake(monkeypatch, lambda kw: [], free=lambda: next(free))
    clock = Clock()
    s = _run(monkeypatch, tmp_path, clock)
    assert len(calls["asr"]) == 1  # never tried to load while VRAM was short
    assert len(clock.sleeps) == 2 and W.MSG_WAIT in _msgs(s, "wait")
    assert s.count("result") == 1


def test_degrades_to_int8_batch4_then_2_after_30_min(monkeypatch, tmp_path):
    def transcribe(kw):
        if kw.get("compute_type") == "int8_float16" and kw.get("batch_size") == 2:
            return []
        raise RuntimeError(OOM)
    calls = _fake(monkeypatch, transcribe)
    clock = Clock()
    s = _run(monkeypatch, tmp_path, clock)
    assert s.count("result") == 1 and s.count("fail") == 0
    degraded = [kw for kw in calls["asr"] if kw.get("compute_type") == "int8_float16"]
    assert [kw["batch_size"] for kw in degraded] == [4, 2]
    first_degraded = calls["asr"].index(degraded[0])
    assert all(kw.get("compute_type", "float16") == "float16" for kw in calls["asr"][:first_degraded])
    assert first_degraded >= W.VRAM_DEGRADE_S // W.VRAM_RETRY_S
    assert any(m == W.MSG_LOWMEM for e, b in s.posts if e == "progress" for m in [b["msg"]])


def test_pyannote_oom_falls_back_to_cpu_after_30_min(monkeypatch, tmp_path):
    def diarize(kw):
        if kw.get("device", "cuda") == "cpu":
            return []
        raise RuntimeError("CUDA out of memory. Tried to allocate 1.00 GiB")
    calls = _fake(monkeypatch, lambda kw: [], diarize)
    clock = Clock()
    s = _run(monkeypatch, tmp_path, clock)
    assert s.count("result") == 1 and s.count("fail") == 0
    assert calls["diar"][-1] == {"device": "cpu"}
    assert sum(clock.sleeps) >= W.VRAM_DEGRADE_S
    assert W.MSG_DIAR_CPU in _msgs(s, "diar")


def test_pyannote_waits_for_free_vram(monkeypatch, tmp_path):
    seq = iter([8000, 500, 500, 8000])  # whisper ok, then short for pyannote twice
    calls = _fake(monkeypatch, lambda kw: [], free=lambda: next(seq, 8000))
    clock = Clock()
    s = _run(monkeypatch, tmp_path, clock)
    assert s.count("result") == 1 and len(calls["diar"]) == 1 and calls["diar"][0] == {}
    assert len(clock.sleeps) == 2


def test_gives_up_after_90_min_with_chinese_error(monkeypatch, tmp_path):
    def transcribe(kw):
        raise RuntimeError(OOM)
    _fake(monkeypatch, transcribe)
    clock = Clock()
    s = _run(monkeypatch, tmp_path, clock)
    assert s.count("result") == 0
    err = [b for e, b in s.posts if e == "fail"][-1]["error"]
    assert err == W.MSG_GIVEUP
    assert "CUDA" not in err and "out of memory" not in err
    assert W.VRAM_GIVEUP_S <= clock.t <= W.VRAM_GIVEUP_S + 2 * W.VRAM_RETRY_S


def test_cancel_during_wait_aborts(monkeypatch, tmp_path):
    def transcribe(kw):
        raise RuntimeError(OOM)
    _fake(monkeypatch, transcribe)
    s = Session(jobs=[JOB])

    class CancelClock(Clock):
        def sleep(self, sec):
            super().sleep(sec)
            s.script["progress"] = [409]  # user cancelled while we waited
    clock = CancelClock()
    _run(monkeypatch, tmp_path, clock, s)
    assert s.count("result") == 0 and s.count("fail") == 0
    assert len(clock.sleeps) == 1


def test_cancel_flag_interrupts_default_wait(monkeypatch):
    """Without an injected sleep the wait returns as soon as the keepalive saw a 409."""
    s = Session()
    api = W.Api(CFG, s, "j1")
    api.cancelled.set()
    gate = W.VramGate(api, clock=lambda: 0.0)
    with pytest.raises(W.Cancelled):
        gate.wait()  # would block 60 s if it ignored the flag


def test_other_cuda_error_reported_in_chinese(monkeypatch, tmp_path):
    def transcribe(kw):
        raise RuntimeError("CUDA failed with error unspecified launch failure")
    _fake(monkeypatch, transcribe)
    s = _run(monkeypatch, tmp_path, Clock())
    err = [b for e, b in s.posts if e == "fail"][-1]["error"]
    assert err.startswith("轉錄失敗：") and "顯示卡" in err
    assert "CUDA failed" not in err and "launch failure" not in err


def test_is_cuda_oom():
    assert W.is_cuda_oom(RuntimeError(OOM))
    assert W.is_cuda_oom(RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB"))
    assert not W.is_cuda_oom(RuntimeError("ffmpeg 無法解碼音檔"))
    assert not W.is_cuda_oom(ValueError("x"))


# ---------------------------------------------------------------- asr / diar internals
def test_asr_reloads_model_when_compute_type_changes(monkeypatch):
    import asr
    made = []

    class WM:
        def __init__(self, name, device, compute_type):
            made.append(compute_type)

    class BP:
        def __init__(self, model):
            self.kw = None

        def transcribe(self, audio, **kw):
            BP.last = kw
            return iter([]), types.SimpleNamespace(duration=1.0)
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=WM, BatchedInferencePipeline=BP))
    monkeypatch.setattr(asr, "setup_cuda_dlls", lambda: None)
    monkeypatch.setattr(asr, "_model", None)
    monkeypatch.setattr(asr, "_pipe", None)
    asr.transcribe_words([0.0])
    assert made == ["float16"] and BP.last["batch_size"] == 8
    asr.transcribe_words([0.0], compute_type="int8_float16", batch_size=4)
    assert made == ["float16", "int8_float16"] and BP.last["batch_size"] == 4
    asr.release()


def test_asr_free_vram_uses_nvidia_smi(monkeypatch):
    import asr
    monkeypatch.setitem(sys.modules, "torch", None)  # import torch -> ImportError
    monkeypatch.setattr(asr.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=b"7421\n"))
    assert asr.free_vram_mb() == 7421
    monkeypatch.setattr(asr.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert asr.free_vram_mb() is None


def test_diar_oom_while_moving_to_gpu_is_not_masked(monkeypatch):
    import diar

    class Pipe:
        def to(self, dev):
            raise RuntimeError("CUDA out of memory")
    torch = types.SimpleNamespace(device=lambda d: d,
                                  cuda=types.SimpleNamespace(empty_cache=lambda: None,
                                                             is_available=lambda: True))
    pa = types.SimpleNamespace(Pipeline=types.SimpleNamespace(from_pretrained=lambda p: Pipe()))
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "pyannote", types.SimpleNamespace(audio=pa))
    monkeypatch.setitem(sys.modules, "pyannote.audio", pa)
    monkeypatch.setattr(diar, "setup_cuda_dlls", lambda: None)
    with pytest.raises(RuntimeError, match="out of memory"):
        diar.diarize([0.0])


def test_single_cuda_error_retried_at_once_in_fresh_process(monkeypatch, tmp_path):
    """Driver reset: the first CUDA error is retried immediately, no waiting."""
    n = {"i": 0}

    def transcribe(kw):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("CUDA failed with error unspecified launch failure")
        return []
    calls = _fake(monkeypatch, transcribe)
    clock = Clock()
    s = _run(monkeypatch, tmp_path, clock)
    assert s.count("result") == 1 and clock.sleeps == [] and len(calls["asr"]) == 2


# ---------------------------------------------------------------- real child processes (spawn)
import child_targets as CT  # noqa: E402


def _runner(tmp_path, target, s=None):
    np = pytest.importorskip("numpy")
    api = W.Api(CFG, s or Session(), "j1")
    return api, W.ChildRunner(api, np.zeros(10, dtype="float32"), tmp_path, target=target)


def test_child_runner_relays_progress_and_result(tmp_path):
    api, r = _runner(tmp_path, CT.ok)
    seen = []
    assert r.whisper(seen.append, compute_type="float16") == [[0.0, 1.0, "hi"]]
    assert seen == [0.5, 1.0]
    assert r.diarize(device="cpu") == {"task": "diar", "kw": {"device": "cpu"}, "n": 10}


def test_child_runner_error_message_reraised(tmp_path):
    api, r = _runner(tmp_path, CT.oom)
    with pytest.raises(RuntimeError) as ei:
        r.whisper(None)
    assert W.is_cuda_oom(ei.value)


def test_child_runner_crash_is_child_died(tmp_path):
    api, r = _runner(tmp_path, CT.crash)
    with pytest.raises(W.ChildDied):
        r.diarize()
    assert W.user_error(W.ChildDied("x")).startswith("轉錄失敗：顯示卡")


def test_child_runner_cancel_kills_child(tmp_path):
    api, r = _runner(tmp_path, CT.hang)
    import threading
    import time as _t
    threading.Timer(1.0, api.cancelled.set).start()
    t0 = _t.time()
    with pytest.raises(W.Cancelled):
        r.whisper(None)
    assert _t.time() - t0 < 10
    assert not [p for p in __import__("multiprocessing").active_children() if p.name.startswith("gpu-")]
