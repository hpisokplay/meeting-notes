import json
import os
import time

import pytest
from fastapi import HTTPException

from server import uploads as up
from server.uploads import scan_inbox, _parse_range

MB = 1024 * 1024
CH = 8 * MB


def _upload(client, name, data):
    r = client.post("/api/uploads", json={"name": name, "size": len(data), "mime": "audio/mp4"})
    assert r.status_code == 200
    j = r.json()
    for i in range(j["chunks"]):
        c = data[i * CH:(i + 1) * CH]
        assert client.put(f"/api/uploads/{j['uploadId']}/{i}", content=c).status_code == 200
    return j["uploadId"]


def test_upload_resume_reports_received_chunks(client):
    r = client.post("/api/uploads", json={"name": "a.m4a", "size": 20 * MB, "mime": "audio/mp4"})
    j = r.json()
    assert j["chunkSize"] == 8388608 and j["chunks"] == 3 and j["received"] == []
    uid = j["uploadId"]
    assert client.put(f"/api/uploads/{uid}/0", content=b"x" * CH).status_code == 200
    r = client.put(f"/api/uploads/{uid}/2", content=b"y" * (20 * MB - 2 * CH))
    assert r.json()["received"] == [0, 2]
    g = client.get(f"/api/uploads/{uid}").json()
    assert g["received"] == [0, 2] and g["chunkSize"] == CH and g["chunks"] == 3
    assert client.get("/api/uploads/deadbeef").status_code == 404


def test_complete_assembles_and_creates_meeting(client, data_dir):
    data = os.urandom(CH + 1234)
    uid = _upload(client, "錄音 會議.m4a", data)
    r = client.post("/api/meetings", json={"group": "g1", "uploads": [uid]})
    assert r.status_code == 200
    m = r.json()
    assert m["status"] == "queued" and m["title"] == "錄音 會議" and m["group"] == "g1"
    assert m["audio"][0]["size"] == len(data)
    assert m["transcript"] == [] and m["summary"] is None
    f = data_dir / "audio" / m["id"] / m["audio"][0]["file"]
    assert f.read_bytes() == data
    assert not (data_dir / "uploads" / uid).exists()
    jobs = list((data_dir / "jobs" / "queue").glob("*.json"))
    assert len(jobs) == 1
    job = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert job["kind"] == "transcribe" and job["meeting"] == m["id"] and job["state"] == "queued"
    assert m["jobId"] == job["id"]
    assert m["progress"]["stage"] == "queued"


def test_multi_file_order_preserved(client, data_dir):
    u1 = _upload(client, "b.m4a", b"BBB")
    u2 = _upload(client, "a.m4a", b"AAAA")
    m = client.post("/api/meetings", json={"title": "T", "uploads": [u1, u2]}).json()
    assert [a["name"] for a in m["audio"]] == ["b.m4a", "a.m4a"]
    assert m["audio"][0]["file"].startswith("0-") and m["audio"][1]["file"].startswith("1-")
    assert client.get(f"/api/meetings/{m['id']}/audio/1").content == b"AAAA"


def test_incomplete_upload_rejected_409(client):
    r = client.post("/api/uploads", json={"name": "a.m4a", "size": 20 * MB, "mime": ""})
    uid = r.json()["uploadId"]
    client.put(f"/api/uploads/{uid}/0", content=b"x" * CH)
    r = client.post("/api/meetings", json={"uploads": [uid]})
    assert r.status_code == 409 and "上傳" in r.json()["detail"]
    assert client.get("/api/meetings").json() == []


