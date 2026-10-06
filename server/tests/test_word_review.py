"""Word-review cards (docs/superpowers/specs/2026-10-06-word-review-design.md): adjudicate step,
normalisation / merge, accept / keep / edit / delete on transcript + summary + notes, no
resurrection after re-verify, leftover cards for partly applied corrections, gaps."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import prompts as P
from server import wordreview as W
from server.ai import handle_ai_job
from server.app import create_app
from server.gaps import compute_gaps, gaps_line
from server.jobs import JobQueue, apply_transcript_result
from server.render import meeting_to_markdown
from server.review import verify_review
from server.storage import Store

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
SEGS = [
    {"speaker": "說話者 1", "text": "黃任中怎麼死的？黃任宗當初賣掉公司拿到60億", "t": 10.0},
    {"speaker": "說話者 1", "text": "當時的股利所得48他要繳24億的稅，黃任宗不想繳", "t": 20.0},
    {"speaker": "說話者 2", "text": "Jefry 會負責報價，台積店很大", "t": 30.0},
    {"speaker": "說話者 1", "text": "台積店的鼓勵所得，台積店", "t": 100.0},
]
CORR = [{"wrong": "台積店", "right": "台積電", "confidence": "high", "reason": "r", "ts": [30]},
        {"wrong": "Jefry", "right": "Jeffrey", "confidence": "medium", "reason": "r", "ts": [30]}]
SLIPS = [{"t": 20, "quote": "股利所得48他要繳24億", "issue": "60億×48%≠24億", "suggestion": "依上下文疑為40%"}]


def rv(m, rid):
    return next(r for r in m["review"]["items"] if r["id"] == rid)


def cards(m, state=None):
    return [r for r in m["review"]["items"] if W.is_card(r) and (state is None or r["state"] == state)]


# ---------------------------------------------------------------- prompt
def test_adjudicate_prompt_and_schema():
    p = P.ADJUDICATE
    for s in ("黃任宗", "黃任中", "48%", "24億", "50億", "40億", "舊制不動產", "keep", "fromFlags", "逐字複製"):
        assert s in p
    item = P.ADJUDICATE_SCHEMA["properties"]["items"]["items"]
    assert set(item["required"]) == {"span", "action", "suggestion", "reason", "confidence", "itemIds", "fromFlags"}
    assert item["properties"]["action"]["enum"] == ["replace", "keep", "delete"]


# ---------------------------------------------------------------- pure fixtures
def grounded_meeting():
    m = {"id": "m1", "title": "t", "createdAt": 1, "mode": "lecture", "status": "done",
         "durationSec": 200.0, "transcript": [dict(s) for s in SEGS],
         "summary": {
             "cases": [{"id": "aaaaaaaa", "text": "黃任宗以當時48%的股利所得課稅要繳24億",
                        "src": [{"t": 20.0, "quote": "當時的股利所得48他要繳24億的稅"}]},
                       {"id": "bbbbbbbb", "text": "黃任宗成立境外公司，前後數字不一致",
                        "src": [{"t": 10.0, "quote": "黃任宗當初賣掉公司拿到60億"}]}],
             "mainPoints": [{"id": "cccccccc", "text": "黃任中案例：股利所得", "src": [{"t": 10.0, "quote": "黃任中怎麼死的"}]}],
         },
         "notes": {"concepts": [{"id": "dddddddd", "term": "黃任宗案", "plain": "股利所得變成黃任宗的證券交易所得",
                                 "why": "", "src": [{"t": 10.0, "quote": "黃任宗當初賣掉公司"}]}],
                   "quiz": [{"id": "eeeeeeee", "q": "黃任宗要繳多少？", "a": "24億",
                             "src": [{"t": 20.0, "quote": "他要繳24億的稅"}]}]},
         "corrections": {"items": [{"wrong": "Jefry", "right": "Jeffrey", "confidence": "medium",
                                    "count": 1, "ts": [30.0], "state": "pending"}]},
         }
    m["review"] = verify_review(m, [])
    return m


RAW = [
    # the AI's own finding: a person-name variant (everywhere: transcript, summary, notes)
    {"span": "黃任宗", "action": "replace", "suggestion": "黃任中", "reason": "[00:00:10] 逐字稿有「黃任中怎麼死的」",
     "confidence": "high", "itemIds": ["aaaaaaaa"], "fromFlags": []},
    # same span -> suggestion twice: one card
    {"span": "黃任宗", "action": "replace", "suggestion": "黃任中", "reason": "dup", "confidence": "low",
     "itemIds": [], "fromFlags": []},
    # the term flag
    {"span": "Jefry", "action": "replace", "suggestion": "Jeffrey", "reason": "[00:00:30]「Jefry 會負責報價」",
     "confidence": "medium", "itemIds": [], "fromFlags": ["TERM"]},
    # a span that does not exist anywhere -> dropped
    {"span": "王文洋", "action": "replace", "suggestion": "王文潮", "reason": "x", "confidence": "low",
     "itemIds": [], "fromFlags": []},
    # replace without suggestion -> keep (low)
    {"span": "股利所得", "action": "replace", "suggestion": "", "reason": "?", "confidence": "high",
     "itemIds": [], "fromFlags": []},
    # delete of an item fragment
    {"span": "，前後數字不一致", "action": "delete", "suggestion": "x", "reason": "總額與部分", "confidence": "high",
     "itemIds": ["bbbbbbbb"], "fromFlags": []},
    # delete of a transcript-only span -> keep (low)
    {"span": "怎麼死的", "action": "delete", "suggestion": "", "reason": "x", "confidence": "high",
     "itemIds": [], "fromFlags": []},
]


def with_cards(raw=RAW):
    m = grounded_meeting()
    term = next(r for r in m["review"]["items"] if r["kind"] == "term")
    raw = json.loads(json.dumps(raw).replace('"TERM"', json.dumps(term["id"])))
    W.apply_ai_cards(m, raw)
    return m, term["id"]


def card(m, span, action=None):
    return next(c for c in cards(m) if c["span"] == span and (action is None or c["action"] == action))


# ---------------------------------------------------------------- normalisation
def test_normalise_merge_drop_convert_and_targets():
    m, term_id = with_cards()
    cs = cards(m)
    assert sorted((c["span"], c["action"]) for c in cs) == sorted([
        ("黃任宗", "replace"), ("Jefry", "replace"), ("股利所得", "keep"), ("，前後數字不一致", "delete"),
        ("怎麼死的", "keep")])
    h = card(m, "黃任宗")
    assert h["confidence"] == "high" and h["reason"].startswith("[00:00:10]")  # best reason kept
    assert h["suggestion"] == "黃任中" and h["state"] == "pending" and h["origin"] == "ai"
    assert h["id"] == W.card_id("黃任宗", "replace", "黃任中") and h["sources"] == ["ai"]
    where = {(t["where"], t.get("itemId")): t["count"] for t in h["targets"]}
    assert where == {("transcript", None): 2, ("summary.cases", "aaaaaaaa"): 1, ("summary.cases", "bbbbbbbb"): 1,
                     ("notes.concepts", "dddddddd"): 2, ("notes.quiz", "eeeeeeee"): 1}
    assert "【黃任宗】" in h["context"] and len(h["context"]) <= 44
    assert h["section"] == "summary.cases" and h["itemId"] == "aaaaaaaa" and h["detail"] == h["reason"]
    assert card(m, "股利所得")["confidence"] == "low" and card(m, "股利所得")["suggestion"] == ""
    assert card(m, "怎麼死的")["action"] == "keep" and card(m, "怎麼死的")["confidence"] == "low"
    d = card(m, "，前後數字不一致")
    assert d["suggestion"] == "" and d["targets"] == [{"where": "summary.cases", "itemId": "bbbbbbbb", "count": 1}]
    # the term flag answered by the AI is merged into its card (not pending: not counted by the gate)
    j = card(m, "Jefry")
    assert rv(m, term_id)["state"] == "merged" and rv(m, term_id)["mergedInto"] == j["id"]
    assert j["fromFlags"] == [term_id] and j["sources"] == ["term"]
    assert j["targets"] == [{"where": "transcript", "count": 1}]


def test_unknown_flag_ids_and_bad_actions_ignored():
    m = grounded_meeting()
    W.apply_ai_cards(m, [{"span": "黃任宗", "action": "rewrite", "suggestion": "x"},
                         {"span": "黃任宗", "action": "keep", "fromFlags": ["nothere1"], "reason": "ok"}])
    (c,) = cards(m)
    assert c["action"] == "keep" and c["fromFlags"] == [] and c["confidence"] == "low"


# ---------------------------------------------------------------- endpoint
@pytest.fixture
def app_client(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, claude_cmd=FAKE)
    return app, TestClient(app)


def post(c, rid, **body):
    r = c.post(f"/api/meetings/m1/review/{rid}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_accept_updates_transcript_summary_and_notes(app_client):
    app, c = app_client
    m, _ = with_cards()
    app.state.store.save(m)
    out = post(c, card(m, "黃任宗")["id"], action="accept")
    assert out["applied"] == {"transcript": 2, "items": 4}
    assert out["transcript"][0]["text"] == "黃任中怎麼死的？黃任中當初賣掉公司拿到60億"
    assert out["transcript"][1]["text"].endswith("黃任中不想繳")
    assert out["transcriptRaw"][0]["text"] == SEGS[0]["text"]  # raw kept
    s = out["summary"]
    assert s["cases"][0]["text"] == "黃任中以當時48%的股利所得課稅要繳24億" and s["cases"][0]["id"] == "aaaaaaaa"
    assert s["cases"][1]["src"][0]["quote"] == "黃任中當初賣掉公司拿到60億"  # quote follows the transcript
    assert s["mainPoints"][0]["text"] == "黃任中案例：股利所得"  # untouched (already right)
    con = out["notes"]["concepts"][0]
    assert con["term"] == "黃任中案" and con["plain"] == "股利所得變成黃任中的證券交易所得"
    assert out["notes"]["quiz"][0]["q"] == "黃任中要繳多少？"
    h = rv(out, card(m, "黃任宗")["id"])
    assert h["state"] == "confirmed" and h["resolution"] == "accept"
    assert h["applied"] == {"transcript": 2, "items": 4}
    corr = {x["wrong"]: x for x in out["corrections"]["items"]}
    assert corr["黃任宗"]["state"] == "applied" and corr["黃任宗"]["right"] == "黃任中"
    # stored the same way; the response is the full meeting + applied
    st = app.state.store.get("m1")
    assert "applied" not in st and st["summary"] == out["summary"]


def test_accept_skips_matches_inside_existing_suggestion(app_client):
    app, c = app_client
    m = grounded_meeting()
    W.apply_ai_cards(m, [{"span": "黃任", "action": "replace", "suggestion": "黃任中", "reason": "r",
                          "confidence": "high", "itemIds": [], "fromFlags": []}])
    app.state.store.save(m)
    out = post(c, cards(m)[0]["id"], action="accept")
    t0 = out["transcript"][0]["text"]
    assert t0.startswith("黃任中怎麼死的") and "黃任中中" not in t0 and "黃任中宗" in t0


def test_keep_and_term_rejection(app_client):
    app, c = app_client
    m, term_id = with_cards()
    app.state.store.save(m)
    out = post(c, card(m, "Jefry")["id"], action="keep")
    assert out["applied"] == {"transcript": 0, "items": 0}
    assert out["transcript"][2]["text"].startswith("Jefry")
    j = rv(out, card(m, "Jefry")["id"])
    assert j["state"] == "confirmed" and j["resolution"] == "keep"
    assert out["corrections"]["items"][0]["state"] == "rejected"
    # the rejected correction no longer yields a term item; nothing resurrects as pending
    assert not [r for r in out["review"]["items"] if r["kind"] == "term" and r["state"] == "pending"]


def test_edit_uses_user_text(app_client):
    app, c = app_client
    m, _ = with_cards()
    app.state.store.save(m)
    out = post(c, card(m, "黃任宗")["id"], action="edit", text="黃仁中")
    assert out["applied"]["transcript"] == 2 and "黃仁中" in out["summary"]["cases"][0]["text"]
    e = rv(out, card(m, "黃任宗")["id"])
    assert e["state"] == "edited" and e["editedText"] == "黃仁中" and e["resolution"] == "edit"
    r = c.post(f"/api/meetings/m1/review/{card(m, 'Jefry')['id']}", json={"action": "edit", "text": " "})
    assert r.status_code == 400


def test_delete_span_from_items_only(app_client):
    app, c = app_client
    m, _ = with_cards()
    app.state.store.save(m)
    out = post(c, card(m, "，前後數字不一致")["id"], action="accept")
    assert out["applied"] == {"transcript": 0, "items": 1}
    assert out["summary"]["cases"][1]["text"] == "黃任宗成立境外公司"
    assert out["transcript"] == m["transcript"]


def test_no_resurrection_after_reverify_and_rerun(app_client):
    app, c = app_client
    m, term_id = with_cards()
    app.state.store.save(m)
    kid = card(m, "股利所得")["id"]
    post(c, kid, action="keep")
    out = post(c, card(m, "黃任宗")["id"], action="accept")
    assert rv(out, kid)["state"] == "confirmed"  # the pure re-verify keeps handled cards
    # a client PUT re-verifies too
    st = app.state.store.get("m1")
    st["summary"]["mainPoints"][0]["text"] = "黃任中案例：股利所得（改）"
    r = c.put("/api/meetings/m1", json=st)
    assert r.status_code == 200
    got = r.json()
    assert rv(got, kid)["state"] == "confirmed" and rv(got, card(m, "黃任宗")["id"])["state"] == "confirmed"
    # a new adjudicate run proposing the same keep again does not bring back a pending card
    W.apply_ai_cards(got, [{"span": "股利所得", "action": "keep", "suggestion": "", "reason": "again",
                            "confidence": "high", "itemIds": [], "fromFlags": []}])
    same = [x for x in cards(got) if x["span"] == "股利所得"]
    assert len(same) == 1 and same[0]["state"] == "confirmed"


def test_merged_flags_stay_merged_and_reopen_when_card_dropped():
    m = grounded_meeting()
    flags = [r for r in m["review"]["items"] if r["state"] == "pending"]
    assert flags
    raw = [{"span": "黃任宗", "action": "replace", "suggestion": "黃任中", "reason": "r", "confidence": "low",
            "itemIds": [], "fromFlags": [f["id"] for f in flags]}]
    W.apply_ai_cards(m, raw)
    assert all(rv(m, f["id"])["state"] == "merged" for f in flags)
    m["review"] = verify_review(m, [])  # pure re-verify: still merged
    assert all(rv(m, f["id"])["state"] == "merged" for f in flags)
    W.apply_ai_cards(m, [])  # e.g. adjudicate failed on a rerun: flags are open again
    assert all(rv(m, f["id"])["state"] == "pending" for f in flags) and not cards(m)


def test_legacy_actions_still_work_on_cards_and_flags(app_client):
    app, c = app_client
    m, term_id = with_cards()
    app.state.store.save(m)
    out = post(c, card(m, "Jefry")["id"], action="confirm")  # legacy confirm on a card = accept
    assert out["transcript"][2]["text"].startswith("Jeffrey") and out["applied"]["transcript"] == 1
    calc_like = [r for r in out["review"]["items"] if not W.is_card(r) and r["state"] == "pending"]
    if calc_like:
        r = c.post(f"/api/meetings/m1/review/{calc_like[0]['id']}", json={"action": "keep"})
        assert r.status_code == 200 and rv(r.json(), calc_like[0]["id"])["state"] == "confirmed"
        assert "applied" not in r.json()
    assert c.post(f"/api/meetings/m1/review/{term_id}", json={"action": "bogus"}).status_code == 400


# ---------------------------------------------------------------- leftovers
def test_leftover_card_for_partly_applied_correction(app_client):
    app, c = app_client
    m = grounded_meeting()
    m["transcript"][2]["text"] = "Jefry 會負責報價，台積電很大"
    m["corrections"]["items"].append({"wrong": "台積店", "right": "台積電", "confidence": "high", "count": 1,
                                      "ts": [30.0], "state": "applied"})
    m["review"] = verify_review(m, [])
    lo = [x for x in cards(m) if x["origin"] == "leftover"]
    assert len(lo) == 1
    x = lo[0]
    assert (x["span"], x["action"], x["suggestion"]) == ("台積店", "replace", "台積電")
    assert x["reason"].startswith("已自動改 1 處「台積店」→「台積電」，另有 2 處未改")
    assert x["targets"] == [{"where": "transcript", "count": 2}] and x["sources"] == ["term"]
    app.state.store.save(m)
    out = post(c, x["id"], action="accept")
    assert out["transcript"][3]["text"] == "台積電的鼓勵所得，台積電" and out["applied"]["transcript"] == 2
    assert rv(out, x["id"])["state"] == "confirmed"
    assert len([y for y in cards(out) if y["span"] == "台積店"]) == 1  # not regenerated


# ---------------------------------------------------------------- pipeline
@pytest.fixture
def sj(tmp_path):
    store = Store(tmp_path / "data")
    return store, JobQueue(store)


def run_pipeline(store, jobs, monkeypatch, adjudicate, **env):
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(CORR, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_SLIPS", json.dumps(SLIPS, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_KIND", "lecture")
    monkeypatch.setenv("FAKE_CLAUDE_ADJUDICATE", json.dumps(adjudicate, ensure_ascii=False))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    jid = jobs.enqueue("transcribe", "m1", {})
    store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "transcribing", "jobId": jid,
                "transcript": [], "summary": None})
    job = jobs.claim(["transcribe"], "gpu-worker")
    assert apply_transcript_result(store, jobs, job, {"segments": SEGS, "durationSec": 200, "engine": "gpu"})
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    return store.get("m1")


def test_pipeline_adjudicate_cards(sj, monkeypatch, tmp_path):
    store, jobs = sj
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    adj = [{"span": "Jefry", "action": "replace", "suggestion": "Jeffrey", "reason": "[00:00:30]「Jefry」",
            "confidence": "medium", "itemIds": [], "fromFlags": ["*term"]},
           {"span": "股利所得48他要繳24億", "action": "keep", "suggestion": "", "reason": "請回聽 [00:00:20]",
            "confidence": "low", "itemIds": [], "fromFlags": ["*slip"]},
           {"span": "講座", "action": "replace", "suggestion": "演講", "reason": "r", "confidence": "high",
            "itemIds": [], "fromFlags": []}]
    m = run_pipeline(store, jobs, monkeypatch, adj)
    assert m["status"] == "done" and "warnings" not in m
    # the adjudicate call got flags, entries with their context and the name list
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    adj_calls = [c for c in calls if '"span"' in c["argv"][c["argv"].index("--json-schema") + 1]]
    assert len(adj_calls) == 1
    stdin = adj_calls[0]["stdin"]
    assert "人名與異寫清單" in stdin and "[I:" in stdin and "] term：" in stdin and "] slip：" in stdin
    assert "前後逐字稿" in stdin and "--model" in adj_calls[0]["argv"]
    j = card(m, "Jefry")
    term = next(r for r in m["review"]["items"] if r["kind"] == "term")
    slip = next(r for r in m["review"]["items"] if r["kind"] == "slip")
    assert term["state"] == "merged" and term["mergedInto"] == j["id"]
    assert slip["state"] == "merged" and slip["mergedInto"] == card(m, "股利所得48他要繳24億")["id"]
    k = card(m, "講座")
    assert k["targets"][0]["where"].startswith("summary.") and sum(t["count"] for t in k["targets"]) == 6
    # 台積店 was auto-applied at ±15 s of 30 s only: the occurrences at 100 s become a leftover card
    lo = [x for x in cards(m) if x["origin"] == "leftover"]
    assert [x["span"] for x in lo] == ["台積店"] and lo[0]["targets"] == [{"where": "transcript", "count": 2}]
    # gaps saved with the transcript, shown in the md header
    assert m["gaps"] and m["gaps"][-1]["end"] == 200.0
    assert "未辨識" in meeting_to_markdown(m)


def test_pipeline_adjudicate_failure_keeps_flags(sj, monkeypatch):
    store, jobs = sj
    m = run_pipeline(store, jobs, monkeypatch, [], FAKE_CLAUDE_ADJUDICATE_FAIL="1")
    assert m["status"] == "done" and m["error"] is None
    assert any(w.startswith("AI 審稿失敗，已略過") for w in m["warnings"])
    assert next(r for r in m["review"]["items"] if r["kind"] == "term")["state"] == "pending"
    assert next(r for r in m["review"]["items"] if r["kind"] == "slip")["state"] == "pending"
    assert not [x for x in cards(m) if x["origin"] == "ai"]


# ---------------------------------------------------------------- gaps
def test_compute_gaps_leading_between_trailing():
    segs = [{"text": "a" * 50, "t": 30.0},       # leading gap 0–30; ends 40
            {"text": "短", "t": 50.0},            # 10 s after: no gap; ends 52
            {"text": "x" * 100, "t": 80.0},       # gap 52–80; ends 100
            {"text": "y", "t": 110.0, "end": 115.0}]
    assert compute_gaps(segs, 200.0) == [{"start": 0.0, "end": 30.0}, {"start": 52.0, "end": 80.0},
                                         {"start": 115.0, "end": 200.0}]
    assert compute_gaps(segs, 120.0)[-1] == {"start": 52.0, "end": 80.0}
    assert compute_gaps([], 100) == [] and compute_gaps(None) == []
    line = gaps_line(compute_gaps(segs, 200.0))
    assert line == "⚠️ 約 2.4 分鐘未辨識：00:00:00–00:00:30、00:00:52–00:01:20、00:01:55–00:03:20"
    many = [{"start": i * 100.0, "end": i * 100.0 + 30} for i in range(12)]
    assert gaps_line(many).endswith("…等共 12 段") and gaps_line(many).count("–") == 10
    assert gaps_line([]) == ""


def test_gaps_on_save_and_in_exports(tmp_path):
    from server.render import meeting_to_docx
    store = Store(tmp_path / "data")
    m = store.save({"id": "g1", "title": "t", "createdAt": 1, "durationSec": 100.0,
                    "transcript": [{"speaker": "A", "text": "你好", "t": 40.0}]})
    assert m["gaps"] == [{"start": 0.0, "end": 40.0}, {"start": 42.0, "end": 100.0}]
    md = (tmp_path / "data" / "meetings" / "g1.md").read_text(encoding="utf-8")
    assert "> ⚠️ 約 1.6 分鐘未辨識：00:00:00–00:00:40、00:00:42–00:01:40" in md
    from docx import Document
    meeting_to_docx(m, tmp_path / "g.docx")
    assert any("未辨識" in p.text for p in Document(str(tmp_path / "g.docx")).paragraphs)
    m["transcript"] = []
    assert "gaps" not in store.save(m)


def test_name_list_shows_transcript_variants():
    lines = W.name_list(grounded_meeting())
    assert any(x.startswith("- 黃任宗：逐字稿 2 次") and "黃任中 1 次" in x for x in lines)


def test_single_char_replace_becomes_keep():
    m = grounded_meeting()
    W.apply_ai_cards(m, [{"span": "宗", "action": "replace", "suggestion": "中", "reason": "x", "confidence": "high",
                          "itemIds": [], "fromFlags": []}])
    (c,) = cards(m)
    assert c["action"] == "keep" and c["confidence"] == "low" and c["suggestion"] == ""


def test_user_keep_answers_later_keep_card_for_same_span(app_client):
    app, c = app_client
    m, _ = with_cards()
    app.state.store.save(m)
    hid = card(m, "黃任宗")["id"]
    out = post(c, hid, action="keep")  # the user keeps the original on a replace card
    assert rv(out, hid)["state"] == "confirmed"
    W.apply_ai_cards(out, [{"span": "黃任宗", "action": "keep", "suggestion": "", "reason": "again",
                            "confidence": "high", "itemIds": [], "fromFlags": []}])
    assert [x["state"] for x in cards(out) if x["span"] == "黃任宗"] == ["confirmed"]


# ---------------------------------------------------------------- accept-high
HIGH_RAW = [
    {"span": "台積店", "action": "replace", "suggestion": "台積電", "reason": "r", "confidence": "high",
     "itemIds": [], "fromFlags": []},
    {"span": "股利所得", "action": "keep", "suggestion": "", "reason": "ok", "confidence": "high",
     "itemIds": [], "fromFlags": []},
    {"span": "黃任宗", "action": "replace", "suggestion": "黃任中", "reason": "name", "confidence": "high",
     "itemIds": ["aaaaaaaa"], "fromFlags": []},
    {"span": "Jefry", "action": "replace", "suggestion": "Jeffrey", "reason": "r", "confidence": "medium",
     "itemIds": [], "fromFlags": []},
    {"span": "，前後數字不一致", "action": "delete", "suggestion": "", "reason": "r", "confidence": "low",
     "itemIds": ["bbbbbbbb"], "fromFlags": []},
]


def test_accept_high_applies_only_high_non_name(app_client):
    app, c = app_client
    m, _ = with_cards(HIGH_RAW)
    app.state.store.save(m)
    r = c.post("/api/meetings/m1/review/accept-high")
    assert r.status_code == 200, r.text
    out = r.json()
    # the keep card (股利所得) was auto-confirmed by the AI already: only 台積店 is applied
    assert out["applied"]["cards"] == 1 and out["applied"]["transcript"] == 3  # 台積店 x3 (30s, 100s x2)
    assert out["transcript"][2]["text"].count("台積電") == 1 and "台積店" not in out["transcript"][3]["text"]
    assert rv(out, card(m, "台積店")["id"])["state"] == "confirmed"
    assert rv(out, card(m, "股利所得")["id"])["state"] == "confirmed"
    for sp in ("黃任宗", "Jefry", "，前後數字不一致"):  # name / medium / low stay pending
        assert rv(out, card(m, sp)["id"])["state"] == "pending"
    assert "黃任宗" in out["transcript"][0]["text"]
    assert "applied" not in app.state.store.get("m1")
    # nothing eligible left: a second call is a no-op
    again = c.post("/api/meetings/m1/review/accept-high").json()
    assert again["applied"] == {"cards": 0, "transcript": 0, "items": 0}


def test_accept_high_no_resurrection_and_name_flag_excluded(app_client):
    app, c = app_client
    m, _ = with_cards(HIGH_RAW)
    for x in m["review"]["items"]:
        if W.is_card(x) and x["span"] == "台積店":
            x["sources"] = ["name"]
    app.state.store.save(m)
    out = c.post("/api/meetings/m1/review/accept-high").json()
    assert out["applied"]["cards"] == 0
    tid = card(m, "台積店")["id"]
    assert rv(out, tid)["state"] == "pending"
    st = app.state.store.get("m1")
    for x in st["review"]["items"]:
        if x.get("id") == tid:
            x["sources"] = ["term"]
    app.state.store.save(st)
    assert c.post("/api/meetings/m1/review/accept-high").json()["applied"]["cards"] == 1
    st = app.state.store.get("m1")
    got = c.put("/api/meetings/m1", json=st).json()
    assert rv(got, tid)["state"] == "confirmed"


def test_accept_high_409_while_job_active(app_client):
    app, c = app_client
    m, _ = with_cards(HIGH_RAW)
    app.state.store.save(m)
    assert c.post("/api/meetings/m1/ai", json={"action": "reprocess"}).status_code == 200
    r = c.post("/api/meetings/m1/review/accept-high")
    assert r.status_code == 409 and r.json()["detail"] == "整理中，請稍後再確認"


# ---------------------------------------------------------------- checklist (v107)
def test_ai_keep_cards_auto_confirmed_not_counted():
    from server.verify import pending_count
    m, _ = with_cards()
    for sp in ("股利所得", "怎麼死的"):
        k = card(m, sp)
        assert k["action"] == "keep" and k["state"] == "confirmed" and k["resolvedBy"] == "ai"
        assert k["resolution"] == "keep" and k["keepReason"] == k["reason"]
    assert all(r["action"] != "keep" for r in cards(m, "pending"))
    assert len(cards(m, "pending")) == 3
    assert pending_count(m["review"]) == len([r for r in m["review"]["items"] if r["state"] == "pending"])
    assert not [row for row in W.review_rows(m["review"]["items"]) if row["from"] in ("股利所得", "怎麼死的")]
    m["review"] = verify_review(m, [])  # a re-verify keeps them confirmed
    assert card(m, "股利所得")["state"] == "confirmed"


def test_existing_pending_keep_cards_migrated_on_read_and_reverify(app_client):
    app, c = app_client
    m, _ = with_cards()
    for r in m["review"]["items"]:  # an old meeting: keep cards still pending, no word pairs
        if W.is_card(r):
            r.pop("from", None)
            r.pop("to", None)
            if r["action"] == "keep":
                for k in ("resolvedBy", "resolution", "keepReason", "decidedAt"):
                    r.pop(k, None)
                r["state"] = "pending"
    app.state.store.save(m)
    assert len([r for r in cards(app.state.store.get("m1"), "pending") if r["action"] == "keep"]) == 2
    got = c.get("/api/meetings/m1").json()
    k = card(got, "股利所得")
    assert k["state"] == "confirmed" and k["resolvedBy"] == "ai"
    assert (card(got, "黃任宗")["from"], card(got, "黃任宗")["to"]) == ("黃任宗", "黃任中")
    st = app.state.store.get("m1")  # saved, not only shown
    assert card(st, "怎麼死的")["state"] == "confirmed"
    # the pure re-verify path cleans an old meeting too
    m2, _ = with_cards()
    for r in m2["review"]["items"]:
        if W.is_card(r) and r["action"] == "keep":
            r["state"] = "pending"
    m2["review"] = verify_review(m2, [])
    assert card(m2, "股利所得")["state"] == "confirmed"


@pytest.mark.parametrize("span,sug,action,want", [
    ("討論稅", "逃漏稅", "replace", ("討論稅", "逃漏稅")),
    ("李專", "理專", "replace", ("李專", "理專")),
    ("所以選對專案你就自打嘴巴了", "所以選對專案你就打自己嘴巴了", "replace", ("自打", "打自己")),
    ("我們今天要討論稅的問題喔", "我們今天要逃漏稅的問題喔", "replace", ("討論", "逃漏")),
    ("黃任宗當初賣掉公司拿到", "黃任中當初賣掉公司拿到", "replace", ("任宗", "任中")),
    ("選對專案你就自打嘴巴了真的", "", "delete", ("選對專案你就自打嘴巴了…", "（刪除）")),
    ("嗯那個", "", "delete", ("嗯那個", "（刪除）")),
])
def test_word_pair_diff(span, sug, action, want):
    frm, to = W.word_pair(span, sug, action)
    assert (frm, to) == want and len(frm) <= 12 and len(to) <= 12


def test_word_pair_stored_and_rows_merge_same_pair_across_spans():
    m = grounded_meeting()
    W.apply_ai_cards(m, [
        {"span": "黃任宗當初", "action": "replace", "suggestion": "黃任中當初", "reason": "r1", "confidence": "high",
         "itemIds": [], "fromFlags": []},
        {"span": "黃任宗不想繳", "action": "replace", "suggestion": "黃任中不想繳", "reason": "r2", "confidence": "low",
         "itemIds": [], "fromFlags": []},
        {"span": "，前後數字不一致", "action": "delete", "suggestion": "", "reason": "r", "confidence": "high",
         "itemIds": [], "fromFlags": []}])
    a, b = card(m, "黃任宗當初"), card(m, "黃任宗不想繳")
    assert (a["from"], a["to"]) == ("任宗", "任中") == (b["from"], b["to"])
    rows = W.review_rows(m["review"]["items"])
    row = next(x for x in rows if x["from"] == "任宗")
    assert row["cards"] == [a["id"], b["id"]] and row["confidence"] == "low"
    assert row["count"] == W.card_count(a) + W.card_count(b) and row["id"] == W.row_id("任宗", "任中")
    d = next(x for x in rows if x["to"] == "（刪除）")
    assert d["from"] == "，前後數字不一致" and d["action"] == "delete"


def test_apply_batch_accept_and_keep_counts(app_client):
    app, c = app_client
    m = grounded_meeting()
    W.apply_ai_cards(m, [
        {"span": "黃任宗當初", "action": "replace", "suggestion": "黃任中當初", "reason": "r1", "confidence": "high",
         "itemIds": [], "fromFlags": []},
        {"span": "黃任宗不想繳", "action": "replace", "suggestion": "黃任中不想繳", "reason": "r2", "confidence": "high",
         "itemIds": [], "fromFlags": []},
        {"span": "Jefry", "action": "replace", "suggestion": "Jeffrey", "reason": "r", "confidence": "medium",
         "itemIds": [], "fromFlags": []},
        {"span": "，前後數字不一致", "action": "delete", "suggestion": "", "reason": "r", "confidence": "high",
         "itemIds": [], "fromFlags": []}])
    app.state.store.save(m)
    row = next(x for x in W.review_rows(m["review"]["items"]) if x["from"] == "任宗")
    jid, did = card(m, "Jefry")["id"], card(m, "，前後數字不一致")["id"]
    r = c.post("/api/meetings/m1/review/apply-batch", json={"accept": [row["id"], did], "keep": [jid]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["applied"] == {"cards": 3, "kept": 1, "transcript": 2, "items": 1}
    assert out["transcript"][0]["text"].endswith("黃任中當初賣掉公司拿到60億")
    assert out["transcript"][1]["text"].endswith("黃任中不想繳")
    assert out["transcript"][2]["text"].startswith("Jefry")
    assert out["summary"]["cases"][1]["text"] == "黃任宗成立境外公司"
    for cid in row["cards"] + [did]:
        assert rv(out, cid)["state"] == "confirmed" and rv(out, cid)["resolution"] == "accept"
    assert rv(out, jid)["state"] == "confirmed" and rv(out, jid)["resolution"] == "keep"
    assert not cards(out, "pending") and "applied" not in app.state.store.get("m1")
    # unknown / already-settled ids are ignored
    again = c.post("/api/meetings/m1/review/apply-batch", json={"accept": [jid, "nope"], "keep": []}).json()
    assert again["applied"] == {"cards": 0, "kept": 0, "transcript": 0, "items": 0}
    assert c.post("/api/meetings/m1/review/apply-batch", json={"accept": "x"}).status_code == 400
    assert c.post("/api/meetings/m1/review/accept-high").status_code == 200  # old endpoint still there


def test_apply_batch_409_while_job_active(app_client):
    app, c = app_client
    m, _ = with_cards(HIGH_RAW)
    app.state.store.save(m)
    assert c.post("/api/meetings/m1/ai", json={"action": "reprocess"}).status_code == 200
    r = c.post("/api/meetings/m1/review/apply-batch", json={"accept": [card(m, "台積店")["id"]], "keep": []})
    assert r.status_code == 409 and r.json()["detail"] == "整理中，請稍後再確認"
