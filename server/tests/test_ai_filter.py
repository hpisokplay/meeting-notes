"""AI second opinion (VERIFY_CANDIDATES): medium / low correction candidates are re-judged by the AI;
rejected ones never become checklist rows (state "rejected", resolvedBy "ai-filter") and never come back."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import ai
from server import prompts as P
from server import wordreview as W
from server.ai import Ctx, _filtered, filter_proposals, handle_ai_job, verify_candidates
from server.app import create_app

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


@pytest.fixture
def app_client(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, claude_cmd=FAKE)
    return app, TestClient(app)


def run_job(app):
    handle_ai_job(app.state.store, app.state.jobs.claim(["ai"], "nas-ai"), FAKE, app.state.jobs)


def calls_of(log: Path, key: str) -> list[str]:
    out = []
    if not log.exists():
        return out
    for line in log.read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        if f'"{key}"' in c["argv"][c["argv"].index("--json-schema") + 1]:
            out.append(c["stdin"])
    return out


# ---------------------------------------------------------------- unit: verdict mapping, batching
def stub_ctx(answer, reports=None):
    seen = []

    def call(system, user, schema):
        assert system == P.VERIFY_CANDIDATES and schema == P.VERIFY_CANDIDATES_SCHEMA
        seen.append(user)
        return answer(user)
    ctx = Ctx({"transcript": []}, call, lambda pct, msg, stage=None: (reports if reports is not None else []).append((pct, msg, stage)),
              glossary=["元大"])
    return ctx, seen


def test_verify_candidates_maps_decisions_and_missing_answers_to_unsure():
    segs = [{"text": "我們在園大開戶，今天", "t": 500.0}, {"text": "保單輪交保費", "t": 61.0}]
    want = {"c1": "confirm", "c2": "reject", "c3": "maybe?"}  # c4 unanswered

    def answer(user):
        return {"items": [{"id": k, "decision": v, "reason": f"理由{k}"} for k, v in want.items()]}
    ctx, seen = stub_ctx(answer)
    cands = [{"wrong": "園大", "right": "元大", "confidence": "medium", "reason": "券商"},
             {"wrong": "輪交", "right": "躉繳", "confidence": "low", "reason": ""},
             {"wrong": "蠻高", "right": "滿高", "confidence": "low", "reason": ""},
             {"wrong": "齁", "right": "喔", "confidence": "low", "reason": ""}]
    v = verify_candidates(ctx, cands, segs)
    assert v[("園大", "元大")] == ("confirm", "理由c1") and v[("輪交", "躉繳")] == ("reject", "理由c2")
    assert v[("蠻高", "滿高")][0] == "unsure" and v[("齁", "喔")] == ("unsure", "")
    msg = seen[0]
    assert "【詞庫】\n元大" in msg and "[C:c1] 「園大」→「元大」（初判信心：medium；初判理由：券商）" in msg
    assert "[00:08:20] 我們在【園大】開戶，今天" in msg and "（逐字稿找不到出現位置）" in msg


def test_filtered_confirm_keeps_unsure_lowers_reject_drops():
    props = [{"wrong": "園大", "right": "元大", "confidence": "medium", "ts": [1]},
             {"wrong": "輪交", "right": "躉繳", "confidence": "medium", "ts": [2]},
             {"wrong": "蠻高", "right": "滿高", "confidence": "low", "ts": [3]},
             {"wrong": "寶兒大", "right": "保額大", "confidence": "high", "ts": [4]}]
    kept, rej, unsure = _filtered(props, {("園大", "元大"): ("confirm", ""), ("輪交", "躉繳"): ("unsure", "?"),
                                          ("蠻高", "滿高"): ("reject", "用字風格")})
    assert [(p["wrong"], p["confidence"]) for p in kept] == [("園大", "medium"), ("輪交", "low"), ("寶兒大", "high")]
    assert rej == [{**props[2], "filterReason": "用字風格"}] and unsure == {("輪交", "躉繳")}


def test_filter_skips_high_and_settled_and_batches_by_30_with_progress():
    reports = []
    ctx, seen = stub_ctx(lambda user: {"items": []}, reports)
    props = [{"wrong": f"錯{i}", "right": f"對{i}", "confidence": "medium" if i % 2 else "low", "ts": []}
             for i in range(65)]
    props += [{"wrong": "高的", "right": "高對", "confidence": "high", "ts": []},
              {"wrong": "舊的", "right": "舊對", "confidence": "medium", "ts": []}]
    old = {"items": [{"wrong": "舊的", "right": "舊對", "state": "rejected"}]}
    kept, rej, unsure = filter_proposals(ctx, props, [], old)
    assert len(seen) == 3 and [s.count("[C:") for s in seen] == [30, 30, 5]
    assert not any("高的" in s or "舊的" in s for s in seen)
    assert len(unsure) == 65 and not rej  # nothing answered -> unsure (kept, confidence low)
    assert {p["wrong"]: p["confidence"] for p in kept}["高的"] == "high"
    msgs = [m for _p, m, st in reports]
    assert {"AI 複核可能錯字中（1/3 批）", "AI 複核可能錯字中（3/3 批）"} <= set(msgs)
    assert {st for *_x, st in reports} == {"correction"}
    # nothing medium / low: no call at all
    ctx2, seen2 = stub_ctx(lambda user: {"items": []})
    assert filter_proposals(ctx2, props[65:66], [], None) == (props[65:66], [], set()) and not seen2


# ---------------------------------------------------------------- reprocess / deep_correct
def segs():
    texts = ["我們在園大開戶", "保單輪交保費", "這個蠻高的", "保費小寶兒大的保單", "要科醫產稅", "沒有錯字"]
    return [{"speaker": "說話者 1", "text": t, "t": float(i * 30)} for i, t in enumerate(texts)]


PROPS = [{"wrong": "寶兒大", "right": "保額大", "confidence": "high", "reason": "保單", "ts": [90]},
         {"wrong": "園大", "right": "元大", "confidence": "medium", "reason": "券商", "ts": [0]},
         {"wrong": "輪交", "right": "躉繳", "confidence": "medium", "reason": "保費", "ts": [30]},
         {"wrong": "蠻高", "right": "滿高", "confidence": "low", "reason": "風格", "ts": [60]}]
VERDICT = {"蠻高": "reject", "輪交": "unsure"}


def test_reprocess_filters_medium_low_before_merge(app_client, monkeypatch, tmp_path):
    app, c = app_client
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(PROPS, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_VERIFY", json.dumps(VERDICT, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_ADJUDICATE", json.dumps(
        [{"span": "輪交", "action": "replace", "suggestion": "躉繳", "reason": "[00:00:30]「輪交保費」",
          "confidence": "high", "itemIds": [], "fromFlags": ["*term:輪交"]}], ensure_ascii=False))
    app.state.store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": segs(),
                          "summary": None, "mode": "meeting"})
    assert c.post("/api/meetings/m1/ai", json={"action": "reprocess"}).status_code == 200
    run_job(app)
    m = app.state.store.get("m1")
    corr = {x["wrong"]: x for x in m["corrections"]["items"]}
    assert corr["寶兒大"]["state"] == "applied"  # high: skipped the filter, auto-applied as before
    assert corr["園大"]["state"] == "pending" and corr["園大"]["confidence"] == "medium"  # confirmed
    assert corr["輪交"]["state"] == "pending" and corr["輪交"]["confidence"] == "low"  # unsure
    r = corr["蠻高"]
    assert r["state"] == "rejected" and r["resolvedBy"] == "ai-filter" and "reject" in r["filterReason"]
    verify = calls_of(log, "decision")
    assert len(verify) == 1 and "園大" in verify[0] and "蠻高" in verify[0] and "寶兒大" not in verify[0]
    # unsure stays a row, capped at low even though the adjudicator said high; rejected: no row, no flag
    rows = {(x["from"], x["to"]): x for x in W.review_rows(m["review"]["items"])}
    assert rows[("輪交", "躉繳")]["confidence"] == "low"
    assert not any(x.get("text") == "蠻高" and x.get("state") == "pending" for x in m["review"]["items"])


def test_deep_correct_rejected_never_resurrect(app_client, monkeypatch, tmp_path):
    app, c = app_client
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(PROPS, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_VERIFY", json.dumps(VERDICT, ensure_ascii=False))
    app.state.store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": segs(),
                          "summary": None, "mode": "meeting"})
    assert c.post("/api/meetings/m1/ai", json={"action": "deep_correct"}).status_code == 200
    run_job(app)
    m = app.state.store.get("m1")
    corr = {x["wrong"]: x for x in m["corrections"]["items"]}
    assert corr["蠻高"]["state"] == "rejected" and corr["蠻高"]["resolvedBy"] == "ai-filter"
    assert corr["園大"]["state"] == "pending" and corr["輪交"]["confidence"] == "low"
    assert corr["寶兒大"]["state"] == "applied"
    assert not any(x.get("kind") == "term" and x.get("text") == "蠻高" for x in m["review"]["items"])
    # second 加強校對: the AI would now confirm everything, but the rejected pair is settled
    monkeypatch.setenv("FAKE_CLAUDE_VERIFY", "{}")
    log.unlink()
    assert c.post("/api/meetings/m1/ai", json={"action": "deep_correct"}).status_code == 200
    run_job(app)
    m = app.state.store.get("m1")
    corr = {x["wrong"]: x for x in m["corrections"]["items"]}
    assert corr["蠻高"]["state"] == "rejected"
    assert not any("蠻高" in s for s in calls_of(log, "decision"))  # not even re-judged
    assert not any(x.get("state") == "pending" and "蠻高" in str(x.get("text") or x.get("span") or "")
                   for x in m["review"]["items"])


def test_filter_failure_is_optional(app_client, monkeypatch):
    app, c = app_client
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(PROPS, ensure_ascii=False))
    real = ai.verify_candidates

    def boom(*a, **k):
        raise ai.ClaudeError("壞了")
    monkeypatch.setattr(ai, "verify_candidates", boom)
    app.state.store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": segs(),
                          "summary": None, "mode": "meeting"})
    c.post("/api/meetings/m1/ai", json={"action": "deep_correct"})
    run_job(app)
    m = app.state.store.get("m1")
    assert {x["wrong"]: x["state"] for x in m["corrections"]["items"]}["蠻高"] == "pending"  # unfiltered
    assert any(w.startswith("AI 複核失敗") for w in m.get("warnings") or [])
    monkeypatch.setattr(ai, "verify_candidates", real)


# ---------------------------------------------------------------- filter_pending (retroactive cleanup)
def pending_meeting():
    m = {"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": segs(), "summary": None,
         "mode": "meeting",
         "corrections": {"items": [
             {"wrong": "蠻高", "right": "滿高", "confidence": "medium", "count": 1, "ts": [60.0], "state": "pending"},
             {"wrong": "科醫產稅", "right": "課遺產稅", "confidence": "medium", "count": 1, "ts": [120.0],
              "state": "pending"},
             {"wrong": "寶兒大", "right": "保額大", "confidence": "high", "count": 1, "ts": [90.0], "state": "pending"}],
             "at": 1}}
    cards = [W.make_card(m, "園大", "replace", "元大", "券商", "medium", [], ["term"]),
             W.make_card(m, "輪交", "replace", "躉繳", "保費", "low", [], ["term"]),
             W.make_card(m, "蠻高", "replace", "滿高", "風格", "medium", [], ["term"]),
             W.make_card(m, "寶兒大", "replace", "保額大", "保單", "high", [], ["term"]),
             W.make_card(m, "沒有錯字", "replace", "沒有錯誤", "使用者處理過", "medium", [], ["term"])]
    cards[4].update(state="confirmed", resolution="keep")
    m["review"] = {"items": cards, "at": 1}
    return m


def test_filter_pending_only_touches_pending_medium_low(app_client, monkeypatch, tmp_path):
    app, c = app_client
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_VERIFY", json.dumps({"蠻高": "reject", "科醫產稅": "reject", "輪交": "unsure"},
                                                        ensure_ascii=False))
    from server import review as R
    m = pending_meeting()
    m["review"] = R.verify_review(m, [])  # term rows for the pending corrections, as the app has them
    app.state.store.save(m)
    r = c.post("/api/meetings/m1/ai", json={"action": "filter_pending"})
    assert r.status_code == 200
    jid = r.json()["jobId"]
    m = app.state.store.get("m1")
    assert m["status"] == "summarizing" and m["progress"]["action"] == "filter_pending"
    assert c.post("/api/meetings/m1/ai", json={"action": "filter_pending"}).status_code == 409
    assert c.post("/api/meetings/m1/ai", json={"action": "deep_correct"}).status_code == 409
    run_job(app)
    assert app.state.jobs.get(jid)["state"] == "done"
    m = app.state.store.get("m1")
    assert m["status"] == "done"
    sent = calls_of(log, "decision")
    assert len(sent) == 1
    assert all(w in sent[0] for w in ("園大", "輪交", "蠻高", "科醫產稅"))
    assert "寶兒大" not in sent[0] and "沒有錯字" not in sent[0]  # high / handled never asked
    cards = {x["span"]: x for x in m["review"]["items"] if W.is_card(x)}
    assert cards["蠻高"]["state"] == "rejected" and cards["蠻高"]["resolvedBy"] == "ai-filter"
    assert "reject" in cards["蠻高"]["filterReason"]
    assert cards["輪交"]["state"] == "pending" and cards["輪交"]["confidence"] == "low"
    assert cards["園大"]["state"] == "pending" and cards["園大"]["confidence"] == "medium"
    assert cards["寶兒大"]["state"] == "pending" and cards["寶兒大"]["confidence"] == "high"
    assert cards["沒有錯字"]["state"] == "confirmed" and "resolvedBy" not in cards["沒有錯字"]
    corr = {x["wrong"]: x for x in m["corrections"]["items"]}
    assert corr["蠻高"]["state"] == corr["科醫產稅"]["state"] == "rejected"
    assert corr["科醫產稅"]["resolvedBy"] == "ai-filter" and corr["寶兒大"]["state"] == "pending"
    rows = {(x["from"], x["to"]) for x in W.review_rows(m["review"]["items"])}
    assert rows == {("園大", "元大"), ("輪交", "躉繳"), ("寶兒大", "保額大")}
    pend = [x for x in m["review"]["items"] if x.get("state") == "pending" and not W.is_card(x)]
    assert {x["text"] for x in pend} == {"寶兒大"}  # the high term row is untouched; rejected ones are gone
    assert m["aiFilter"]["rejected"] == 2 and m["aiFilter"]["unsure"] == 1 and m["aiFilter"]["confirmed"] == 1
    # a later 加強校對 proposing the rejected pairs again: never resurrected, never re-judged
    monkeypatch.setenv("FAKE_CLAUDE_VERIFY", "{}")
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(
        [{"wrong": "蠻高", "right": "滿高", "confidence": "medium", "reason": "", "ts": [60]},
         {"wrong": "科醫產稅", "right": "課遺產稅", "confidence": "low", "reason": "", "ts": [120]}],
        ensure_ascii=False))
    log.unlink()
    c.post("/api/meetings/m1/ai", json={"action": "deep_correct"})
    run_job(app)
    m = app.state.store.get("m1")
    assert {(x["from"], x["to"]) for x in W.review_rows(m["review"]["items"])} == rows
    assert not calls_of(log, "decision")
