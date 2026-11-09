"""MeetingWorker: poll the NAS for transcribe jobs, run them on the local GPU, post segments back."""
import json
import logging
import logging.handlers
import multiprocessing
import os
import shutil
import threading
import time
import traceback
from pathlib import Path

import comfy

# %USERPROFILE%\MeetingWorker, not %LOCALAPPDATA%: writes to LOCALAPPDATA from an MSIX-packaged
# parent (e.g. the Claude desktop app) are virtualized into Packages\...\LocalCache.
BASE = Path.home() / "MeetingWorker"
TIMEOUT = 60
KEEPALIVE_S = 60  # re-post the last progress this often while a stage is silent (diar, decode, model download)
RESULT_BACKOFF = (15, 45, 90, 150)  # waits between the 5 result POST attempts (~5 min in total)
log = logging.getLogger("worker")

# The 16 GB card (RTX 5070 Ti) is shared with other programs (ComfyUI keeps its models in VRAM after
# generating, ~10 GB): first ask an idle ComfyUI to free it, else wait instead of failing, degrade
# after a while, give up only after a long time. The *_VRAM_MB values are absolute needs of the
# models, not shares of the card size.
WHISPER_VRAM_MB = 4500          # large-v3 float16 + batch 8 working memory
WHISPER_LOWMEM_VRAM_MB = 3000   # int8_float16, batch 4/2
PYANNOTE_VRAM_MB = 1500
VRAM_RETRY_S = 60
VRAM_DEGRADE_S = 30 * 60        # whisper -> int8_float16; pyannote -> CPU
VRAM_GIVEUP_S = 90 * 60
VRAM_FREE_COOLDOWN_S = 120      # ask ComfyUI to free memory at most this often
VRAM_FREE_SETTLE_S = 8          # after "freed", give the driver this long before re-checking
COMFY_URL = comfy.DEFAULT_URL   # main() overrides from config.json "comfyui" ("" / false = off)
MSG_WAIT = "等待顯示卡空出記憶體（被其他程式占用，例如 ComfyUI）"
MSG_FREEING = "請 ComfyUI 釋放顯示卡記憶體…"
MSG_LOWMEM = "顯示卡記憶體不足，改用省記憶體模式（較慢）"
MSG_DIAR_CPU = "分辨說話者改用 CPU（較慢）"
MSG_GIVEUP = "顯示卡記憶體一直不足（可能被其他程式占用），請關閉 ComfyUI 等程式後按「重新轉錄」"


class Cancelled(Exception):
    """Server answered 409 on progress: the job was cancelled."""


class GpuBusy(RuntimeError):
    """GPU memory stayed full for VRAM_GIVEUP_S; str() is the user-facing message."""


def is_cuda_oom(e):
    """ctranslate2 ("CUDA failed with error out of memory") and torch.OutOfMemoryError
    (a RuntimeError subclass, "CUDA out of memory. Tried to allocate ...")."""
    return isinstance(e, RuntimeError) and "out of memory" in str(e).lower()


class ChildDied(RuntimeError):
    """The GPU child process exited without an answer (crash, driver reset)."""


def is_gpu_error(e):
    """Worth one retry in a fresh child: CUDA errors and child crashes."""
    low = str(e).lower()
    return isinstance(e, ChildDied) or (isinstance(e, RuntimeError) and any(
        k in low for k in ("cuda", "cudnn", "cublas", "out of memory")))


def user_error(e):
    """Traditional Chinese fail message; the English detail stays in the log."""
    if isinstance(e, GpuBusy):
        return str(e)
    if is_cuda_oom(e):
        return MSG_GIVEUP
    low = str(e).lower()
    if isinstance(e, ChildDied):
        return "轉錄失敗：顯示卡程序異常結束，請稍後按「重新轉錄」再試"
    if any(k in low for k in ("cuda", "cudnn", "cublas", "nvidia")):
        return "轉錄失敗：顯示卡（CUDA）發生錯誤，請重新開機或稍後按「重新轉錄」再試"
    return "轉錄失敗：%s" % e


