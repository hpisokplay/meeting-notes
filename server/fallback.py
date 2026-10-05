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


WINDOW_S = 20 * 60  # transcribe in 20 min windows: bounded memory for 8 h recordings
OVERLAP_S = 2


def probe_duration(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        d = float(p.stdout.decode().strip())
    except ValueError:
        d = 0.0
    if p.returncode != 0 or d <= 0:
        raise RuntimeError("讀不到錄音長度：" + p.stderr.decode("utf-8", "replace")[-200:])
    return d


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
    return [(float(sg.start), _CC.convert((sg.text or "").strip())) for sg in segs]


def transcribe_windows(paths: list, progress: Progress, probe=probe_duration, decode=decode_slice,
                       run_model=_run_model):
    """Window-by-window transcription. Files in order with a running offset; each window is decoded
    with OVERLAP_S extra seconds, and segments starting past the window length belong to the next one."""
    if not progress(0, "NAS 備援轉錄準備中"):
        return None
    durs = [probe(Path(p)) for p in paths]
    total = sum(durs)
    out, base, done = [], 0.0, 0.0
    for path, dur in zip(paths, durs):
        off = 0.0
        while off < dur:
            length = min(WINDOW_S, dur - off)
            audio = decode(Path(path), off, length + OVERLAP_S)
            for start, text in run_model(audio):
                if start < WINDOW_S and text:
                    out.append({"speaker": SPEAKER, "text": text, "t": round(base + off + start, 1)})
            del audio
            off += length
            done += length
            pct = min(99, max(1, int(done / total * 100)))
            if not progress(pct, f"NAS 備援轉錄中…{pct}%"):
                return None
        base += dur
    return {"segments": out, "durationSec": round(total, 1), "engine": ENGINE}


def whisper_transcribe(paths: list, progress: Progress):
    return transcribe_windows(paths, progress)


class FallbackThread(threading.Thread):
    def __init__(self, store: Store, jobs: JobQueue, transcriber: Transcriber | None = None,
                 interval: float = 60.0):
        super().__init__(daemon=True, name="fallback")
        self.store, self.jobs = store, jobs
        self.transcriber = transcriber or whisper_transcribe
        self.interval = interval
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
                return True if self.store.get(job.get("meeting")) is not None else "會議不存在"
            except BadId:
                return "會議不存在"
        job = self.jobs.claim(["transcribe"], OWNER, check=check)
        if job is None:
            return None
        self.process(job)
        return job["id"]

    def process(self, job: dict) -> None:
        jid = job["id"]
        last_write = [0.0]

        def progress(pct, msg):
            if self.jobs.progress(jid, "transcribe", pct, msg) is None:
                return False  # cancelled / finished elsewhere
            now = time.monotonic()
            if pct >= 100 or now - last_write[0] >= MEETING_WRITE_INTERVAL:
                last_write[0] = now
                update_meeting_if_job(self.store, job, lambda m: m.update(
                    status="transcribing",
                    progress={"stage": "transcribe", "pct": pct, "msg": msg, "engine": ENGINE}),
                    statuses=("queued", "transcribing"))
            return True
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
