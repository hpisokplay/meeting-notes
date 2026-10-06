"""MeetingWorker: poll the NAS for transcribe jobs, run them on the local GPU, post segments back."""
import json
import logging
import logging.handlers
import os
import shutil
import threading
import time
import traceback
from pathlib import Path

# %USERPROFILE%\MeetingWorker, not %LOCALAPPDATA%: writes to LOCALAPPDATA from an MSIX-packaged
# parent (e.g. the Claude desktop app) are virtualized into Packages\...\LocalCache.
BASE = Path.home() / "MeetingWorker"
TIMEOUT = 60
KEEPALIVE_S = 60  # re-post the last progress this often while a stage is silent (diar, decode, model download)
RESULT_BACKOFF = (15, 45, 90, 150)  # waits between the 5 result POST attempts (~5 min in total)
log = logging.getLogger("worker")


class Cancelled(Exception):
    """Server answered 409 on progress: the job was cancelled."""


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


def gpu_process(api, job, audio):
    """Full job: download -> concat all parts into ONE 16 kHz mono stream ->
    whisper once -> free -> pyannote once -> merge. One diarization keeps speakers
    consistent across files."""
    import numpy as np

    import asr
    import diar
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
        words = asr.transcribe_words(wave, lambda f: api.progress("asr", f * 100, ""))
        asr.release()
        api.progress("diar", 0, "")
        turns = diar.diarize(wave)
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
                api.fail("轉錄失敗：%s" % e)
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
