"""Public-figure names (user decision 2026-10-07): the AI may use general knowledge to suggest the spelling of
a misheard well-known public figure's name, but such a correction is ALWAYS a pending checklist row for the
human — never auto-applied, never auto-confirmed, never in 全部照建議, never rejected by the AI filter for lack
of transcript corroboration. Ordinary names keep the strict rule. recheck_names revives the public-figure
corrections an earlier filter rejected."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import prompts as P
from server import review as R
from server import wordreview as W
from server.ai import _filtered, handle_ai_job, public_verdict
from server.app import create_app

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
REASON = "公眾人物，依常識應為「張虔生」（日月光董事長）；請確認"


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


def segs():
    texts = ["日月光投控老闆張前生當作是外國人", "我的客戶王小明來找我", "這個蠻高的", "沒有錯字"]
    return [{"speaker": "說話者 1", "text": t, "t": float(i * 30)} for i, t in enumerate(texts)]


PUBLIC = {"wrong": "張前生", "right": "張虔生", "confidence": "high", "reason": "日月光董事長", "ts": [0],
          "publicFigure": True, "identity": "日月光董事長"}
PRIVATE = {"wrong": "王小明", "right": "王曉明", "confidence": "medium", "reason": "人名", "ts": [30]}


# ---------------------------------------------------------------- prompts
def test_prompts_carry_the_rule_and_schema_fields():
    for prompt in (P.CORRECTION, P.VERIFY_CANDIDATES, P.ADJUDICATE):
        assert "知名公眾人物" in prompt and "張前生" in prompt and "曹興誠" in prompt and "劉世芳" in prompt
        assert "余宛如" in prompt and "曾繁城" in prompt and "陳致中" in prompt
    for schema in (P.CORRECTION_SCHEMA, P.VERIFY_CANDIDATES_SCHEMA, P.ADJUDICATE_SCHEMA):
        props = schema["properties"]["items"]["items"]["properties"]
        assert props["publicFigure"] == {"type": "boolean"} and props["identity"] == {"type": "string"}
    assert P.public_reason("張虔生", "日月光董事長") == REASON


# ---------------------------------------------------------------- never auto-applied
def test_merge_corrections_never_applies_a_public_figure_row():
    tx, corr = R.merge_corrections(None, [PUBLIC], segs())
    c = corr["items"][0]
    assert tx == segs()  # transcript untouched although the model said high and auto_apply_ok holds
    assert c["state"] == "pending" and c["confidence"] == "medium" and c["publicFigure"] is True
    assert c["identity"] == "日月光董事長" and c["reason"] == REASON
    # the same pair without the flag would have been applied: the guard is what keeps it pending
    _tx, corr2 = R.merge_corrections(None, [{k: v for k, v in PUBLIC.items() if k not in ("publicFigure", "identity")}],
                                     segs())
    assert corr2["items"][0]["state"] == "applied"


def test_public_card_never_auto_confirmed_nor_accept_high():
    m = {"transcript": segs(), "review": {"items": []}}
    card = W.make_card(m, "張前生", "replace", "張虔生", "x", "high", [], ["term"])
    card["publicFigure"] = True
    assert not W.accept_high_eligible(card)
    keep = W.make_card(m, "張前生", "keep", "", "x", "high", [], ["ai"])
    keep["publicFigure"] = True
    out = W.finalize_review(m, [keep])
    assert out[0]["state"] == "pending" and "resolvedBy" not in out[0]


def test_adjudicate_keep_on_public_term_flag_becomes_pending_public_row():
    m = {"transcript": segs(), "corrections": {"items": [
        {"wrong": "張前生", "right": "張虔生", "confidence": "medium", "count": 1, "ts": [0.0], "state": "pending",
         "publicFigure": True, "identity": "日月光董事長"}]}}
    m["review"] = R.verify_review(m, [])
    flag = next(r for r in m["review"]["items"] if r.get("kind") == "term")
    assert flag["publicFigure"] is True and "公眾人物（日月光董事長）" in flag["detail"]
    W.apply_ai_cards(m, [{"span": "張前生", "action": "keep", "suggestion": "", "reason": "逐字稿只有張前生，真名請查證",
                          "confidence": "high", "itemIds": [], "fromFlags": [flag["id"]]}])
    rows = W.review_rows(m["review"]["items"])
    assert [(x["from"], x["to"], x["confidence"]) for x in rows] == [("張前生", "張虔生", "medium")]
    card = next(r for r in m["review"]["items"] if W.is_card(r))
    assert card["publicFigure"] is True and card["reason"] == REASON and card["state"] == "pending"
    assert not W.accept_high_eligible(card)
    # an adjudicator that says high on a public replace card is capped at medium
    m2 = {"transcript": segs(), "review": {"items": []}}
    W.apply_ai_cards(m2, [{"span": "張前生", "action": "replace", "suggestion": "張虔生", "reason": "r",
                           "confidence": "high", "itemIds": [], "fromFlags": [], "publicFigure": True,
                           "identity": "日月光董事長"}])
    c2 = next(r for r in m2["review"]["items"] if W.is_card(r))
    assert c2["confidence"] == "medium" and c2["reason"] == REASON


# ---------------------------------------------------------------- AI filter
def test_filter_keeps_public_rows_unless_ai_says_not_that_person():
    assert public_verdict("reject", True, None) == ("unsure", True)           # no corroboration: kept
    assert public_verdict("reject", True, (None, "")) == ("unsure", True)
    assert public_verdict("reject", True, (False, "")) == ("reject", False)  # context: not that person
    assert public_verdict("reject", False, (True, "x")) == ("reject", False)  # ordinary rejected stays so
    assert public_verdict("confirm", False, (True, "x")) == ("confirm", True)  # AI recognised a public figure
    pub = {**PUBLIC, "confidence": "medium"}
    kept, rej, unsure = _filtered([pub, PRIVATE], {("張前生", "張虔生"): ("reject", "正確寫法未見於逐字稿"),
                                                   ("王小明", "王曉明"): ("reject", "一般人名")})
    assert [p["wrong"] for p in kept] == ["張前生"] and kept[0]["publicFigure"] is True
    assert [p["wrong"] for p in rej] == ["王小明"] and unsure == {("張前生", "張虔生")}
    kept, rej, _u = _filtered([pub], {("張前生", "張虔生"): ("reject", "是講者的客戶")},
                              {("張前生", "張虔生"): (False, "")})
    assert not kept and rej[0]["wrong"] == "張前生"


def test_reprocess_public_row_pending_private_strict_accept_high_skips(app_client, monkeypatch, tmp_path):
    app, c = app_client
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps([PUBLIC, PRIVATE], ensure_ascii=False))
    # the filter says reject to both "for lack of corroboration" (publicFigure not said)
    monkeypatch.setenv("FAKE_CLAUDE_VERIFY", json.dumps({"張前生": "reject", "王小明": "reject"}, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_ADJUDICATE", json.dumps(
        [{"span": "張前生", "action": "keep", "suggestion": "", "reason": "逐字稿只有張前生", "confidence": "high",
          "itemIds": [], "fromFlags": ["*term:張前生"]}], ensure_ascii=False))
    app.state.store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": segs(),
                          "summary": None, "mode": "meeting"})
    assert c.post("/api/meetings/m1/ai", json={"action": "reprocess"}).status_code == 200
    run_job(app)
    m = app.state.store.get("m1")
    assert m["transcript"][0]["text"] == segs()[0]["text"]  # never auto-applied
    corr = {x["wrong"]: x for x in m["corrections"]["items"]}
    assert corr["張前生"]["state"] == "pending" and corr["張前生"]["publicFigure"] is True
    assert corr["張前生"]["confidence"] in ("medium", "low")
    assert corr["王小明"]["state"] == "rejected" and corr["王小明"]["resolvedBy"] == "ai-filter"
    assert "張前生" in calls_of(log, "decision")[0]  # high public proposals do go through the filter
    rows = {(x["from"], x["to"]): x for x in W.review_rows(m["review"]["items"])}
    assert set(rows) == {("張前生", "張虔生")}
    card = next(r for r in m["review"]["items"] if W.is_card(r) and r["span"] == "張前生")
    assert card["publicFigure"] is True and card["reason"] == REASON and card["state"] == "pending"
    r = c.post("/api/meetings/m1/review/accept-high")
    assert r.status_code == 200
    m = app.state.store.get("m1")
    card = next(x for x in m["review"]["items"] if W.is_card(x) and x["span"] == "張前生")
    assert card["state"] == "pending" and m["transcript"][0]["text"] == segs()[0]["text"]


# ---------------------------------------------------------------- recheck_names (retroactive)
def rejected_meeting():
    m = {"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": segs(), "summary": None,
         "mode": "meeting", "corrections": {"items": [
             {"wrong": w, "right": r, "confidence": "medium", "count": 1, "ts": [t], "state": "rejected",
              "resolvedBy": "ai-filter", "filterReason": "人名，正確寫法未見於逐字稿或詞庫", "reason": "?"}
             for w, r, t in (("張前生", "張虔生", 0.0), ("王小明", "王曉明", 30.0), ("蠻高", "滿高", 60.0))]
             + [{"wrong": "沒有錯字", "right": "沒有錯誤", "confidence": "medium", "count": 1, "ts": [90.0],
                 "state": "rejected"}], "at": 1}}
    cards = [W.make_card(m, w, "replace", r, "AI 沒把握", "low", [], ["term"])
             for w, r in (("張前生", "張虔生"), ("王小明", "王曉明"), ("蠻高", "滿高"))]
    for x in cards:
        x.update(state="rejected", resolvedBy="ai-filter", resolution="keep", filterReason="人名", decidedAt=1)
    m["review"] = {"items": cards, "at": 1}
    return m


def test_recheck_names_revives_only_public_figure_names(app_client, monkeypatch, tmp_path):
    app, c = app_client
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_VERIFY", json.dumps({
        "張前生": {"decision": "confirm", "publicFigure": True, "identity": "日月光董事長"},
        "王小明": {"decision": "reject", "publicFigure": False, "identity": ""}}, ensure_ascii=False))
    app.state.store.save(rejected_meeting())
    r = c.post("/api/meetings/m1/ai", json={"action": "recheck_names"})
    assert r.status_code == 200
    m = app.state.store.get("m1")
    assert m["status"] == "summarizing" and m["progress"]["action"] == "recheck_names"
    assert c.post("/api/meetings/m1/ai", json={"action": "recheck_names"}).status_code == 409
    run_job(app)
    m = app.state.store.get("m1")
    assert m["status"] == "done"
    sent = calls_of(log, "decision")
    assert len(sent) == 1 and "張前生" in sent[0] and "王小明" in sent[0]
    assert "蠻高" not in sent[0] and "沒有錯字" not in sent[0]  # not name-like / not filter-rejected
    corr = {x["wrong"]: x for x in m["corrections"]["items"]}
    z = corr["張前生"]
    assert z["state"] == "pending" and z["publicFigure"] is True and z["confidence"] == "medium"
    assert z["reason"] == REASON and "resolvedBy" not in z and "filterReason" not in z
    assert corr["王小明"]["state"] == "rejected" and corr["蠻高"]["state"] == "rejected"
    assert corr["沒有錯字"]["state"] == "rejected"
    assert m["transcript"][0]["text"] == segs()[0]["text"]  # nothing applied
    rows = W.review_rows(m["review"]["items"])
    assert [(x["from"], x["to"], x["confidence"]) for x in rows] == [("張前生", "張虔生", "medium")]
    card = next(x for x in m["review"]["items"] if W.is_card(x) and x["span"] == "張前生")
    assert card["publicFigure"] is True and card["identity"] == "日月光董事長" and card["reason"] == REASON
    assert not W.accept_high_eligible(card)
    pend_flags = [x for x in m["review"]["items"] if not W.is_card(x) and x.get("state") == "pending"]
    assert not pend_flags  # the term flag is merged into the revived card (one row, not two)
    assert m["nameRecheck"]["revived"] == 1 and m["nameRecheck"]["candidates"] == 2
    assert m["nameRecheck"]["items"] == [{"wrong": "張前生", "right": "張虔生", "identity": "日月光董事長"}]
    # accepting the row is the human's call and works like any card
    r = c.post(f"/api/meetings/m1/review/{card['id']}", json={"action": "accept"})
    assert r.status_code == 200
    assert app.state.store.get("m1")["transcript"][0]["text"].startswith("日月光投控老闆張虔生")


def test_recheck_names_unsure_without_public_flag_stays_rejected(app_client, monkeypatch):
    app, c = app_client
    monkeypatch.setenv("FAKE_CLAUDE_VERIFY", json.dumps({"張前生": "unsure", "王小明": "confirm"}, ensure_ascii=False))
    app.state.store.save(rejected_meeting())
    c.post("/api/meetings/m1/ai", json={"action": "recheck_names"})
    run_job(app)
    m = app.state.store.get("m1")
    assert all(x["state"] == "rejected" for x in m["corrections"]["items"])  # no publicFigure: strict rule
    assert not W.review_rows(m["review"]["items"]) and m["nameRecheck"]["revived"] == 0


def test_recheck_names_card_only_and_name_like():
    assert W.name_like_pair("餘萬如委員", "余宛如委員") and W.name_like_pair("劉思芳委員", "劉世芳委員")
    assert not W.name_like_pair("蠻高", "滿高") and not W.name_like_pair("國名", "國民")
