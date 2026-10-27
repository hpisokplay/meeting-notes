"""`report`（完整整理）AI 動作：走 Max 訂閱（`claude -p`，測試用假 Claude）。"""
import copy
import json
import sys
from pathlib import Path

import pytest

from server import ai, prompts as P
from server.ai import handle_ai_job
from server.jobs import JobQueue
from server.storage import Store

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]

TRANSCRIPT = [
    {"speaker": "說話者 1", "text": "我們談台積電的案子", "t": 3725},
    {"speaker": "說話者 2", "text": "下週由小陳回覆客戶", "t": 3800},
    {"speaker": "說話者 1", "text": "預算先抓三百萬", "t": 4000.4},
]
SUMMARY = {"actionItems": [{"text": "舊待辦", "src": []}], "mainPoints": [{"text": "舊重點：說明", "src": []}],
           "qa": [{"text": "問：舊？\n答：舊。", "src": []}]}

@pytest.fixture
def sj(tmp_path):
    store = Store(tmp_path / "data")
    return store, JobQueue(store)


@pytest.fixture
def maxlog(tmp_path, monkeypatch):
    p = tmp_path / "claude.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(p))

    def read():
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    return read


def mk(store, mid="m1", **kw):
    m = {"id": mid, "title": "t", "createdAt": 1, "status": "done", "mode": "meeting",
         "transcript": copy.deepcopy(TRANSCRIPT), "summary": copy.deepcopy(SUMMARY)}
    m.update(kw)
    store.save(m)
    return m


def run_job(store, jobs, mid="m1"):
    jid = jobs.enqueue("ai", mid, {"action": "report"})
    job = jobs.claim(["ai"], "nas-ai")
    assert job["id"] == jid
    handle_ai_job(store, job, FAKE, jobs)
    return jid


def report_calls(log):
    return [r for r in log() if r["argv"][-1] == P.REPORT_PROMPT]


# ---------------------------------------------------------------- registration
def test_report_action_registered():
    assert "report" in ai.ACTIONS
    assert ai.HANDLERS["report"] is ai._report
    assert ai.LABELS["report"] == "完整整理"


# ---------------------------------------------------------------- Max path
def test_report_writes_items_and_meta(sj, maxlog):
    store, jobs = sj
    mk(store)
    jid = run_job(store, jobs)
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    m = store.get("m1")
    s = m["summary"]
    assert [x["text"] for x in s["reportIntro"]] == ["引言（我們談台積電的案子）"]
    assert [(x["group"], x["text"]) for x in s["reportPoints"]] == [
        ("大標題甲", "子標題一：說明一（我們談台積電的案子）"), ("大標題乙", "子標題二：說明二（我們談台積電的案子）")]
    assert all(len(x["id"]) == 8 and x["src"] for x in s["reportPoints"])
    meta = m["reportMeta"]
    assert set(meta) == {"at", "fp", "stale"}
    assert meta["stale"] is False and meta["fp"] == ai._fingerprint(m["transcript"])
    assert isinstance(meta["at"], int) and meta["at"] > 1_700_000_000_000
    assert len(report_calls(maxlog)) == 1
    assert isinstance(m.get("review"), dict)  # _reverify ran


def test_report_does_not_touch_main_points(sj, maxlog):
    store, jobs = sj
    mk(store)
    run_job(store, jobs)
    s = store.get("m1")["summary"]
    for k in ("actionItems", "mainPoints", "qa"):
        assert [x["text"] for x in s[k]] == [x["text"] for x in SUMMARY[k]]
    assert [r["argv"][-1] for r in maxlog()] == [P.REPORT_PROMPT]  # summary present: only the report call


