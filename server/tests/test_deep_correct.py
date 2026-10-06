"""Mishearing recall: windowed exhaustive CORRECTION, global merge, adjudicate re-ask, 加強校對 (deep_correct)."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import ai
from server import wordreview as W
from server.ai import (Ctx, adjudicate_cards, correction_windows, handle_ai_job, merge_window_proposals,
                       transcript_lines)
from server.app import create_app
from server.items import hash8
from server.verify import review_item

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def long_segs(n=30, width=290):
    """n segments of ~width chars, one every 20 s; segment 3 says 寶兒大, segment 25 says 園大."""
    out = []
    for i in range(n):
        text = f"第{i}段" + "內容" * (width // 2)
        if i == 3:
            text = "這張保單寶兒大，" + text
        if i == 25:
            text = "我們在園大開戶，" + text
        out.append({"speaker": "說話者 1", "text": text, "t": float(i * 20)})
    return out


# ---------------------------------------------------------------- windows
def test_correction_windows_cover_everything_with_overlap():
    lines = transcript_lines(long_segs())
    wins = correction_windows(lines, 3500, 300)
    assert len(wins) >= 3 and wins[0][0] == 0 and wins[-1][1] == len(lines)
    for (a1, b1), (a2, b2) in zip(wins, wins[1:]):
        assert a1 < a2 < b1 <= b2  # progress, and the next window repeats the previous tail
        tail = sum(len(x) + 1 for x in lines[a2:b1])
        assert tail >= 300
    for a, b in wins:
        assert b - a == 1 or sum(len(x) + 1 for x in lines[a:b]) <= 3500
    assert correction_windows([], 3500, 300) == []
    assert correction_windows(["x" * 9000], 3500, 300) == [(0, 1)]  # one over-long line: its own window
    assert correction_windows(["a" * 10, "b" * 10], 3500, 300) == [(0, 2)]


# ---------------------------------------------------------------- global merge
def test_merge_window_proposals_union_variants_conflicts_ts():
    segs = [{"text": "證明稅要繳", "t": 0.0}, {"text": "證據稅的問題", "t": 10.0}, {"text": "鑾高的", "t": 20.0},
            {"text": "還是鑾高", "t": 30.0}, {"text": "鑾高啦", "t": 40.0}, {"text": "沒有錯字", "t": 50.0}]
    wins = [(0, 3), (2, 5), (4, 6)]
    per = [
        [{"wrong": "證明稅", "right": "贈與稅", "confidence": "high", "reason": "a", "ts": [999]},
         {"wrong": "證據稅", "right": "贈予稅", "confidence": "medium", "reason": "b", "ts": []},
         {"wrong": "鑾高", "right": "蠻高", "confidence": "medium", "reason": "c", "ts": [20]},
         {"wrong": "不存在", "right": "某詞", "confidence": "high", "reason": "d", "ts": [0]}],
        [{"wrong": "鑾高", "right": "蠻高", "confidence": "high", "reason": "e", "ts": [30]},
         {"wrong": "證明稅", "right": "贈與稅", "confidence": "high", "reason": "x", "ts": [0]}],  # not in window 2
        [{"wrong": "鑾高", "right": "巒高", "confidence": "high", "reason": "f", "ts": [40]}],
    ]
    out = {p["wrong"]: p for p in merge_window_proposals(per, wins, segs, ["贈與稅"])}
    assert set(out) == {"證明稅", "證據稅", "鑾高"}  # 不存在 (no occurrence) dropped
    assert out["證明稅"]["right"] == "贈與稅" and out["證明稅"]["ts"] == [0.0]  # ts from the transcript
    assert out["證據稅"]["right"] == "贈與稅"  # 贈予稅 is a spelling variant of the 詞庫 term
    # conflict: 蠻高 proposed by 2 windows, 巒高 by 1 -> majority; best confidence + its reason kept
    b = out["鑾高"]
    assert b["right"] == "蠻高" and b["confidence"] == "high" and b["reason"] == "e"
    assert b["ts"] == [20.0, 30.0, 40.0]  # every occurrence in the windows that proposed it


def test_merge_conflict_tie_goes_to_confidence():
    segs = [{"text": "輪交保費", "t": 5.0}, {"text": "輪交", "t": 9.0}]
    per = [[{"wrong": "輪交", "right": "論交", "confidence": "low", "reason": "", "ts": []}],
           [{"wrong": "輪交", "right": "躉繳", "confidence": "medium", "reason": "", "ts": []}]]
    out = merge_window_proposals(per, [(0, 1), (1, 2)], segs, [])
    assert [(p["wrong"], p["right"]) for p in out] == [("輪交", "躉繳")]


# ---------------------------------------------------------------- reprocess: one call per window
@pytest.fixture
def app_client(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, claude_cmd=FAKE)
    return app, TestClient(app)


def correction_calls(log: Path) -> list[str]:
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        if '"wrong"' in c["argv"][c["argv"].index("--json-schema") + 1]:
            out.append(c["stdin"])
    return out


CORR = [{"wrong": "寶兒大", "right": "保額大", "confidence": "high", "reason": "保單講保額", "ts": [60]},
        {"wrong": "園大", "right": "元大", "confidence": "medium", "reason": "券商", "ts": [500]}]


def test_reprocess_runs_correction_per_window(app_client, monkeypatch, tmp_path):
    app, c = app_client
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(CORR, ensure_ascii=False))
    segs = long_segs()
    app.state.store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": segs,
                          "summary": None, "mode": "meeting"})
    assert c.post("/api/meetings/m1/ai", json={"action": "reprocess"}).status_code == 200
    handle_ai_job(app.state.store, app.state.jobs.claim(["ai"], "nas-ai"), FAKE, app.state.jobs)
    n = len(correction_windows(transcript_lines(segs)))
    calls = correction_calls(log)
    assert n >= 3 and len(calls) == n
    assert all("【逐字稿】" in s and len(s.split("【逐字稿】")[1]) <= 3600 for s in calls)
    m = app.state.store.get("m1")
    corr = {x["wrong"]: x for x in m["corrections"]["items"]}
    assert corr["寶兒大"]["state"] == "applied" and corr["園大"]["state"] == "pending"
    assert corr["園大"]["ts"] == [500.0]  # recomputed (only window(s) that contain it)


def test_correction_prompt_asks_for_exhaustive_scan():
    from server import prompts as P
    assert "每一個聽錯的詞都要列出" in P.CORRECTION and "不要只挑重要的" in P.CORRECTION
    assert "一律不可為 high" in P.CORRECTION and "最高只能 medium" in P.CORRECTION


# ---------------------------------------------------------------- adjudicate re-ask
def test_adjudicate_reasks_unanswered_flags_once():
    m = {"transcript": [{"speaker": "A", "text": "園大開戶", "t": 0.0}], "summary": None}
    f1 = review_item("term", "transcript", None, "園大", "可能聽錯", suggestion="元大", t=0.0)
    f2 = review_item("term", "transcript", None, "開戶", "可能聽錯", suggestion="開護", t=0.0)
    m["review"] = {"items": [f1, f2]}
    sent = []

    def call(system, user, schema):
        sent.append(user)
        if len(sent) == 1:
            return {"items": [{"span": "園大", "action": "replace", "suggestion": "元大", "reason": "r",
                               "confidence": "medium", "itemIds": [], "fromFlags": [f1["id"]]}]}
        return {"items": [{"span": "開戶", "action": "keep", "suggestion": "", "reason": "r",
                           "confidence": "high", "itemIds": [], "fromFlags": [f2["id"]]}]}
    out = adjudicate_cards(Ctx(m, call, lambda *a, **k: None), m, 150_000)
    assert len(sent) == 2 and f"[F:{f2['id']}]" in sent[1] and f"[F:{f1['id']}]" not in sent[1]
    assert [x["span"] for x in out] == ["園大", "開戶"]
    # everything answered: no second call
    sent.clear()
    out = adjudicate_cards(Ctx(m, call, lambda *a, **k: None), m, 150_000, {f1["id"]}, entries=False)
    assert len(sent) == 1 and f"[F:{f2['id']}]" not in sent[0]


# ---------------------------------------------------------------- deep_correct
def deep_meeting():
    segs = long_segs()
    summary = {"actionItems": [], "mainPoints": [{"id": "aaaaaaaa", "text": "保單寶兒大",
                                                   "src": [{"t": 60.0, "quote": "這張保單寶兒大"}]}], "qa": []}
    kept = {"id": "keptcard", "kind": "word", "origin": "ai", "span": "第1段", "action": "replace",
            "suggestion": "第一段", "state": "confirmed", "resolution": "keep", "targets": [],
            "fromFlags": [], "sources": ["ai"], "reason": "", "confidence": "medium"}
    open_card = {"id": "opencard", "kind": "word", "origin": "ai", "span": "第2段", "action": "replace",
                 "suggestion": "第二段", "state": "pending", "targets": [], "fromFlags": [], "sources": ["ai"],
                 "reason": "舊的", "confidence": "medium"}
    return {"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": segs, "mode": "meeting",
            "summary": summary, "notes": None,
            "corrections": {"items": [{"wrong": "第7段", "right": "第七段", "confidence": "medium", "count": 1,
                                       "ts": [140.0], "state": "rejected"}], "at": 1},
            "review": {"items": [kept, open_card], "at": 1}}


DEEP = CORR + [{"wrong": "第7段", "right": "第七段", "confidence": "medium", "reason": "rejected before", "ts": []},
               {"wrong": "第1段", "right": "第①段", "confidence": "medium", "reason": "kept before", "ts": []}]
ADJ = [{"span": "園大", "action": "replace", "suggestion": "元大", "reason": "[00:08:20]「園大開戶」",
        "confidence": "medium", "itemIds": [], "fromFlags": ["*term:園大"]}]


def test_deep_correct_adds_rows_keeps_summary_and_handled_states(app_client, monkeypatch, tmp_path):
    app, c = app_client
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(DEEP, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_ADJUDICATE", json.dumps(ADJ, ensure_ascii=False))
    app.state.store.save(deep_meeting())
    r = c.post("/api/meetings/m1/ai", json={"action": "deep_correct"})
    assert r.status_code == 200
    jid = r.json()["jobId"]
    m = app.state.store.get("m1")
    assert m["status"] == "summarizing" and m["aiJobId"] == jid and m["progress"]["action"] == "deep_correct"
    assert app.state.jobs.get(jid)["payload"] == {"action": "deep_correct"}
    assert c.post("/api/meetings/m1/ai", json={"action": "deep_correct"}).status_code == 409  # one at a time
    assert c.post("/api/meetings/m1/ai", json={"action": "reprocess"}).status_code == 409
    msgs = []
    real = app.state.jobs.progress
    monkeypatch.setattr(app.state.jobs, "progress",
                        lambda j, st, pct, msg="": (msgs.append((st, msg)), real(j, st, pct, msg))[1])
    handle_ai_job(app.state.store, app.state.jobs.claim(["ai"], "nas-ai"), FAKE, app.state.jobs)
    assert app.state.jobs.get(jid)["state"] == "done"
    m = app.state.store.get("m1")
    assert m["status"] == "done" and not m.get("error")
    n = len(correction_windows(transcript_lines(m["transcript"])))
    assert ("correction", f"加強校對中（第 1/{n} 段）") in msgs and ("correction", f"加強校對中（第 {n}/{n} 段）") in msgs
    # no summary / notes / mode calls
    schemas = [json.loads(line)["argv"] for line in log.read_text(encoding="utf-8").splitlines()]
    kinds = {("wrong" if '"wrong"' in a[a.index("--json-schema") + 1] else
              "span" if '"span"' in a[a.index("--json-schema") + 1] else
              "decision" if '"decision"' in a[a.index("--json-schema") + 1] else "other") for a in schemas}
    assert kinds == {"wrong", "span", "decision"}  # correction windows, AI filter, adjudicate
    # safe high one auto-applied to the transcript AND the entry quoting it (no new quote flag)
    assert not any("寶兒大" in s["text"] for s in m["transcript"])
    assert m["summary"]["mainPoints"][0]["text"] == "保單保額大"
    assert m["summary"]["mainPoints"][0]["src"][0]["quote"] == "這張保單保額大"
    assert not [x for x in m["review"]["items"] if x.get("kind") == "quote" and x.get("state") == "pending"]
    assert m["transcriptRaw"][3]["text"].startswith("這張保單寶兒大")
    # medium one -> new pending correction -> term flag merged into a new card (a new checklist row)
    corr = {(x["wrong"], x["right"]): x for x in m["corrections"]["items"]}
    assert corr[("園大", "元大")]["state"] == "pending"
    rows = W.review_rows(m["review"]["items"])
    assert {(x["from"], x["to"]) for x in rows} == {("園大", "元大"), ("第2段", "第二段")}  # old open row kept
    # handled: the rejected correction and the kept span are not resurrected
    assert corr[("第7段", "第七段")]["state"] == "rejected" and ("第1段", "第①段") not in corr
    kept = next(x for x in m["review"]["items"] if x["id"] == "keptcard")
    assert kept["state"] == "confirmed" and kept["resolution"] == "keep"


def test_deep_correct_aborts_when_transcript_changed(app_client, monkeypatch):
    app, c = app_client
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(CORR, ensure_ascii=False))
    app.state.store.save(deep_meeting())
    jid = c.post("/api/meetings/m1/ai", json={"action": "deep_correct"}).json()["jobId"]
    real = ai._deep_correct

    def edit_then(ctx, payload):
        apply = real(ctx, payload)
        app.state.store.update("m1", lambda m: m["transcript"][0].update(text="手動改過"))
        return apply
    monkeypatch.setitem(ai.HANDLERS, "deep_correct", edit_then)
    handle_ai_job(app.state.store, app.state.jobs.claim(["ai"], "nas-ai"), FAKE, app.state.jobs)
    j = app.state.jobs.get(jid)
    assert j["state"] == "failed" and "加強校對" in j["error"]
    m = app.state.store.get("m1")
    assert m["transcript"][0]["text"] == "手動改過" and any("寶兒大" in s["text"] for s in m["transcript"])
    assert m["status"] == "done" and m["summary"]["mainPoints"][0]["text"] == "保單寶兒大"


def test_deep_correct_409_while_transcribing_and_400_without_transcript(app_client):
    app, c = app_client
    m = deep_meeting()
    m["status"] = "transcribing"
    app.state.store.save(m)
    assert c.post("/api/meetings/m1/ai", json={"action": "deep_correct"}).status_code == 409
    m.update(status="done", transcript=[])
    app.state.store.save(m)
    assert c.post("/api/meetings/m1/ai", json={"action": "deep_correct"}).status_code == 400


def test_uncertain_keep_on_term_flag_becomes_low_row():
    from server.ai import uncertain_keeps_to_rows
    f = review_item("term", "transcript", None, "園大", "可能聽錯", suggestion="元大", t=0.0)
    m = {"review": {"items": [f]}}
    raw = [{"span": "各位園大的", "action": "keep", "suggestion": "", "reason": "請查證主辦單位", "confidence": "low",
            "itemIds": [], "fromFlags": [f["id"], "other"]},
           {"span": "X大", "action": "keep", "suggestion": "", "reason": "沒錯", "confidence": "high",
            "itemIds": [], "fromFlags": [f["id"]]}]
    out = uncertain_keeps_to_rows(raw, m, {f["id"]})
    assert out[0] == {"span": "園大", "action": "replace", "suggestion": "元大", "confidence": "low",
                      "reason": "AI 沒把握，請確認：請查證主辦單位", "itemIds": [], "fromFlags": [f["id"]]}
    assert out[1]["action"] == "keep" and out[1]["fromFlags"] == ["other"]
    assert out[2] == raw[1]  # a sure keep stays a keep


def test_deep_correct_reasks_term_flags_an_ai_keep_hid(app_client, monkeypatch):
    app, c = app_client
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(CORR, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_ADJUDICATE", json.dumps(
        [{"span": "園大開戶", "action": "keep", "suggestion": "", "reason": "請查證", "confidence": "low",
          "itemIds": [], "fromFlags": ["*term:園大"]}], ensure_ascii=False))
    m = deep_meeting()
    m["corrections"]["items"].append({"wrong": "園大", "right": "元大", "confidence": "high", "count": 1,
                                      "ts": [500.0], "state": "pending"})
    flag = review_item("term", "transcript", None, "園大", "可能聽錯", suggestion="元大", t=500.0)
    flag.update(id=hash8("term|transcript||園大|元大"), state="merged", mergedInto="aikeep01")
    aikeep = {"id": "aikeep01", "kind": "word", "origin": "ai", "span": "我們在園大", "action": "keep",
              "suggestion": "", "state": "confirmed", "resolution": "keep", "resolvedBy": "ai", "targets": [],
              "fromFlags": [flag["id"]], "sources": ["term"], "reason": "請查證", "confidence": "low"}
    m["review"]["items"] += [flag, aikeep]
    app.state.store.save(m)
    c.post("/api/meetings/m1/ai", json={"action": "deep_correct"})
    handle_ai_job(app.state.store, app.state.jobs.claim(["ai"], "nas-ai"), FAKE, app.state.jobs)
    m = app.state.store.get("m1")
    rows = {(x["from"], x["to"]): x for x in W.review_rows(m["review"]["items"])}
    assert rows[("園大", "元大")]["confidence"] == "low"
    f = next(x for x in m["review"]["items"] if x["id"] == flag["id"])
    assert f["state"] == "merged" and f["mergedInto"] == rows[("園大", "元大")]["cards"][0]
