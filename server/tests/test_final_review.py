"""Final whole-branch review fixes (2026-10-06 chairman quality): nothing unverified reaches the chairman.

C1  a client PUT that changes summary / notes / transcript is re-verified on the server
I3  server-written exports (exports/<id>.docx, meetings/<id>.md) obey the 待確認 gate
I4  itemless review items (slip ...) can only be confirmed
M1  failed / cancelled reprocess and a new transcription re-verify the review
M2  restore-raw keeps the pre-restore transcript in alt["transcript:beforeRestore"]
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.ai import handle_ai_job
from server.jobs import apply_transcript_result
from server.review import verify_review
from server.verify import review_item

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
SEGS = [
    {"speaker": "說話者 1", "text": "我們談台積店的案子，台積店很大", "t": 10.0},
    {"speaker": "說話者 2", "text": "Jefry 會負責報價", "t": 20.0},
    {"speaker": "說話者 1", "text": "只要繳五千塊就好", "t": 30.0},
]
CORR = [{"wrong": "台積店", "right": "台積電", "confidence": "high", "reason": "r", "ts": [10]},
        {"wrong": "Jefry", "right": "Jeffrey", "confidence": "medium", "reason": "r", "ts": [20]}]
NO_SRC = "沒有附逐字稿原句，請確認。"


@pytest.fixture
def app_client(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, claude_cmd=FAKE)
    return app, TestClient(app)


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(CORR, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_KIND", "lecture")


def grounded(**kw):
    """A meeting through the grounded pipeline: every entry has a quote that is in the transcript;
    one confirmed review item and one pending slip; alt holds the pre-grounding (source-less) list."""
    items = [{"id": "aaaaaaaa", "text": "Jefry 負責報價", "src": [{"t": 20.0, "quote": "Jefry 會負責報價"}]}]
    slip = review_item("slip", "transcript", None, "只要繳五千塊就好", "口誤", t=30.0)
    m = {"id": "m1", "title": "t", "createdAt": 1, "status": "done", "mode": "meeting",
         "transcript": [dict(s) for s in SEGS],
         "summary": {"actionItems": [], "mainPoints": items, "qa": []},
         "review": {"items": [slip], "at": 1},
         "alt": {"summary:mainPoints": ["自己補的一句話", "另一句沒有出處"]}}
    m.update(kw)
    return m, slip


def pending(m):
    return [r for r in m["review"]["items"] if r["state"] == "pending"]


# ---------------------------------------------------------------- C1
def test_swap_to_unverified_alt_list_is_flagged_and_export_blocked(app_client):
    app, c = app_client
    m, slip = grounded()
    app.state.store.save(m)
    store = app.state.store
    # the slip is pending: nothing exported yet; confirm it -> docx exists
    out = c.post(f"/api/meetings/m1/review/{slip['id']}", json={"action": "confirm"}).json()
    assert pending(out) == []
    assert store.path("exports", "m1.docx").exists()

    # 換版 in the browser: alt list (no sources) back into the summary, whole-meeting PUT
    body = c.get("/api/meetings/m1").json()
    body["summary"]["mainPoints"], body["alt"]["summary:mainPoints"] = (
        body["alt"]["summary:mainPoints"], body["summary"]["mainPoints"])
    r = c.put("/api/meetings/m1", json=body)
    assert r.status_code == 200
    got = r.json()
    p = pending(got)
    assert len(p) == 2 and {x["kind"] for x in p} == {"quote"}
    assert {x["text"] for x in p} == {"自己補的一句話", "另一句沒有出處"}
    assert all(x["detail"] == NO_SRC for x in p)
    # the stored meeting agrees and the server-side export is gated (I3)
    assert len(pending(store.get("m1"))) == 2
    assert not store.path("exports", "m1.docx").exists()
    md = store.path("meetings", "m1.md").read_text(encoding="utf-8")
    assert md.splitlines()[0] == "⚠️ 尚有 2 項待確認，內容未經確認，請勿轉交"
    # the confirmed slip stays confirmed
    assert next(x for x in got["review"]["items"] if x["id"] == slip["id"])["state"] == "confirmed"


def test_put_drops_pending_of_removed_entries_keeps_slips(app_client):
    app, c = app_client
    m, slip = grounded()
    m["summary"]["mainPoints"].append({"id": "bbbbbbbb", "text": "營收三億", "src": [{"t": 30.0, "quote": "只要繳五千塊就好"}]})
    app.state.store.save(m)
    body = c.get("/api/meetings/m1").json()
    body["review"] = {"items": []}  # client copy of the review is ignored anyway
    c.put("/api/meetings/m1", json=dict(body, title="x"))  # title only: no re-verify
    assert [x["id"] for x in app.state.store.get("m1")["review"]["items"]] == [slip["id"]]

    body = c.get("/api/meetings/m1").json()
    body["summary"]["mainPoints"].append({"id": "cccccccc", "text": "營收三億", "src": [{"t": 30.0, "quote": "只要繳五千塊就好"}]})
    got = c.put("/api/meetings/m1", json=body).json()
    assert any(x["kind"] == "number" and x["itemId"] == "cccccccc" for x in pending(got))
    # manual delete of that entry: its pending doubt goes, the slip stays
    body = c.get("/api/meetings/m1").json()
    body["summary"]["mainPoints"] = [x for x in body["summary"]["mainPoints"] if x.get("id") != "cccccccc"]
    got = c.put("/api/meetings/m1", json=body).json()
    assert not any(x.get("itemId") == "cccccccc" for x in got["review"]["items"])
    assert any(x["id"] == slip["id"] and x["state"] == "pending" for x in got["review"]["items"])


def test_put_transcript_edit_reverifies_quotes(app_client):
    app, c = app_client
    m, slip = grounded()
    app.state.store.save(m)
    body = c.get("/api/meetings/m1").json()
    body["transcript"][1]["text"] = "完全不同的一句"
    got = c.put("/api/meetings/m1", json=body).json()
    assert any(x["kind"] == "quote" and x["itemId"] == "aaaaaaaa" for x in pending(got))


def test_put_legacy_meeting_keeps_legacy_exemption(app_client):
    app, c = app_client
    m = {"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": [dict(s) for s in SEGS],
         "summary": {"actionItems": ["舊待辦"], "mainPoints": ["舊重點"], "qa": []}}
    app.state.store.save(m)
    body = c.get("/api/meetings/m1").json()
    body["summary"]["mainPoints"] = ["改過的舊重點"]
    got = c.put("/api/meetings/m1", json=body).json()
    assert not any(x["kind"] == "quote" for x in got["review"]["items"])  # never reprocessed: no quote flag


# ---------------------------------------------------------------- I3
def test_exports_gated_until_all_handled(app_client):
    app, c = app_client
    store = app.state.store
    m, slip = grounded()
    store.save(m)
    assert not store.path("exports", "m1.docx").exists()
    assert store.path("meetings", "m1.md").read_text(encoding="utf-8").startswith("⚠️ 尚有 1 項待確認")
    c.post(f"/api/meetings/m1/review/{slip['id']}", json={"action": "confirm"})
    assert store.path("exports", "m1.docx").exists()
    assert not store.path("meetings", "m1.md").read_text(encoding="utf-8").startswith("⚠️")


def test_bot_skill_checks_docx_before_sending():
    skill = (Path(__file__).resolve().parents[2] / "bot-skill" / "meetings-db" / "SKILL.md").read_text(encoding="utf-8")
    assert "這場還有 N 項待確認，請先到網頁處理完再傳" in skill
    assert "exports/<id>.docx" in skill and "state" in skill


# ---------------------------------------------------------------- I4
def test_slip_only_confirm(app_client):
    app, c = app_client
    m, slip = grounded()
    app.state.store.save(m)
    for body in ({"action": "edit", "text": "改"}, {"action": "delete"}):
        r = c.post(f"/api/meetings/m1/review/{slip['id']}", json=body)
        assert r.status_code == 400 and "知道了" in r.json()["detail"]
    assert pending(app.state.store.get("m1"))[0]["id"] == slip["id"]
    assert c.post(f"/api/meetings/m1/review/{slip['id']}", json={"action": "confirm"}).status_code == 200


# ---------------------------------------------------------------- M1 / M2
def transcribed(store, jobs, mid="m1", segs=SEGS, **kw):
    jid = jobs.enqueue("transcribe", mid, {})
    m = {"id": mid, "title": "t", "createdAt": 1, "status": "transcribing", "jobId": jid,
         "transcript": [], "summary": None}
    m.update(kw)
    store.save(m)
    job = jobs.claim(["transcribe"], "gpu-worker")
    assert apply_transcript_result(store, jobs, job, {"segments": segs, "durationSec": 40, "engine": "gpu"})
    return store.get(mid)["aiJobId"]


def run_claimed(store, jobs):
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)


def test_restore_then_cancel_reverifies_and_keeps_before_restore(app_client, fake_env):
    app, c = app_client
    store, jobs = app.state.store, app.state.jobs
    transcribed(store, jobs)
    run_claimed(store, jobs)
    m = store.get("m1")
    term = next(x for x in m["review"]["items"] if x["kind"] == "term")  # Jefry (medium)
    c.post(f"/api/meetings/m1/review/{term['id']}", json={"action": "confirm"})
    before = store.get("m1")["transcript"]
    assert "Jeffrey" in before[1]["text"]
    jid = c.post("/api/meetings/m1/restore-raw").json()["jobId"]
    m = store.get("m1")
    assert m["alt"]["transcript:beforeRestore"] == before  # M2
    assert not any(x["kind"] == "term" for x in m["review"]["items"])  # removed by restore
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200
    m = store.get("m1")
    assert m["status"] == "done"
    terms = sorted(x["text"] for x in pending(m) if x["kind"] == "term")
    assert terms == ["Jefry", "台積店"]  # rebuilt from the reset corrections: export stays locked
    # restore again: the one slot is overwritten with the latest pre-restore transcript
    store.update("m1", lambda x: x["transcript"][0].update(text="手動改過"))
    jid = c.post("/api/meetings/m1/restore-raw").json()["jobId"]
    assert store.get("m1")["alt"]["transcript:beforeRestore"][0]["text"] == "手動改過"


def test_new_transcription_drops_stale_review_keeps_confirmed(app_client, fake_env):
    app, c = app_client
    store, jobs = app.state.store, app.state.jobs
    transcribed(store, jobs)
    run_claimed(store, jobs)

    def add_entry(x):
        x["summary"]["mainPoints"].append(
            {"id": "dddddddd", "text": "營收三億", "src": [{"t": 30.0, "quote": "只要繳五千塊就好"}]})
        x["review"] = verify_review(x, [])
    store.update("m1", add_entry)
    m = store.get("m1")
    term = next(x for x in pending(m) if x["kind"] == "term")
    num = next(x for x in pending(m) if x["kind"] == "number" and x.get("itemId") == "dddddddd")
    c.post(f"/api/meetings/m1/review/{num['id']}", json={"action": "confirm"})
    # re-transcription lands: corrections are gone -> the term doubt is stale
    jid = jobs.enqueue("transcribe", "m1", {})
    store.update("m1", lambda x: x.update(status="transcribing", jobId=jid))
    job = jobs.claim(["transcribe"], "gpu-worker")
    assert apply_transcript_result(store, jobs, job, {"segments": SEGS, "durationSec": 40, "engine": "gpu"})
    m = store.get("m1")
    ids = {x["id"]: x for x in m["review"]["items"]}
    assert term["id"] not in ids and "corrections" not in m
    assert ids[num["id"]]["state"] == "confirmed"
