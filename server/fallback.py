"""NAS CPU fallback transcriber: used when the GPU worker has been away and a job waited too long.

Heavy imports (numpy, faster-whisper, opencc) are lazy so the server and tests run without them.
Model cache location is controlled by the HF_HOME env var (set in the container).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .jobs import JobQueue, apply_transcript_result, update_meeting_if_job
from .storage import BadId, Store, read_json

log = logging.getLogger(__name__)

OWNER = "nas-cpu"
ENGINE = "nas-cpu"
SPEAKER = "說話者"
WORKER_STALE_S = 120
MIN_JOB_AGE_S = 60  # a job may be enqueued just before its meeting is saved
MEETING_WRITE_INTERVAL = 5.0
SAMPLE_RATE = 16000

# transcribe(paths, progress) -> {"segments", "durationSec", "engine"} | None (cancelled)
# progress(pct, msg) -> False when the job is no longer running (stop between segments).
Progress = Callable[[int, str], bool]
Transcriber = Callable[[list, Progress], "dict | None"]


# 5 min windows: bounded memory for 8 h recordings, and one window (~3-4 min of CPU at 1.5x
# realtime) stays inside the 600 s stale-requeue timeout even without the keepalive thread.
WINDOW_S = 5 * 60
OVERLAP_S = 2
KEEPALIVE_S = 60.0  # job heartbeat re-post interval while the transcriber is busy


def probe_duration(path: Path):
    """Seconds, or None when ffprobe ran fine but reports no duration (N/A: some streamed
    webm/ogg); the caller then decodes windows until ffmpeg returns no more audio."""
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("讀不到錄音長度：" + p.stderr.decode("utf-8", "replace")[-200:])
    try:
        d = float(p.stdout.decode().strip())
    except ValueError:
        return None
    return d if d > 0 else None


def decode_slice(path: Path, offset: float, length: float):
    """ffmpeg (-ss before -i) -> 16 kHz mono s16le -> float32 numpy for just this slice."""
    import numpy as np
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{offset:.3f}", "-i", str(path),
           "-t", f"{length:.3f}", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    if shutil.which("nice"):
        cmd = ["nice", "-n", "10"] + cmd
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg 解碼失敗：" + p.stderr.decode("utf-8", "replace")[-200:])
    audio = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)
    audio /= 32768.0
    return audio


_MODEL = None
_CC = None


def _model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
    return _MODEL


def _run_model(audio):
    """-> [(start_sec_in_window, text)] (Traditional Chinese)."""
    global _CC
    if _CC is None:
        from opencc import OpenCC
        _CC = OpenCC("s2twp")
    segs, _info = _model().transcribe(audio, language="zh", beam_size=1, vad_filter=True)
    # lazy: faster-whisper decodes as the generator is consumed, so a cancel can stop mid-window
    return ((float(sg.start), _CC.convert((sg.text or "").strip())) for sg in segs)


def transcribe_windows(paths: list, progress: Progress, probe=probe_duration, decode=decode_slice,
                       run_model=_run_model):
    """Window-by-window transcription. Files in order with a running offset; each window is decoded
    with OVERLAP_S extra seconds, and segments starting past the window length belong to the next one.

    A file whose probe gives None (duration N/A) is decoded window by window until ffmpeg returns
    no audio. `progress.cancelled` (optional callable) is also checked between segments."""
    cancelled = getattr(progress, "cancelled", None) or (lambda: False)
    if not progress(0, "NAS 備援轉錄準備中"):
        return None
    durs = [probe(Path(p)) for p in paths]
    known = all(d is not None for d in durs)
    total = sum(d for d in durs if d is not None)
    out, base, done = [], 0.0, 0.0
    for path, dur in zip(paths, durs):
        off = 0.0
        while dur is None or off < dur:
            length = WINDOW_S if dur is None else min(WINDOW_S, dur - off)
            audio = decode(Path(path), off, length + OVERLAP_S)
            if dur is None:
                got = len(audio) / SAMPLE_RATE
                if got <= 0:
                    break  # end of an unknown-length file
                length = min(WINDOW_S, got)
            for start, text in run_model(audio):
                if cancelled():
                    return None
                if start < WINDOW_S and text:
                    out.append({"speaker": SPEAKER, "text": text, "t": round(base + off + start, 1)})
            del audio
            off += length
            done += length
            if known and total > 0:
                pct = min(99, max(1, int(done / total * 100)))
                msg = f"NAS 備援轉錄中…{pct}%"
            else:  # unknown total: creep by minutes done
                pct = min(99, max(1, int(done / 600)))
                msg = f"NAS 備援轉錄中…已處理 {int(done // 60)} 分鐘"
            if not progress(pct, msg):
                return None
            if dur is None and length < WINDOW_S:
                break  # short read: that was the last window
        base += off if dur is None else dur
    return {"segments": out, "durationSec": round(total if known else done, 1), "engine": ENGINE}


def whisper_transcribe(paths: list, progress: Progress):
    return transcribe_windows(paths, progress)


class FallbackThread(threading.Thread):
    def __init__(self, store: Store, jobs: JobQueue, transcriber: Transcriber | None = None,
                 interval: float = 60.0, keepalive_s: float = KEEPALIVE_S):
        super().__init__(daemon=True, name="fallback")
        self.store, self.jobs = store, jobs
        self.transcriber = transcriber or whisper_transcribe
        self.interval = interval
        self.keepalive_s = keepalive_s
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def worker_age(self, now: float) -> float:
        w = read_json(self.store.path("jobs", "worker.json"), {})
        last = w.get("lastSeen") if isinstance(w, dict) else None
        return now - last if isinstance(last, (int, float)) else float("inf")

    def tick(self, now: float | None = None):
        """One decision round. Returns the processed job id, or None when nothing was claimed."""
        now = time.time() if now is None else now
        s = self.store.get_settings()
        if not s.get("fallbackEnabled"):
            return None
        if self.worker_age(now) <= WORKER_STALE_S:
            return None
        try:
            wait = max(float(s.get("fallbackMinutes") or 0) * 60, MIN_JOB_AGE_S)
        except (TypeError, ValueError):
            wait = 20 * 60.0

        def check(job):
            if now - (job.get("created") or now) < wait:
                return False
            try:
                m = self.store.get(job.get("meeting"))
            except BadId:
                return "會議不存在"
            if m is None:
                return "會議不存在"
            if m.get("transcript"):
                return False  # re-transcribing an existing transcript is GPU-only: leave it queued
            return True
        job = self.jobs.claim(["transcribe"], OWNER, check=check)
        if job is None:
            return None
        self.process(job)
        return job["id"]

    def process(self, job: dict) -> None:
        jid = job["id"]
        last_write = [0.0]
        last = {"pct": 0, "msg": "NAS 備援轉錄準備中"}
        gone = threading.Event()  # job cancelled / requeued / finished elsewhere
        stop_keepalive = threading.Event()

        def keepalive():
            # re-post the last pct/msg so a long window never looks stale to requeue_stale
            while not stop_keepalive.wait(self.keepalive_s):
                if self.jobs.progress(jid, "transcribe", last["pct"], last["msg"]) is None:
                    gone.set()
                    return

        def progress(pct, msg):
            last.update(pct=pct, msg=msg)
            if gone.is_set() or self.jobs.progress(jid, "transcribe", pct, msg) is None:
                gone.set()
                return False  # cancelled / finished elsewhere
            now = time.monotonic()
            if pct >= 100 or now - last_write[0] >= MEETING_WRITE_INTERVAL:
                last_write[0] = now
                update_meeting_if_job(self.store, job, lambda m: m.update(
                    status="transcribing",
                    progress={"stage": "transcribe", "pct": pct, "msg": msg, "engine": ENGINE}),
                    statuses=("queued", "transcribing"))
            return True
        progress.cancelled = gone.is_set
        ka = threading.Thread(target=keepalive, daemon=True, name=f"fallback-keepalive-{jid}")
        ka.start()
        try:
            progress(0, "NAS 備援轉錄準備中")  # first heartbeat right after claim
            m = self.store.get(job["meeting"]) or {}
            paths = [self.store.path("audio", job["meeting"], a["file"]) for a in m.get("audio") or []]
            if not paths:
                raise RuntimeError("這場會議沒有錄音檔")
            result = self.transcriber(paths, progress)
            if result is None:
                log.info("fallback job %s stopped (cancelled)", jid)
                return
            if not apply_transcript_result(self.store, self.jobs, job, result):
                log.info("fallback job %s ended elsewhere; result discarded", jid)
        except Exception as e:
            log.exception("fallback transcription failed for %s", jid)
            err = f"轉錄失敗：{e}"
            if self.jobs.fail(jid, err) is not None:
                update_meeting_if_job(self.store, job, lambda mm: mm.update(status="failed", error=err))
        finally:
            stop_keepalive.set()
            ka.join(timeout=5)

    def run(self) -> None:
        try:
            os.nice(10)  # on Linux this lowers only this thread (and the threads it spawns)
        except (AttributeError, OSError):
            pass
        while not self._halt.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("fallback tick failed")