def test_audio_range_request(client):
    uid = _upload(client, "a.m4a", bytes(range(256)) * 10)
    m = client.post("/api/meetings", json={"uploads": [uid]}).json()
    r = client.get(f"/api/meetings/{m['id']}/audio/0", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    assert r.headers["content-range"] == "bytes 0-9/2560"
    r = client.get(f"/api/meetings/{m['id']}/audio/0")
    assert r.status_code == 200 and len(r.content) == 2560
    assert client.get(f"/api/meetings/{m['id']}/audio/5").status_code == 404


def test_inbox_file_becomes_meeting(client, data_dir):
    store = client.app.state.store
    jobs = client.app.state.jobs
    p = data_dir / "inbox" / "週會.m4a"
    p.write_bytes(b"hello")
    old = time.time() - 60
    os.utime(p, (old, old))
    (data_dir / "inbox" / "fresh.m4a").write_bytes(b"x")
    (data_dir / "inbox" / ".hidden.m4a").write_bytes(b"x")
    (data_dir / "inbox" / "note.txt").write_bytes(b"x")
    os.utime(data_dir / "inbox" / ".hidden.m4a", (old, old))
    os.utime(data_dir / "inbox" / "note.txt", (old, old))
    ids = scan_inbox(store, jobs)
    assert len(ids) == 1
    m = store.get(ids[0])
    assert m["title"] == "週會" and m["status"] == "queued"
    assert not p.exists()
    assert (data_dir / "audio" / ids[0] / m["audio"][0]["file"]).read_bytes() == b"hello"
    assert (data_dir / "inbox" / "fresh.m4a").exists()


def test_chunk_index_out_of_range_400(client):
    uid = client.post("/api/uploads", json={"name": "a.m4a", "size": 10, "mime": ""}).json()["uploadId"]
    assert client.put(f"/api/uploads/{uid}/1", content=b"x").status_code == 400
    assert client.put(f"/api/uploads/{uid}/-1", content=b"x").status_code in (400, 404, 422)
    assert client.put(f"/api/uploads/{uid}/0", content=b"x" * 9).status_code == 400
    assert client.put(f"/api/uploads/{uid}/0", content=b"x" * 10).status_code == 200


def test_bad_upload_id_rejected(client):
    assert client.get("/api/uploads/..%2f..%2fx").status_code in (400, 404)
    assert client.post("/api/meetings", json={"uploads": ["../x"]}).status_code in (400, 404)


def test_retranscribe(client, data_dir):
    assert client.post("/api/meetings/nope/retranscribe").status_code == 404
    uid = _upload(client, "a.m4a", b"abc")
    m = client.post("/api/meetings", json={"uploads": [uid]}).json()
    store = client.app.state.store
    store.update(m["id"], lambda x: x.update(status="done", transcript=[{"speaker": "A", "text": "舊"}]))
    assert client.post(f"/api/meetings/{m['id']}/retranscribe").status_code == 409  # already queued
    jf = data_dir / "jobs" / "queue" / f"{m['jobId']}.json"
    j = json.loads(jf.read_text(encoding="utf-8"))
    j["state"] = "done"
    jf.write_text(json.dumps(j), encoding="utf-8")
    r = client.post(f"/api/meetings/{m['id']}/retranscribe")
    assert r.status_code == 200
    n = r.json()
    assert n["status"] == "queued" and n["jobId"] != m["jobId"] and n["transcript"][0]["text"] == "舊"
    assert len(list((data_dir / "jobs" / "queue").glob("*.json"))) == 2
    bare = store.new_id()
    store.save({"id": bare, "title": "x", "createdAt": 1, "transcript": []})
    assert client.post(f"/api/meetings/{bare}/retranscribe").status_code == 409


def _no_orphans(data_dir):
    assert not any((data_dir / "audio").iterdir())
    assert not list((data_dir / "meetings").glob("*.json"))
    assert not list((data_dir / "jobs" / "queue").glob("*.json"))


def test_failed_assembly_leaves_nothing(client, data_dir, monkeypatch):
    uid = _upload(client, "a.m4a", b"abcdef")
    real = up.shutil.copyfileobj

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(up.shutil, "copyfileobj", boom)
    with pytest.raises(OSError):
        client.post("/api/meetings", json={"uploads": [uid]})
    monkeypatch.setattr(up.shutil, "copyfileobj", real)
    _no_orphans(data_dir)
    assert (data_dir / "uploads" / uid / "0.part").exists()  # client can retry
    assert client.post("/api/meetings", json={"uploads": [uid]}).status_code == 200


def test_failed_save_rolls_back_job_and_audio(client, data_dir, monkeypatch):
    uid = _upload(client, "a.m4a", b"abcdef")
    store = client.app.state.store

    def boom(m):
        raise OSError("save failed")
    monkeypatch.setattr(store, "save", boom)
    with pytest.raises(OSError):
        client.post("/api/meetings", json={"uploads": [uid]})
    _no_orphans(data_dir)


def test_inbox_failure_keeps_file(client, data_dir, monkeypatch):
    store, jobs = client.app.state.store, client.app.state.jobs
    p = data_dir / "inbox" / "a.m4a"
    p.write_bytes(b"hello")
    old = time.time() - 60
    os.utime(p, (old, old))

    def boom(m):
        raise OSError("save failed")
    monkeypatch.setattr(store, "save", boom)
    assert scan_inbox(store, jobs) == []
    assert p.read_bytes() == b"hello"
    _no_orphans(data_dir)


def test_job_ids_unique_in_tight_loop(client, data_dir):
    jobs = client.app.state.jobs
    ids = [jobs.enqueue("transcribe", "m", {}) for _ in range(50)]
    assert len(set(ids)) == 50
    assert len(list((data_dir / "jobs" / "queue").glob("*.json"))) == 50


def test_parse_range_cases():
    assert _parse_range("bytes=0-9", 100) == (0, 9)
    assert _parse_range("bytes=90-", 100) == (90, 99)
    assert _parse_range("bytes=-10", 100) == (90, 99)
    assert _parse_range("bytes=50-500", 100) == (50, 99)
    for bad in ("bytes=100-", "bytes=-0", "bytes=9-3"):
        with pytest.raises(HTTPException) as e:
            _parse_range(bad, 100)
        assert e.value.status_code == 416 and e.value.headers["Content-Range"] == "bytes */100"
    for ignored in ("bytes=0-1,5-6", "items=0-1", "garbage", "bytes=-", None):
        assert _parse_range(ignored, 100) is None


def test_audio_416_head_and_ignored_range(client):
    uid = _upload(client, "a.m4a", b"0123456789")
    m = client.post("/api/meetings", json={"uploads": [uid]}).json()
    url = f"/api/meetings/{m['id']}/audio/0"
    r = client.get(url, headers={"Range": "bytes=20-"})
    assert r.status_code == 416 and r.headers["content-range"] == "bytes */10"
    r = client.get(url, headers={"Range": "bytes=0-1,3-4"})
    assert r.status_code == 200 and len(r.content) == 10
    r = client.head(url)
    assert r.status_code == 200 and r.headers["content-length"] == "10" and r.content == b""


def test_non_string_title_400(client):
    uid = _upload(client, "a.m4a", b"abc")
    assert client.post("/api/meetings", json={"title": 5, "uploads": [uid]}).status_code == 400
    assert client.post("/api/meetings", json={"group": ["x"], "uploads": [uid]}).status_code == 400