def test_report_backup_restorable(sj, maxlog):
    store, jobs = sj
    old_pts = [{"id": "aaaaaaaa", "text": "舊子標題：舊說明", "group": "舊大標題", "src": []}]
    old_intro = [{"id": "bbbbbbbb", "text": "舊引言", "src": []}]
    mk(store, summary={**copy.deepcopy(SUMMARY), "reportIntro": old_intro, "reportPoints": old_pts})
    run_job(store, jobs)
    m = store.get("m1")
    assert m["alt"]["summary:reportPoints"] == old_pts
    assert m["alt"]["summary:reportIntro"] == old_intro
    assert "summary:mainPoints" not in m["alt"]  # only the report lists are backed up
    assert m["summary"]["reportPoints"][0]["text"] == "子標題一：說明一（我們談台積電的案子）"


def test_report_empty_points_fails(sj, maxlog, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_REPORT_EMPTY", "1")
    store, jobs = sj
    mk(store)
    jid = run_job(store, jobs)
    j = jobs.get(jid)
    assert j["state"] == "failed" and "完整整理沒有產生內容" in j["error"]
    m = store.get("m1")
    assert "reportPoints" not in m["summary"] and "reportMeta" not in m


def test_report_max_failure_is_plain_error(sj, maxlog, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    store, jobs = sj
    mk(store)
    jid = run_job(store, jobs)
    j = jobs.get(jid)
    assert j["state"] == "failed"
    assert "boom: something broke" in j["error"] and "API" not in j["error"]
    assert "reportMeta" not in store.get("m1")


def test_report_max_limit_is_retried(sj, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    store, jobs = sj
    mk(store)
    jid = run_job(store, jobs)
    j = jobs.get(jid)
    assert j["error"] == ai.LIMIT_MSG and j.get("notBefore") and j["state"] == "queued"


def test_report_cancel_during_call_discards(sj, maxlog, monkeypatch):
    store, jobs = sj
    mk(store)
    holder = {}
    real = ai.run_claude

    def cancelling(system, *a, **kw):
        if system == P.REPORT_PROMPT:
            jobs.cancel(holder["jid"])
        return real(system, *a, **kw)
    monkeypatch.setattr(ai, "run_claude", cancelling)
    jid = jobs.enqueue("ai", "m1", {"action": "report"})
    holder["jid"] = jid
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    assert jobs.get(jid)["state"] == "cancelled"
    assert "reportMeta" not in store.get("m1")
    assert "reportPoints" not in store.get("m1")["summary"]


# ---------------------------------------------------------------- missing actionItems / qa
def test_report_fills_missing_summary_first(sj, maxlog):
    store, jobs = sj
    mk(store, summary={})
    jid = run_job(store, jobs)
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    m = store.get("m1")
    s = m["summary"]
    assert [x["text"] for x in s["actionItems"]] == ["新待辦 [DRI: 說話者 1]"]
    assert s["qa"] and s["mainPoints"]
    assert s["reportPoints"] and m["reportMeta"]["stale"] is False
    assert [r["argv"][-1] for r in maxlog()] == [P.SUMMARY_MEETING, P.REPORT_PROMPT]


def test_report_keeps_existing_lists_when_only_qa_missing(sj, maxlog):
    store, jobs = sj
    s0 = copy.deepcopy(SUMMARY)
    del s0["qa"]
    mk(store, summary=s0)
    run_job(store, jobs)
    s = store.get("m1")["summary"]
    assert [x["text"] for x in s["actionItems"]] == ["舊待辦"]
    assert [x["text"] for x in s["mainPoints"]] == ["舊重點：說明"]
    assert s["qa"] and s["qa"][0]["text"].startswith("問：新問題")


# ---------------------------------------------------------------- multi-chunk merge
def test_report_multi_chunk_merge_keeps_text(sj, maxlog, monkeypatch):
    monkeypatch.setattr(ai, "MAX_CHARS", 30)  # every transcript line is its own chunk
    store, jobs = sj
    mk(store)
    jid = run_job(store, jobs)
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    log = maxlog()
    assert len(report_calls(maxlog)) == 3
    merges = [r for r in log if r["argv"][-1] == P.REPORT_MERGE_PROMPT]
    assert len(merges) == 1
    lines = merges[0]["stdin"].splitlines()
    assert len(lines) == 6 and lines[0].startswith("[1] 〔大標題甲〕子標題一：") and lines[-1].startswith("[6] ")
    s = store.get("m1")["summary"]
    pts = s["reportPoints"]
    drafts = [ln.split("〕", 1)[1] for ln in lines]
    assert [p["text"] for p in pts] == drafts[::-1]  # fake merge reverses; texts from the drafts
    assert {p["group"] for p in pts} == {"合併後大標題"}
    assert all(p["src"] for p in pts if "無" not in p["text"])
    assert len(s["reportIntro"]) == 1


def test_apply_report_order_handles_model_mistakes():
    pts = [{"id": f"{i:08x}", "text": f"子{i}：說明{i}", "group": g, "src": [{"t": i, "quote": "q"}]}
           for i, g in enumerate(["甲", "甲", "乙", "丙"], 1)]
    groups = [{"heading": "新乙", "items": [3, 3, 99, "2", True, 0]},
              {"heading": "", "items": [1]},
              "垃圾"]
    out = ai._apply_report_order(pts, {"groups": groups})
    # 3 once (dup / unknown / non-int ignored); 1 keeps its own heading when the model gave none;
    # orphans 2 (甲 -> joins the 甲 group) and 4 (丙) follow in original order
    assert [(p["text"], p["group"]) for p in out] == [
        ("子3：說明3", "新乙"), ("子1：說明1", "甲"), ("子2：說明2", "甲"), ("子4：說明4", "丙")]
    assert [p["src"] for p in out] == [pts[2]["src"], pts[0]["src"], pts[1]["src"], pts[3]["src"]]
    assert ai._apply_report_order(pts, None) == pts
    assert ai._apply_report_order(pts, {"groups": []}) == pts


# ---------------------------------------------------------------- Task 4: reprocess / stale / name correction
def test_reprocess_also_builds_report(sj, maxlog):
    store, jobs = sj
    mk(store, summary=None)
    jid = jobs.enqueue("ai", "m1", {"action": "reprocess", "correction": False, "slips": False, "adjudicate": False})
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    m = store.get("m1")
    s = m["summary"]
    assert s["reportIntro"] and s["reportPoints"]
    assert m["reportMeta"]["stale"] is False
    assert m["reportMeta"]["fp"] == ai._fingerprint(m["transcript"])
    assert len(report_calls(maxlog)) == 1


def test_reprocess_report_failure_is_only_a_warning(sj, maxlog, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_REPORT_EMPTY", "1")
    store, jobs = sj
    mk(store, summary=None)
    jid = jobs.enqueue("ai", "m1", {"action": "reprocess", "correction": False, "slips": False, "adjudicate": False})
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    assert jobs.get(jid)["state"] == "done"
    m = store.get("m1")
    assert m["summary"]["mainPoints"] and "reportPoints" not in m["summary"] and "reportMeta" not in m
    assert any(w.startswith("完整整理失敗，已略過") for w in m["warnings"])


def test_reprocess_progress_stays_monotonic(sj, maxlog, monkeypatch):
    monkeypatch.setattr(ai, "MAX_CHARS", 30)  # several report chunks -> inner progress reports
    store, jobs = sj
    mk(store, summary=None)
    jid = jobs.enqueue("ai", "m1", {"action": "reprocess", "correction": False, "adjudicate": False})
    seen = []
    real = jobs.progress

    def spy(j, stage, pct, msg=""):
        seen.append((stage, pct, msg))
        return real(j, stage, pct, msg)
    monkeypatch.setattr(jobs, "progress", spy)
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    pcts = [p for _s, p, _m in seen]
    assert any(s == "report" for s, _p, _m in seen)
    assert pcts == sorted(pcts), seen


def test_new_transcript_marks_report_stale(sj):
    from server.jobs import apply_transcript_result
    store, jobs = sj
    jid = jobs.enqueue("transcribe", "m1", {})
    mk(store, status="transcribing", jobId=jid,
       reportMeta={"at": 1, "fp": "abc", "stale": False})
    job = jobs.claim(["transcribe"], "gpu-worker")
    assert apply_transcript_result(store, jobs, job, {"segments": TRANSCRIPT[:1], "durationSec": 5, "engine": "gpu"})
    assert store.get("m1")["reportMeta"]["stale"] is True
    # no reportMeta -> nothing invented
    jid = jobs.enqueue("transcribe", "m2", {})
    mk(store, "m2", status="transcribing", jobId=jid)
    job = jobs.claim(["transcribe"], "gpu-worker")
    assert apply_transcript_result(store, jobs, job, {"segments": TRANSCRIPT[:1], "durationSec": 5, "engine": "gpu"})
    assert "reportMeta" not in store.get("m2")


def test_restore_raw_marks_report_stale(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.app import create_app
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setenv("MEETINGS_STATIC", str(static))
    app = create_app(tmp_path / "data", start_threads=False, claude_cmd=FAKE)
    c = TestClient(app)
    mk(app.state.store, transcriptRaw=copy.deepcopy(TRANSCRIPT[:2]),
       reportMeta={"at": 1, "fp": "abc", "stale": False})
    assert c.post("/api/meetings/m1/restore-raw").status_code == 200
    assert app.state.store.get("m1")["reportMeta"]["stale"] is True


def test_name_correction_replaces_in_report_items(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import wordreview as W
    from server.app import create_app
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setenv("MEETINGS_STATIC", str(static))
    app = create_app(tmp_path / "data", start_threads=False, claude_cmd=FAKE)
    c = TestClient(app)
    tr = [{"speaker": "說話者 1", "text": "Jefry 會負責報價", "t": 20.0}]
    intro = [{"id": "aaaaaaaa", "text": "Jefry 負責報價", "src": [{"t": 20.0, "quote": "Jefry 會負責報價"}]}]
    pts = [{"id": "bbbbbbbb", "text": "報價：Jefry 會回覆", "group": "分工", "src": [{"t": 20.0, "quote": "Jefry 會負責報價"}]}]
    m = mk(app.state.store, transcript=tr, summary={"reportIntro": intro, "reportPoints": pts},
           reportMeta={"at": 1, "fp": ai._fingerprint(tr), "stale": False})
    W.apply_ai_cards(m, [{"span": "Jefry", "action": "replace", "suggestion": "Jeffrey", "reason": "r",
                          "confidence": "high", "itemIds": [], "fromFlags": []}])
    app.state.store.save(m)
    cid = next(x["id"] for x in m["review"]["items"] if W.is_card(x))
    r = c.post(f"/api/meetings/m1/review/{cid}", json={"action": "accept"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["transcript"][0]["text"] == "Jeffrey 會負責報價"
    assert out["summary"]["reportIntro"][0]["text"] == "Jeffrey 負責報價"
    assert out["summary"]["reportPoints"][0]["text"] == "報價：Jeffrey 會回覆"
    assert out["summary"]["reportPoints"][0]["src"][0]["quote"] == "Jeffrey 會負責報價"
    assert out["reportMeta"]["stale"] is False  # a name correction is not a new transcript


def test_reprocess_report_failure_keeps_old_report_and_marks_stale(sj, maxlog, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_REPORT_EMPTY", "1")
    store, jobs = sj
    old_pts = [{"id": "aaaaaaaa", "text": "舊子標題：舊說明", "group": "舊大標題", "src": []}]
    mk(store, summary={**copy.deepcopy(SUMMARY), "reportPoints": old_pts},
       reportMeta={"at": 1, "fp": "old", "stale": False})
    jobs.enqueue("ai", "m1", {"action": "reprocess", "correction": False, "slips": False, "adjudicate": False})
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    m = store.get("m1")
    assert m["summary"]["reportPoints"] == old_pts and m["reportMeta"]["stale"] is True


# ---------------------------------------------------------------- final fix wave
OLD_INTRO = [{"id": "iiiiiiii", "text": "舊引言", "src": []}]
OLD_PTS = [{"id": "aaaaaaaa", "text": "舊子標題：舊說明", "group": "舊大標題", "src": []}]
OLD_META = {"at": 1, "fp": ai._fingerprint(TRANSCRIPT), "stale": False}


def run_payload(store, jobs, payload, mid="m1"):
    jid = jobs.enqueue("ai", mid, payload)
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    return jid


def test_summary_keeps_existing_report(sj, maxlog):
    store, jobs = sj
    mk(store, summary={**copy.deepcopy(SUMMARY), "reportIntro": OLD_INTRO, "reportPoints": OLD_PTS},
       reportMeta=dict(OLD_META))
    jid = run_payload(store, jobs, {"action": "summary"})
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    m = store.get("m1")
    assert m["summary"]["reportIntro"] == OLD_INTRO and m["summary"]["reportPoints"] == OLD_PTS
    assert m["summary"]["mainPoints"] != SUMMARY["mainPoints"]  # the summary itself was replaced
    assert m["reportMeta"] == OLD_META


def test_report_success_clears_old_report_warning(sj, maxlog):
    store, jobs = sj
    mk(store, warnings=["完整整理失敗，已略過：舊錯誤", "名詞校正失敗，已略過：x"])
    jid = run_job(store, jobs)
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    assert store.get("m1")["warnings"] == ["名詞校正失敗，已略過：x"]
    mk(store, "m2", warnings=["完整整理失敗，已略過：舊錯誤"])
    run_job(store, jobs, "m2")
    assert "warnings" not in store.get("m2")


def test_reprocess_report_success_clears_old_report_warning(sj, maxlog):
    store, jobs = sj
    mk(store, warnings=["完整整理失敗，已略過：舊錯誤"])
    jid = run_payload(store, jobs, {"action": "reprocess", "correction": False, "slips": False, "adjudicate": False})
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    m = store.get("m1")
    assert m["summary"]["reportPoints"]
    assert not any(w.startswith("完整整理失敗") for w in m.get("warnings") or [])


def test_mode_switch_skips_report_and_keeps_old_one(sj, maxlog):
    store, jobs = sj
    mk(store, summary={**copy.deepcopy(SUMMARY), "reportIntro": OLD_INTRO, "reportPoints": OLD_PTS},
       reportMeta=dict(OLD_META), warnings=["完整整理失敗，已略過：舊錯誤"])
    jid = run_payload(store, jobs, {"action": "reprocess", "mode": "lecture", "correction": False, "slips": False,
                                    "report": False})
    assert jobs.get(jid)["state"] == "done", jobs.get(jid).get("error")
    m = store.get("m1")
    assert m["mode"] == "lecture"
    assert report_calls(maxlog) == []  # no REPORT call
    assert m["summary"]["reportIntro"] == OLD_INTRO and m["summary"]["reportPoints"] == OLD_PTS
    assert m["reportMeta"] == OLD_META  # transcript unchanged -> still fresh
    assert m["warnings"] == ["完整整理失敗，已略過：舊錯誤"]  # the step did not run: its note stays


def test_mode_route_sends_report_false(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.app import create_app
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setenv("MEETINGS_STATIC", str(static))
    app = create_app(tmp_path / "data", start_threads=False, claude_cmd=FAKE)
    c = TestClient(app)
    mk(app.state.store)
    r = c.post("/api/meetings/m1/mode", json={"mode": "lecture"})
    assert r.status_code == 200, r.text
    assert app.state.jobs.get(r.json()["jobId"])["payload"]["report"] is False
    # restore-raw still rebuilds the report
    app.state.store.update("m1", lambda m: m.update(status="done", aiJobId=None, transcriptRaw=copy.deepcopy(TRANSCRIPT)))
    r = c.post("/api/meetings/m1/restore-raw")
    assert r.status_code == 200, r.text
    assert app.state.jobs.get(r.json()["jobId"])["payload"].get("report", True) is not False
