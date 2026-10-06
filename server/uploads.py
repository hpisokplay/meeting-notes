"""Resumable chunked uploads, meeting creation from audio, audio serving, inbox scan."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .storage import Store, atomic_write_json, read_json

log = logging.getLogger(__name__)
CHUNK_SIZE = 8 * 1024 * 1024
READ_SIZE = 1024 * 1024
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".caf", ".aiff", ".mp4", ".ogg", ".flac", ".webm",
              ".oga", ".opus", ".mov", ".m4v", ".3gp", ".amr", ".wma"}
INBOX_SETTLE_SEC = 10
UPLOAD_TTL_SEC = 3 * 24 * 3600  # abandoned resumable uploads are swept after this
_UID_RE = re.compile(r"^[0-9a-f]{32}$")
_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f/\\<>:"|?*]')
_MIME = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".aac": "audio/aac",
         ".ogg": "audio/ogg", ".flac": "audio/flac", ".webm": "audio/webm",
         ".mp4": "video/mp4", ".aiff": "audio/aiff", ".caf": "audio/x-caf",
         ".oga": "audio/ogg", ".opus": "audio/ogg", ".mov": "video/quicktime", ".m4v": "video/x-m4v",
         ".3gp": "video/3gpp", ".amr": "audio/amr", ".wma": "audio/x-ms-wma"}


def safe_name(name) -> str:
    base = str(name or "").replace("\\", "/").split("/")[-1]
    base = _BAD_CHARS.sub("", base).strip(" .")
    if len(base) > 120:
        stem, ext = os.path.splitext(base)
        base = stem[:120 - len(ext)] + ext
    return base or "audio"


def probe_duration(path: Path):
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return round(float(out.stdout.strip()), 2)
    except Exception:
        return None


def _udir(store: Store, uid: str) -> Path:
    return store.path("uploads", uid)


def _valid_uid(uid) -> bool:
    return isinstance(uid, str) and bool(_UID_RE.match(uid))


def _load_upload(store: Store, uid: str) -> dict:
    if not _valid_uid(uid):
        raise HTTPException(404, "找不到這個上傳")
    meta = read_json(_udir(store, uid) / "meta.json")
    if meta is None:
        raise HTTPException(404, "找不到這個上傳")
    return meta


def _received(store: Store, uid: str) -> list:
    return sorted(int(p.stem) for p in _udir(store, uid).glob("*.part") if p.stem.isdigit())


def _upload_view(store: Store, uid: str, meta: dict) -> dict:
    return {"uploadId": uid, "chunkSize": CHUNK_SIZE, "chunks": meta["chunks"],
            "received": _received(store, uid)}


def _expected_len(meta: dict, index: int) -> int:
    if index == meta["chunks"] - 1:
        return meta["size"] - CHUNK_SIZE * (meta["chunks"] - 1)
    return CHUNK_SIZE


def create_meeting(store: Store, jobs, files: list, title=None, group=None) -> dict:
    """files: [{name, src}]; src is a path (moved into audio/<id>/) or a callable(dest_path)
    that writes the destination file. On any failure everything created here is rolled back
    (audio dir, queued job, saved meeting) and moved source files are put back."""
    mid = store.new_id()
    adir = store.path("audio", mid)
    moved: list = []  # (dest, original src)
    jid = None
    try:
        adir.mkdir(parents=True, exist_ok=True)
        audio = []
        for n, f in enumerate(files):
            sname = safe_name(f["name"])
            fname = f"{n}-{sname}"
            dest = adir / fname
            src = f["src"]
            if callable(src):
                src(dest)
            else:
                shutil.move(str(src), str(dest))
                moved.append((dest, Path(src)))
            audio.append({"file": fname, "name": sname, "size": dest.stat().st_size,
                          "durationSec": probe_duration(dest)})
        now = int(time.time() * 1000)
        if not title:
            title = os.path.splitext(audio[0]["name"])[0] if audio else "新會議"
        durs = [a["durationSec"] for a in audio]
        jid = jobs.enqueue("transcribe", mid, {"glossary": store.glossary_list()})
        m = {"id": mid, "title": title, "createdAt": now, "updatedAt": now, "group": group or None,
             "status": "queued", "jobId": jid,
             "progress": {"stage": "queued", "pct": 0, "msg": "", "engine": ""},
             "audio": audio, "transcript": [], "summary": None,
             "durationSec": sum(durs) if durs and all(d is not None for d in durs) else None}
        return store.save(m)
    except BaseException:
        if jid:
            jobs.queue_dir.joinpath(f"{jid}.json").unlink(missing_ok=True)
        if store.get(mid) is not None:
            try:
                store.trash(mid)  # never hard-delete a meeting json
            except Exception:
                pass
        keep = False  # a source we could not put back must not be deleted with the audio dir
        for dest, orig in moved:
            if not dest.exists():
                continue
            if orig.exists():
                keep = True
                log.error("rollback: %s exists again; kept %s", orig, dest)
                continue
            try:
                shutil.move(str(dest), str(orig))
            except OSError:
                keep = True
                log.exception("rollback: could not move %s back to %s; kept it", dest, orig)
        if keep:
            log.error("rollback: audio dir %s left in place (contains unrestored source files)", adir)
        else:
            shutil.rmtree(adir, ignore_errors=True)
        raise


def _assembler(store: Store, uid: str, meta: dict):
    def write(dest: Path):
        tmp = dest.with_name(dest.name + ".tmp")
        try:
            with open(tmp, "wb") as out:
                for i in range(meta["chunks"]):
                    with open(_udir(store, uid) / f"{i}.part", "rb") as part:
                        shutil.copyfileobj(part, out, READ_SIZE)
                out.flush()
                os.fsync(out.fileno())
            if tmp.stat().st_size != meta["size"]:
                raise HTTPException(409, "上傳的檔案大小不符，請重新上傳")
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    return write


def scan_inbox(store: Store, jobs) -> list:
    ids = []
    now = time.time()
    for p in sorted(store.path("inbox").iterdir(), key=lambda x: x.name):
        if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in AUDIO_EXTS:
            continue
        try:
            if now - p.stat().st_mtime < INBOX_SETTLE_SEC:
                continue
            m = create_meeting(store, jobs, [{"name": p.name, "src": p}])
        except Exception:
            continue  # create_meeting rolled back; file stays in inbox, retry next scan
        ids.append(m["id"])
    return ids


def sweep_uploads(store: Store, now: float | None = None, ttl: float = UPLOAD_TTL_SEC) -> list:
    """Remove uploads/<uid> dirs whose newest file is older than ttl (abandoned uploads).
    Returns the removed uids."""
    now = time.time() if now is None else now
    gone = []
    root = store.path("uploads")
    if not root.is_dir():
        return gone
    for d in root.iterdir():
        if not d.is_dir() or not _valid_uid(d.name):
            continue
        try:
            newest = max([f.stat().st_mtime for f in d.iterdir()] + [d.stat().st_mtime])
        except OSError:
            continue
        if now - newest > ttl:
            shutil.rmtree(d, ignore_errors=True)
            gone.append(d.name)
    return gone


def _parse_range(header, size: int):
    """Returns (start, end), or None to ignore the header (invalid / multi-range / unknown unit).
    Raises 416 when a well-formed single range is unsatisfiable."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    if m.group(1) == "":
        start, end = max(size - int(m.group(2)), 0), size - 1
    else:
        start = int(m.group(1))
        end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
    if start >= size or start > end:
        raise HTTPException(416, "Range 超出範圍", headers={"Content-Range": f"bytes */{size}"})
    return start, end


