import json
import os
import time

from server import fallback as F
from server import notify as N
from server.backup import needs_catchup, run_backup
from server.fallback import FallbackThread, transcribe_windows
from server.importer import merge_legacy
from server.jobs import JobQueue
from server.storage import Store


def fake_env(durs):
    calls = []
    paths = [f"f{i}.m4a" for i in range(len(durs))]
    dmap = {p: d for p, d in zip(paths, durs)}

    def probe(p):
        return dmap[str(p)]

    def decode(p, off, length):
        calls.append((str(p), off, length))
        return (str(p), off, length)

    return paths, probe, decode, calls


def test_windows_offsets_across_windows_and_files():
    W = F.WINDOW_S
    paths, probe, decode, calls = fake_env([W + 600, 300])

    def model(audio):
        return [(5.0, "a"), (W + 1.0, "overlap-dup")] if audio[1] == 0 else [(10.0, "b")]
    prog = []
    r = transcribe_windows(paths, lambda p, m: prog.append(p) or True, probe, decode, model)
    segs = [(s["t"], s["text"]) for s in r["segments"]]
    # window 1 of file 0: "a" at 5, overlap segment dropped; window 2 (off=W): "b"; file 1 offset W+600
    assert (5.0, "a") in segs and not any(t == "overlap-dup" for _, t in segs)
    assert (W + 10.0, "b") in segs and (W + 600 + 5.0, "a") in segs
    assert calls[0] == ("f0.m4a", 0.0, W + F.OVERLAP_S)
    assert calls[1] == ("f0.m4a", W, 600 + F.OVERLAP_S)
    assert calls[2] == ("f1.m4a", 0.0, 300 + F.OVERLAP_S)
    assert r["durationSec"] == W + 900 and r["engine"] == "nas-cpu"
    assert len(prog) == 1 + 3  # initial + one per window


def test_windows_cancel_between_windows():
    W = F.WINDOW_S
    paths, probe, decode, calls = fake_env([W * 3])
    n = []

    def prog(p, m):
        n.append(p)
        return len(n) < 3  # initial ok, window1 ok, window2 -> stop
    assert transcribe_windows(paths, prog, probe, decode, lambda a: []) is None
    assert len(calls) == 2


def test_initial_progress_right_after_claim(tmp_path):
    s = Store(tmp_path)
    jobs = JobQueue(s)
    jid = jobs.enqueue("transcribe", "m1", {})
    p = jobs.dirs["queued"] / f"{jid}.json"
    j = json.loads(p.read_text("utf-8")); j["created"] = time.time() - 3600; p.write_text(json.dumps(j), "utf-8")
    s.save({"id": "m1", "title": "t", "createdAt": 1, "status": "queued", "jobId": jid,
            "audio": [{"file": "0-a.m4a"}], "transcript": []})
    seen = {}

    def t(paths, progress):
        seen["job"] = jobs.get(jid)["progress"]
        seen["m"] = s.get("m1")["progress"]
        return None
    FallbackThread(s, jobs, t).tick()
    assert seen["job"]["msg"] == "NAS 備援轉錄準備中" and seen["m"]["engine"] == "nas-cpu"


def test_summary_failed_message():
    s = Store(__import__("tempfile").mkdtemp())
    s.save({"id": "m1", "title": "週會", "createdAt": 1, "status": "done", "aiJobId": "a1", "error": "額度用完"})
    sent = []
    assert N.on_job_done(s, {"id": "a1", "kind": "ai", "meeting": "m1"}, lambda t, st=None: sent.append(t))
    assert sent[0] == "⚠️《週會》逐字稿已完成，但摘要失敗：額度用完\nhttps://ddys.tail95e8ec.ts.net:8443/#/m/m1"


def test_on_job_done_polls_until_done(tmp_path):
    s = Store(tmp_path)
    s.save({"id": "m1", "title": "週會", "createdAt": 1, "status": "summarizing", "aiJobId": "a1"})
    sent, ticks = [], []

    def sleep(x):
        ticks.append(x)
        if len(ticks) == 3:
            s.update("m1", lambda m: m.update(status="done"))
    assert N.on_job_done(s, {"id": "a1", "kind": "ai", "meeting": "m1"},
                         lambda t, st=None: sent.append(t), sleep=sleep)
    assert len(ticks) == 3 and sent[0].startswith("✅")
    # never done within the wait -> no message
    s.update("m1", lambda m: m.update(status="summarizing"))
    assert not N.on_job_done(s, {"id": "a1", "kind": "ai", "meeting": "m1"},
                             lambda t, st=None: sent.append(t), wait=1, sleep=lambda x: None)


def test_merge_keeps_local_only_fields_when_legacy_wins(tmp_path):
    s = Store(tmp_path)
    s.save({"id": "L1", "title": "本機", "createdAt": 1, "updatedAt": 100, "status": "done",
            "myField": 7, "terms": {"items": [{"t": "A", "applied": True}, {"t": "B"}]}}, touch=False)
    inc = {"id": "L1", "title": "舊", "createdAt": 1, "updatedAt": 200, "legacyOnly": 1,
           "terms": {"items": [{"t": "A"}, {"t": "C", "draft": "x"}]}}
    rep = merge_legacy(s, {"meetings": [inc]})
    assert rep["updated"] == 1
    m = s.get("L1")
    assert m["title"] == "舊" and m["myField"] == 7 and m["legacyOnly"] == 1
    items = {i["t"]: i for i in m["terms"]["items"]}
    assert set(items) == {"A", "B", "C"} and items["A"]["applied"] is True and items["C"]["draft"] == "x"
    # local wins: legacy-only field copied in
    rep = merge_legacy(s, {"meetings": [{"id": "L1", "updatedAt": 5, "extra": 2}]})
    assert rep["kept"] == 1 and s.get("L1")["extra"] == 2


def test_chat_without_at_deduped_by_qa(tmp_path):
    s = Store(tmp_path)
    s.save({"id": "L1", "createdAt": 1, "updatedAt": 1, "status": "done", "chat": [{"q": "q", "a": "a"}]}, touch=False)
    merge_legacy(s, {"meetings": [{"id": "L1", "updatedAt": 2, "chat": [{"q": "q", "a": "a"}, {"q": "q2", "a": "a2"}]}]})
    assert [c["q"] for c in s.get("L1")["chat"]] == ["q", "q2"]


def test_backup_catchup(tmp_path):
    s = Store(tmp_path / "d")
    bk = tmp_path / "bk"
    now = time.time()
    assert needs_catchup(bk, now) is True
    out = run_backup(s, bk, now)
    assert needs_catchup(bk, now) is False
    os.utime(out, (now - 25 * 3600, now - 25 * 3600))
    assert needs_catchup(bk, now) is True
