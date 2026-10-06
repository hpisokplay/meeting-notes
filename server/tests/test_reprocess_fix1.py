"""Task C fix round 1: safe auto-correction, restore resets terms, legacy backups, /mode payload,
correction-failure warnings, /review blocked while a job runs, multi-pair term apply."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import ai
from server.ai import ClaudeError, handle_ai_job
from server.app import create_app
from server.render import meeting_to_markdown
from server.review import merge_corrections, replace_term, verify_review
from server.storage import Store
from server.verify import review_item

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def seg(t, text, spk="A"):
    return {"speaker": spk, "text": text, "t": float(t)}


def prop(wrong, right, ts, conf="high"):
    return {"wrong": wrong, "right": right, "confidence": conf, "reason": "r", "ts": ts}


def state_of(corr, wrong):
    return next(c for c in corr["items"] if c["wrong"] == wrong)


# ---------------------------------------------------------------- 1. safe auto-apply
def test_substring_pair_never_auto_applied_and_no_doubling():
    tx = [seg(10, "股利所得要課稅，股利很多")]
    new, corr = merge_corrections(None, [prop("股利", "股利所得", [10])], tx)
    assert new == tx and state_of(corr, "股利")["state"] == "pending"
    assert state_of(corr, "股利")["demoted"] is True
    # even when a human applies it, the existing 「股利所得」 is not doubled
    out, n, _ = replace_term(tx, "股利", "股利所得", [10])
    assert out[0]["text"] == "股利所得要課稅，股利所得很多" and n == 1


def test_short_cjk_pair_demoted_and_windowed_apply_spares_other_text(tmp_path, monkeypatch):
    tx = [seg(10, "證與稅要注意"), seg(100, "保證與否還不確定")]
    new, corr = merge_corrections(None, [prop("證與", "贈與", [10])], tx)
    assert new == tx and state_of(corr, "證與")["state"] == "pending"
    assert state_of(corr, "證與")["ts"] == [10.0]
    # the review detail shows the exact total and every look-alike before the user decides
    det = next(r for r in verify_review({"transcript": tx, "corrections": corr}, [])["items"]
               if r["kind"] == "term")["detail"]
    assert "確認後全文 2 處都會改成「贈與」" in det
    assert "[00:00:10] 【證與】稅要注意" in det and "[00:01:40] 保【證與】否還不確定" in det
    # a USER confirm changes every occurrence (round-2 ruling); the automatic path did not
    store = Store(tmp_path / "d")
    store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": tx,
                "corrections": corr, "review": {"items": [
                    review_item("term", "transcript", None, "證與", "x", suggestion="贈與", t=10.0)]}})
    c = TestClient(create_app(tmp_path / "d", start_threads=False, claude_cmd=FAKE))
    rid = store.get("m1")["review"]["items"][0]["id"]
    out = c.post(f"/api/meetings/m1/review/{rid}", json={"action": "confirm"}).json()
    assert [s["text"] for s in out["transcript"]] == ["贈與稅要注意", "保贈與否還不確定"]


def test_number_changing_pair_demoted():
    tx = [seg(1378, "我繳一億的稅如果對砍只要繳五千塊")]
    new, corr = merge_corrections(None, [prop("繳五千塊", "繳五千萬", [1378])], tx)
    assert new == tx and state_of(corr, "繳五千塊")["state"] == "pending"
    new, corr = merge_corrections(None, [prop("一百八十三天", "一百三十三天", [5])], [seg(5, "住滿一百八十三天")])
    assert state_of(corr, "一百八十三天")["state"] == "pending"


def test_replacement_restricted_to_window():
    tx = [seg(10, "放在維新群島"), seg(20, "維新群島的公司"), seg(100, "維新群島又出現")]
    new, corr = merge_corrections(None, [prop("維新群島", "維京群島", [10])], tx)
    assert [s["text"] for s in new] == ["放在維京群島", "維京群島的公司", "維新群島又出現"]
    c = state_of(corr, "維新群島")
    assert c["state"] == "applied" and c["count"] == 2 and c["ts"] == [10.0, 20.0]


def test_too_many_matches_or_no_ts_demoted():
    tx = [seg(10, "維新群島" * 4)]
    new, corr = merge_corrections(None, [prop("維新群島", "維京群島", [10])], tx)
    assert new == tx and state_of(corr, "維新群島")["state"] == "pending"  # 4 > max(3, 2)
    new, corr = merge_corrections(None, [prop("維新群島", "維京群島", [])], [seg(10, "維新群島")])
    assert state_of(corr, "維新群島")["state"] == "pending"
    # a Latin token with word boundaries is fine
    new, corr = merge_corrections(None, [prop("CIS", "CRS", [10])], [seg(10, "CIS 和 CISCO")])
    assert new[0]["text"] == "CRS 和 CISCO"


# ---------------------------------------------------------------- HTTP fixtures
@pytest.fixture
def app_client(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, claude_cmd=FAKE)
    return app, TestClient(app)


def base(**kw):
    m = {"id": "m1", "title": "t", "createdAt": 1, "status": "done",
         "transcript": [seg(10, "Jefry 會負責"), seg(20, "台積店很大")],
         "summary": {"actionItems": ["舊待辦"], "mainPoints": [{"id": "aaaaaaaa", "text": "舊重點", "src": []}],
                     "qa": []}}
    m.update(kw)
    return m


def run(app):
    handle_ai_job(app.state.store, app.state.jobs.claim(["ai"], "nas-ai"), FAKE, app.state.jobs)


# ---------------------------------------------------------------- 2. restore resets term review items
def test_confirm_then_restore_makes_term_pending_again(app_client):
    app, c = app_client
    store = app.state.store
    corr = {"items": [{"wrong": "Jefry", "right": "Jeffrey", "confidence": "medium", "count": 1,
                       "ts": [10.0], "state": "pending"}], "at": 1}
    term = review_item("term", "transcript", None, "Jefry", "x", suggestion="Jeffrey", t=10.0)
    store.save(base(corrections=corr, review={"items": [term], "at": 1}))
    out = c.post(f"/api/meetings/m1/review/{term['id']}", json={"action": "confirm"}).json()
    assert out["transcript"][0]["text"] == "Jeffrey 會負責"
    assert c.post("/api/meetings/m1/restore-raw").status_code == 200
    assert not [r for r in store.get("m1")["review"]["items"] if r["kind"] == "term"]
    run(app)
    m = store.get("m1")
    terms = [r for r in m["review"]["items"] if r["kind"] == "term"]
    assert [(r["text"], r["state"]) for r in terms] == [("Jefry", "pending")]
    assert m["transcript"][0]["text"] == "Jefry 會負責"


# ---------------------------------------------------------------- 3. legacy backup kept once
def test_legacy_backup_written_once(app_client):
    app, c = app_client
    store = app.state.store
    store.save(base())
    c.post("/api/meetings/m1/ai", json={"action": "reprocess"})
    run(app)
    m = store.get("m1")
    assert m["alt"]["summary:actionItems:legacy"] == ["舊待辦"]
    assert m["alt"]["summary:mainPoints:legacy"] == [{"id": "aaaaaaaa", "text": "舊重點", "src": []}]
    assert m["alt"]["summary:actionItems"] == ["舊待辦"]
    c.post("/api/meetings/m1/ai", json={"action": "reprocess"})
    run(app)
    m = store.get("m1")
    assert m["alt"]["summary:actionItems:legacy"] == ["舊待辦"]  # never overwritten
    assert m["alt"]["summary:actionItems"][0]["src"]  # swap slot = the previous grounded version
    jid = c.post("/api/meetings/m1/ai", json={"action": "enhance", "section": "qa"}).json()["jobId"]
    run(app)
    assert app.state.jobs.get(jid)["state"] == "done"
    assert "summary:qa:legacy" not in store.get("m1")["alt"]  # grounded list: no legacy slot


# ---------------------------------------------------------------- 4. /mode applied only on success
@pytest.mark.parametrize("how", ["fail", "cancel"])
def test_mode_kept_when_job_fails_or_is_cancelled(app_client, monkeypatch, how):
    app, c = app_client
    store, jobs = app.state.store, app.state.jobs
    store.save(base(mode="meeting"))
    jid = c.post("/api/meetings/m1/mode", json={"mode": "lecture"}).json()["jobId"]
    if how == "fail":
        monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
        run(app)
        assert jobs.get(jid)["state"] == "failed"
    else:
        assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200
    m = store.get("m1")
    assert m["mode"] == "meeting" and m["status"] == "done"


# ---------------------------------------------------------------- 5. correction failure warning
def test_correction_failure_recorded_as_warning(app_client, monkeypatch):
    app, c = app_client
    store = app.state.store
    store.save(base())
    real = ai.run_claude

    def flaky(system, user, schema, *a, **k):
        if "wrong" in json.dumps(schema):
            raise ClaudeError("Claude 執行失敗：boom")
        return real(system, user, schema, *a, **k)
    monkeypatch.setattr(ai, "run_claude", flaky)
    c.post("/api/meetings/m1/ai", json={"action": "reprocess"})
    run(app)
    m = store.get("m1")
    assert m["status"] == "done" and m["error"] is None
    assert m["warnings"] == ["名詞校正失敗，已略過：Claude 執行失敗：boom"]
    assert "> ⚠️ 名詞校正失敗，已略過" in meeting_to_markdown(m)
    monkeypatch.setattr(ai, "run_claude", real)
    c.post("/api/meetings/m1/ai", json={"action": "reprocess"})
    run(app)
    assert "warnings" not in store.get("m1")  # a clean run clears it


# ---------------------------------------------------------------- 6. /review blocked while summarizing
def test_review_409_while_job_active(app_client):
    app, c = app_client
    store = app.state.store
    r0 = review_item("name", "summary.mainPoints", "aaaaaaaa", "舊重點", "x")
    store.save(base(review={"items": [r0], "at": 1}))
    jid = c.post("/api/meetings/m1/ai", json={"action": "reprocess"}).json()["jobId"]
    r = c.post(f"/api/meetings/m1/review/{r0['id']}", json={"action": "confirm"})
    assert r.status_code == 409 and r.json()["detail"] == "整理中，請稍後再確認"
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200  # listener puts the meeting back to done
    assert c.post(f"/api/meetings/m1/review/{r0['id']}", json={"action": "confirm"}).status_code == 200


# ---------------------------------------------------------------- 7. all pending pairs with that wrong
def test_term_apply_settles_every_pending_pair(app_client):
    app, c = app_client
    store = app.state.store
    corr = {"items": [
        {"wrong": "台積店", "right": "台積電", "confidence": "medium", "count": 1, "ts": [20.0], "state": "pending"},
        {"wrong": "台積店", "right": "台積殿", "confidence": "low", "count": 1, "ts": [20.0], "state": "pending"}],
        "at": 1}
    term = review_item("term", "transcript", None, "台積店", "x", suggestion="台積電", t=20.0)
    store.save(base(corrections=corr, review={"items": [term], "at": 1}))
    out = c.post(f"/api/meetings/m1/review/{term['id']}", json={"action": "confirm"}).json()
    assert out["transcript"][1]["text"] == "台積電很大"
    assert [(x["right"], x["state"]) for x in out["corrections"]["items"]] == [("台積電", "applied")]


# ---------------------------------------------------------------- fix round 2
def test_user_confirm_replaces_all_occurrences(app_client):
    app, c = app_client
    store = app.state.store
    tx = [seg(10 + 100 * i, f"第{i}段提到維新群島") for i in range(7)]
    corr = {"items": [{"wrong": "維新群島", "right": "維京群島", "confidence": "medium", "count": 7,
                       "ts": [10.0], "state": "pending"}], "at": 1}
    m = base(transcript=tx, corrections=corr)
    m["review"] = verify_review(m, [])
    term = next(r for r in m["review"]["items"] if r["kind"] == "term")
    assert "確認後全文 7 處" in term["detail"] and "只列前 5 處" in term["detail"]  # > 5: contexts listed
    store.save(m)
    out = c.post(f"/api/meetings/m1/review/{term['id']}", json={"action": "confirm"}).json()
    assert not any("維新群島" in s["text"] for s in out["transcript"])
    assert out["corrections"]["items"][0]["count"] == 7


def test_long_rare_term_has_no_context_list():
    tx = [seg(10, "放在維新群島")]
    corr = {"items": [{"wrong": "維新群島", "right": "維京群島", "confidence": "medium", "count": 1,
                       "ts": [10.0], "state": "pending"}]}
    det = next(r for r in verify_review({"transcript": tx, "corrections": corr}, [])["items"]
               if r["kind"] == "term")["detail"]
    assert "確認後全文 1 處" in det and "出現位置" not in det


def test_settle_merges_into_existing_pair(app_client):
    app, c = app_client
    store = app.state.store
    corr = {"items": [
        {"wrong": "台積店", "right": "台積殿", "confidence": "low", "count": 1, "ts": [20.0], "state": "pending"},
        {"wrong": "台積店", "right": "台積電", "confidence": "medium", "count": 1, "ts": [20.0], "state": "rejected"}],
        "at": 1}
    term = review_item("term", "transcript", None, "台積店", "x", suggestion="台積殿", t=20.0)
    store.save(base(corrections=corr, review={"items": [term], "at": 1}))
    out = c.post(f"/api/meetings/m1/review/{term['id']}", json={"action": "edit", "text": "台積電"}).json()
    assert out["transcript"][1]["text"] == "台積電很大"
    assert [(x["wrong"], x["right"], x["state"]) for x in out["corrections"]["items"]] == [
        ("台積店", "台積電", "applied")]


@pytest.mark.parametrize("route", ["mode", "restore-raw"])
def test_rerun_without_correction_keeps_correction_warning(app_client, route):
    app, c = app_client
    store = app.state.store
    store.save(base(mode="meeting", transcriptRaw=[seg(10, "Jefry 會負責"), seg(20, "台積店很大")],
                    warnings=["名詞校正失敗，已略過：boom"]))
    if route == "mode":
        assert c.post("/api/meetings/m1/mode", json={"mode": "lecture"}).status_code == 200
    else:
        assert c.post("/api/meetings/m1/restore-raw").status_code == 200
    run(app)
    assert store.get("m1")["warnings"] == ["名詞校正失敗，已略過：boom"]


def test_no_chaining_in_one_batch():
    tx = [seg(10, "放在維新群島的公司")]
    new, corr = merge_corrections(None, [prop("維新群島", "維京群島", [10]),
                                         prop("維京群島", "英屬維京群島", [10])], tx)
    assert new[0]["text"] == "放在維京群島的公司"
    assert [c["wrong"] for c in corr["items"]] == ["維新群島"]