def _iter_file(path: Path, start: int, end: int):
    remaining = end - start + 1
    with open(path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(READ_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _write_chunk(d: Path, index: int, data: bytes) -> None:
    tmp = d / f"{index}.part.{uuid.uuid4().hex[:6]}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, d / f"{index}.part")
    finally:
        tmp.unlink(missing_ok=True)


def build_router(store: Store, get_jobs) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.post("/uploads")
    def start_upload(body: dict = Body(...)):
        try:
            size = int(body.get("size"))
        except (TypeError, ValueError):
            raise HTTPException(400, "檔案大小無效")
        if size <= 0:
            raise HTTPException(400, "檔案大小無效")
        uid = uuid.uuid4().hex
        meta = {"name": safe_name(body.get("name")), "size": size,
                "mime": str(body.get("mime") or ""), "chunks": -(-size // CHUNK_SIZE),
                "created": time.time()}
        atomic_write_json(_udir(store, uid) / "meta.json", meta)
        return _upload_view(store, uid, meta)

    @r.get("/uploads/{uid}")
    def get_upload(uid: str):
        return _upload_view(store, uid, _load_upload(store, uid))

    @r.put("/uploads/{uid}/{index}")
    async def put_chunk(uid: str, index: int, request: Request):
        meta = _load_upload(store, uid)
        if index < 0 or index >= meta["chunks"]:
            raise HTTPException(400, "分塊編號超出範圍")
        want = _expected_len(meta, index)
        body = bytearray()
        async for piece in request.stream():
            body += piece
            if len(body) > want:
                break
        if len(body) != want:
            raise HTTPException(400, f"分塊大小不符（應為 {want} 位元組）")
        await run_in_threadpool(_write_chunk, _udir(store, uid), index, bytes(body))
        return {"received": _received(store, uid)}

    @r.post("/meetings")
    def create(body: dict = Body(...)):
        title, group = body.get("title"), body.get("group")
        if title is not None and not isinstance(title, str):
            raise HTTPException(400, "標題必須是文字")
        if group is not None and not isinstance(group, str):
            raise HTTPException(400, "群組必須是文字")
        uids = body.get("uploads")
        if not isinstance(uids, list) or not uids:
            raise HTTPException(400, "請至少提供一個上傳檔案")
        files = []
        for uid in uids:
            if not _valid_uid(uid):
                raise HTTPException(400, "上傳編號無效")
            meta = read_json(_udir(store, uid) / "meta.json")
            if meta is None:
                raise HTTPException(404, "找不到這個上傳")
            if _received(store, uid) != list(range(meta["chunks"])):
                raise HTTPException(409, f"檔案「{meta['name']}」還沒上傳完成，請等上傳完成再建立會議")
            files.append((uid, meta))
        m = create_meeting(
            store, get_jobs(),
            [{"name": meta["name"], "src": _assembler(store, uid, meta)} for uid, meta in files],
            title=(title or "").strip() or None, group=group)
        for uid, _ in files:
            shutil.rmtree(_udir(store, uid), ignore_errors=True)
        return m

    @r.api_route("/meetings/{mid}/audio/{n}", methods=["GET", "HEAD"])
    def get_audio(mid: str, n: int, request: Request):
        m = store.get(mid)
        if m is None:
            raise HTTPException(404, "找不到這場會議")
        audio = m.get("audio") or []
        if n < 0 or n >= len(audio):
            raise HTTPException(404, "找不到這個錄音")
        path = store.path("audio", mid, audio[n]["file"])
        if not path.is_file():
            raise HTTPException(404, "錄音檔不存在")
        size = path.stat().st_size
        mime = _MIME.get(path.suffix.lower(), "application/octet-stream")
        headers = {"Accept-Ranges": "bytes"}
        rng = _parse_range(request.headers.get("range"), size)
        if rng:
            start, end = rng
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1)
            return StreamingResponse(_iter_file(path, start, end), status_code=206,
                                     media_type=mime, headers=headers)
        headers["Content-Length"] = str(size)
        return StreamingResponse(_iter_file(path, 0, size - 1), media_type=mime, headers=headers)

    @r.post("/meetings/{mid}/retranscribe")
    def retranscribe(mid: str):
        m = store.get(mid)
        if m is None:
            raise HTTPException(404, "找不到這場會議")
        if not m.get("audio"):
            raise HTTPException(409, "這場會議沒有錄音檔，無法重新轉錄")
        jobs = get_jobs()
        made: list = []

        def apply(x):
            cur = jobs.read(x.get("jobId")) if x.get("jobId") else None
            if cur and cur.get("kind") == "transcribe" and cur.get("state") in ("queued", "running", "claimed"):
                raise HTTPException(409, "已在轉錄中")
            jid = jobs.enqueue("transcribe", mid, {"glossary": store.glossary_list()})
            made.append(jid)
            # 舊的「轉錄失敗：…」不能在排隊期間還掛在畫面上
            x.update(status="queued", jobId=jid, error=None,
                     progress={"stage": "queued", "pct": 0, "msg": "", "engine": ""})
        try:
            return store.update(mid, apply)
        except BaseException:
            for jid in made:
                jobs.queue_dir.joinpath(f"{jid}.json").unlink(missing_ok=True)
            raise

    return r