class Api:
    def __init__(self, cfg, session, job_id=None):
        self.server = cfg["server"].rstrip("/")
        self.key = cfg["key"]
        self.s = session
        self.job_id = job_id
        self._last = (None, 0.0)
        self._state = ("", 0, "")  # last (stage, pct, msg) for the keepalive thread
        self.cancelled = threading.Event()  # set when the server answered 409 (job gone)

    @property
    def headers(self):
        return {"X-Worker-Key": self.key}

    def next_job(self):
        r = self.s.get(self.server + "/api/worker/next", headers=self.headers, timeout=TIMEOUT)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    def _post(self, path, body):
        return self.s.post("%s/api/worker/jobs/%s/%s" % (self.server, self.job_id, path),
                           json=body, headers=self.headers, timeout=TIMEOUT)

    def check(self):
        """Raise Cancelled when a (keepalive) progress post was answered 409."""
        if self.cancelled.is_set():
            raise Cancelled()

    def progress(self, stage, pct, msg=""):
        self.check()
        self._state = (stage, int(pct), msg)
        now = time.time()
        if self._last[0] == stage and now - self._last[1] < 2.0 and pct < 100:
            return  # throttle
        self._last = (stage, now)
        r = self._post("progress", {"stage": stage, "pct": int(pct), "msg": msg})
        if r.status_code == 409:
            self.cancelled.set()
            raise Cancelled()

    def beat(self):
        """Keepalive: re-post the last stage/pct unthrottled. A 409 sets `cancelled`;
        network errors are only logged (the next beat retries)."""
        stage, pct, msg = self._state
        try:
            r = self._post("progress", {"stage": stage, "pct": pct, "msg": msg})
        except Exception as e:
            log.warning("keepalive progress failed: %s", e)
            return
        if r.status_code == 409:
            self.cancelled.set()

    def result(self, payload, sleep=None):
        """POST the result; retry network errors / 5xx with backoff, stop at once on 409
        (Cancelled) or another 4xx (raise)."""
        sleep = sleep or time.sleep
        last = None
        for attempt in range(len(RESULT_BACKOFF) + 1):
            if attempt:
                sleep(RESULT_BACKOFF[attempt - 1])
            try:
                r = self._post("result", payload)
            except Exception as e:  # connection reset, timeout, DNS ...
                last = e
                log.warning("result post attempt %d failed: %s", attempt + 1, e)
                continue
            if r.status_code == 409:
                raise Cancelled()
            if r.status_code >= 500:
                last = RuntimeError("上傳結果失敗 HTTP %d" % r.status_code)
                log.warning("result post attempt %d: HTTP %d", attempt + 1, r.status_code)
                continue
            r.raise_for_status()
            return
        raise RuntimeError("上傳結果失敗：%s" % last)

    def fail(self, error):
        self._post("fail", {"error": error[:2000]})

    def for_job(self, job_id):
        return Api({"server": self.server, "key": self.key}, self.s, job_id)


def download(api, item, dest, tries=5):
    """Download with HTTP Range resume. item: {url,name,size}.
    Retry budget resets whenever bytes were gained."""
    url = api.server + item["url"]
    size = int(item.get("size") or 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    attempts = 0
    while attempts < tries:
        have = dest.stat().st_size if dest.exists() else 0
        if size and have > size:  # corrupt partial: start over
            dest.unlink()
            have = 0
        if size and have == size:
            return
        start = have
        try:
            hdr = dict(api.headers)
            if have:
                hdr["Range"] = "bytes=%d-" % have
            r = api.s.get(url, headers=hdr, stream=True, timeout=TIMEOUT)
            if r.status_code == 416:
                if have and (not size or have >= size):
                    return  # already complete
                dest.unlink()  # range makes no sense for this partial: restart
                raise RuntimeError("HTTP 416")
            if r.status_code == 200 and have:  # server ignored Range: restart
                have = 0
            elif r.status_code not in (200, 206):
                raise RuntimeError("下載失敗 HTTP %d" % r.status_code)
            with open(dest, "ab" if have else "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    have += len(chunk)
                    if size:
                        api.progress("download", have * 100 / size, item.get("name", ""))
            if not size or have >= size:
                return
            last_err = "連線中斷（已收 %d / %d）" % (have, size)
        except Cancelled:
            raise
        except Exception as e:  # network blip: loop and resume
            last_err = e
        have_now = dest.stat().st_size if dest.exists() else 0
        attempts = 0 if have_now > start else attempts + 1
        if attempts < tries:
            time.sleep(2)
    raise RuntimeError("下載錄音失敗：%s" % last_err)


class Keepalive:
    """Background thread calling api.beat() every `interval` s until stopped."""

    def __init__(self, api, interval=None):
        self.api = api
        self.interval = KEEPALIVE_S if interval is None else interval
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True, name="keepalive")

    def _run(self):
        while not self._stop.wait(self.interval):
            self.api.beat()
            if self.api.cancelled.is_set():
                return

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._t.ident is not None:  # started
            self._t.join(timeout=TIMEOUT + 5)
        return False


class VramGate:
    """Waiting for GPU memory. sleep/clock are injectable; the default sleep returns early when
    the keepalive got a 409, so a cancel aborts the wait promptly."""

    def __init__(self, api, sleep=None, clock=None, free_comfy=None):
        self.api = api
        self.free_comfy = free_comfy or (lambda: comfy.free_comfyui(COMFY_URL))
        self._last_free = None
        self.sleep = sleep or api.cancelled.wait
        self.clock = clock or time.monotonic
        self.t0 = self.clock()

    def elapsed(self):
        return self.clock() - self.t0

    def _try_free_comfy(self):
        """True if an idle ComfyUI released its memory (rate-limited by VRAM_FREE_COOLDOWN_S)."""
        now = self.clock()
        if self._last_free is not None and now - self._last_free < VRAM_FREE_COOLDOWN_S:
            return False
        self._last_free = now
        outcome = self.free_comfy()
        log.info("ComfyUI free-memory request: %s", outcome)
        return outcome == "freed"

    def wait(self, why=""):
        """Make room (ask ComfyUI) or wait; the caller re-checks free memory afterwards."""
        if self._try_free_comfy():
            log.warning("GPU memory: %s; ComfyUI freed memory, re-checking in %d s", why, VRAM_FREE_SETTLE_S)
            self.api.progress("wait", 0, MSG_FREEING)
            self.sleep(VRAM_FREE_SETTLE_S)
        else:
            self._wait_retry(why)
            return
        self.api.check()
        self.api.beat()
        self.api.check()

    def _wait_retry(self, why):
        log.warning("GPU memory: %s; waiting %d s (%.0f s so far)", why, VRAM_RETRY_S, self.elapsed())
        self.api.progress("wait", 0, MSG_WAIT)
        self.sleep(VRAM_RETRY_S)
        self.api.check()
        self.api.beat()  # explicit poll: a cancel during the wait is seen now, not a minute later
        self.api.check()


def _gpu_child(conn, task, wave_path, kw):
    """Child-process entry: one whisper or pyannote run on a fresh CUDA context. A long-lived
    process whose context was invalidated by a display-driver reset (nvlddmkm event 153) gets
    misleading 'out of memory' errors forever; a new process per stage avoids that and returns
    all VRAM to the card when it exits."""
    try:
        import numpy as np
        wave = np.load(wave_path)
        if task == "asr":
            import asr
            res = asr.transcribe_words(wave, lambda f: conn.send(("progress", f)), **kw)
        else:
            import diar
            res = diar.diarize(wave, **kw)
        conn.send(("ok", res))
    except BaseException as e:
        conn.send(("err", type(e).__name__, str(e)[:4000], traceback.format_exc()[-8000:]))
    finally:
        conn.close()


class ChildRunner:
    """Runs each GPU stage in a fresh spawned child; relays progress, aborts on cancel."""
    POLL_S = 0.5

    def __init__(self, api, wave, workdir, target=None):
        import numpy as np
        self.api = api
        self.target = target or _gpu_child
        workdir.mkdir(parents=True, exist_ok=True)
        self.path = str(workdir / "wave.npy")
        np.save(self.path, wave)

    def whisper(self, cb, **kw):
        return self._run("asr", kw, cb)

    def diarize(self, **kw):
        return self._run("diar", kw, None)

    def release(self):
        pass  # the child exiting already returned its VRAM

    def _died(self, p, task):
        p.join(5)
        return ChildDied("GPU child (%s) exited with code %s" % (task, p.exitcode))

    def _run(self, task, kw, cb):
        ctx = multiprocessing.get_context("spawn")
        rx, tx = ctx.Pipe(duplex=False)
        p = ctx.Process(target=self.target, args=(tx, task, self.path, kw), daemon=True,
                        name="gpu-" + task)
        p.start()
        tx.close()  # parent keeps only the read end: EOF once the child is gone
        try:
            while True:
                self.api.check()  # keepalive saw a 409: kill the child (finally) and stop
                if not rx.poll(self.POLL_S):
                    if not p.is_alive() and not rx.poll(0):
                        raise self._died(p, task)
                    continue
                try:
                    msg = rx.recv()
                except EOFError:
                    raise self._died(p, task)
                if msg[0] == "progress":
                    if cb:
                        cb(msg[1])
                elif msg[0] == "ok":
                    p.join(30)
                    return msg[1]
                else:
                    log.warning("GPU child (%s) %s: %s\n%s", task, msg[1], msg[2],
                                msg[3] if len(msg) > 3 else "")
                    raise RuntimeError(msg[2])
        finally:
            rx.close()
            if p.is_alive():
                p.terminate()
                p.join(10)


class InProcessRunner:
    """Same interface as ChildRunner, in this process (tests, debugging)."""

    def __init__(self, api, wave, workdir):
        self.wave = wave

    def whisper(self, cb, **kw):
        import asr
        return asr.transcribe_words(self.wave, cb, **kw)

    def diarize(self, **kw):
        import diar
        return diar.diarize(self.wave, **kw)

    def release(self):
        import asr
        asr.release()


Runner = ChildRunner


def run_whisper(api, asr, runner, gate):
    """Whisper with VRAM wait/retry; int8_float16 batch 4 (then 2) after VRAM_DEGRADE_S."""
    batch = 4
    retried = False
    while True:
        if gate.elapsed() >= VRAM_GIVEUP_S:
            raise GpuBusy(MSG_GIVEUP)
        low = gate.elapsed() >= VRAM_DEGRADE_S
        need = WHISPER_LOWMEM_VRAM_MB if low else WHISPER_VRAM_MB
        free = asr.free_vram_mb()
        if free is not None and free < need:
            gate.wait("whisper needs %d MiB, %d free" % (need, free))
            continue
        if low:
            kw = {"compute_type": "int8_float16", "batch_size": batch}
        else:
            kw = {"compute_type": "float16", "batch_size": 8}
        msg = MSG_LOWMEM if low else ""
        api.progress("asr", 0, msg)
        try:
            return runner.whisper(lambda f: api.progress("asr", f * 100, msg), **kw)
        except RuntimeError as e:
            runner.release()
            if is_gpu_error(e) and not retried:
                retried = True  # first GPU error: straight away in a fresh process
                log.warning("whisper GPU error (%s), retrying in a new process: %s", kw, e)
                continue
            if not is_cuda_oom(e):
                raise
            log.warning("whisper out of GPU memory (%s): %s", kw, e)
            if low:
                batch = 2
            gate.wait("whisper out of memory")


def run_diar(api, asr, runner, gate):
    """pyannote with VRAM wait/retry; on the CPU after VRAM_DEGRADE_S."""
    retried = False
    while True:
        if gate.elapsed() >= VRAM_DEGRADE_S:
            log.warning("pyannote: GPU still full, running on the CPU")
            api.progress("diar", 0, MSG_DIAR_CPU)
            return runner.diarize(device="cpu")
        free = asr.free_vram_mb()
        if free is not None and free < PYANNOTE_VRAM_MB:
            gate.wait("pyannote needs %d MiB, %d free" % (PYANNOTE_VRAM_MB, free))
            continue
        api.progress("diar", 0, "")
        try:
            return runner.diarize()
        except RuntimeError as e:
            if is_gpu_error(e) and not retried:
                retried = True
                log.warning("pyannote GPU error, retrying in a new process: %s", e)
                continue
            if not is_cuda_oom(e):
                raise
            log.warning("pyannote out of GPU memory: %s", e)
            gate.wait("pyannote out of memory")


def gpu_process(api, job, audio, sleep=None, clock=None, runner=None):
    """Full job: download -> concat all parts into ONE 16 kHz mono stream ->
    whisper once -> free -> pyannote once -> merge. One diarization keeps speakers
    consistent across files."""
    import numpy as np

    import asr
    import opencc
    from pipeline import (PROMPT, align_words, build_segments, filter_hallucinations,
                          fix_traditional, name_speakers, smooth_speakers)
    cache = BASE / "cache" / job["id"]
    ka = Keepalive(api)
    try:
        ka.__enter__()  # beats while ffmpeg decodes, the model downloads and pyannote runs
        paths = []
        for i, item in enumerate(audio):
            p = cache / ("%d%s" % (i, Path(item.get("name") or "a.m4a").suffix or ".m4a"))
            download(api, item, p)
            paths.append(p)
        api.progress("download", 100, "")
        wave = np.concatenate([asr.load_audio(p) for p in paths])
        api.check()
        total = len(wave) / 16000.0
        gpu = (runner or Runner)(api, wave, cache / "gpu")  # GPU stages run in child processes
        words = run_whisper(api, asr, gpu, VramGate(api, sleep, clock))
        gpu.release()
        turns = run_diar(api, asr, gpu, VramGate(api, sleep, clock))
        api.check()  # a keepalive 409 during diarization: stop before posting anything
        segs = build_segments(align_words(words, turns))
        cc = opencc.OpenCC("s2twp")
        for s in segs:
            s["text"] = fix_traditional(cc.convert(s["text"]))
        segs = name_speakers(smooth_speakers(filter_hallucinations(segs, PROMPT)))
        api.progress("upload", 0, "")
        return {"segments": segs, "durationSec": round(total, 2), "engine": "gpu"}
    finally:
        ka.__exit__(None, None, None)
        shutil.rmtree(cache, ignore_errors=True)


class Worker:
    def __init__(self, cfg, session, processor=gpu_process):
        self.cfg = cfg
        self.api = Api(cfg, session)
        self.processor = processor

    def run_once(self):
        """Poll once. Returns True if a job was handled (success, fail or cancel)."""
        data = self.api.next_job()
        if not data:
            return False
        job = data["job"]
        api = self.api.for_job(job["id"])
        log.info("job %s (meeting %s)", job["id"], job.get("meeting"))
        try:
            result = self.processor(api, job, data.get("audio") or [])
            api.result(result)
            log.info("job %s done: %d segments", job["id"], len(result.get("segments", [])))
        except Cancelled:
            log.info("job %s cancelled by server", job["id"])
        except Exception as e:
            log.error("job %s failed: %s", job["id"], traceback.format_exc())
            try:
                api.fail(user_error(e))
            except Exception:
                log.exception("could not report failure")
        return True


def setup_logging():
    BASE.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(BASE / "worker.log", maxBytes=5 * 1024 * 1024,
                                             backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def main():
    setup_logging()  # first, so startup failures under pythonw are logged
    try:
        import requests
        cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
        global COMFY_URL
        COMFY_URL = comfy.url_from_config(cfg)
        w = Worker(cfg, requests.Session())
        poll = cfg.get("poll", 10)
        log.info("worker started, server %s", cfg["server"])
    except Exception:
        log.exception("startup failed (config.json missing/invalid or dependency missing)")
        raise SystemExit(1)
    while True:
        try:
            if w.run_once():
                continue
        except Exception:
            log.exception("poll error")
        time.sleep(poll)


if __name__ == "__main__":
    main()
