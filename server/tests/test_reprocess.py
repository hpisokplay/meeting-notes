"""Task C (2026-10-06 chairman quality): reprocess pipeline, review / mode / restore-raw endpoints,
settings glossary + model migration, glossary in transcribe payloads."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import ai
from server.ai import handle_ai_job
from server.app import create_app
from server.fallback import FallbackThread, glossary_hint
from server.items import hash8
from server.jobs import JobQueue, apply_transcript_result
from server.render import meeting_to_markdown
from server.storage import DEFAULT_GLOSSARY, Store
from server.uploads import create_meeting
from server.verify import review_item

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
SEGS = [
    {"speaker": "說話者 1", "text": "我們談台積店的案子，台積店很大", "t": 10.0},
    {"speaker": "說話者 2", "text": "Jefry 會負責報價", "t": 20.0},
    {"speaker": "說話者 1", "text": "只要繳五千塊就好", "t": 30.0},
]
CORR = [{"wrong": "台積店", "right": "台積電", "confidence": "high", "reason": "r", "ts": [10]},
        {"wrong": "Jefry", "right": "Jeffrey", "confidence": "medium", "reason": "r", "ts": [20]},
        {"wrong": "不存在", "right": "某詞", "confidence": "high", "reason": "r", "ts": []}]
SLIPS = [{"t": 30, "quote": "只要繳五千塊就好", "issue": "前文稅額一億，減半應為五千萬",
          "suggestion": "依上下文疑為五千萬"}]


@pytest.fixture
def sj(tmp_path):
    store = Store(tmp_path / "data")
    return store, JobQueue(store)


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_CORRECTIONS", json.dumps(CORR, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_SLIPS", json.dumps(SLIPS, ensure_ascii=False))
    monkeypatch.setenv("FAKE_CLAUDE_KIND", "lecture")


def transcribed(store, jobs, mid="m1", **kw):
    """A meeting whose transcription just finished (the real hook enqueues the reprocess job)."""
    jid = jobs.enqueue("transcribe", mid, {})
    m = {"id": mid, "title": "t", "createdAt": 1, "status": "transcribing", "jobId": jid,
         "transcript": [], "summary": None}
    m.update(kw)
    store.save(m)
    job = jobs.claim(["transcribe"], "gpu-worker")
    assert apply_transcript_result(store, jobs, job, {"segments": SEGS, "durationSec": 40, "engine": "gpu"})
    return store.get(mid)["aiJobId"]


def run_claimed(store, jobs, cmd=FAKE):
    job = jobs.claim(["ai"], "nas-ai")
    handle_ai_job(store, job, cmd, jobs)
    return job


def log_calls(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        out.append((json.loads(c["argv"][c["argv"].index("--json-schema") + 1]),
                    c["argv"][c["argv"].index("--model") + 1], c["stdin"]))
    return out


# ---------------------------------------------------------------- pipeline
def test_pipeline_after_transcription_lecture(sj, fake_env, monkeypatch, tmp_path):
    store, jobs = sj
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    ai_jid = transcribed(store, jobs)
    j = jobs.get(ai_jid)
    assert j["payload"] == {"action": "reprocess"}
    assert store.get("m1")["progress"]["action"] == "reprocess"

    seen = []  # (job stage, meeting progress) while the job runs
    real = jobs.progress

    def spy(jid, stage, pct, msg=""):
        mp = store.get("m1").get("progress") or {}
        seen.append((stage, msg, mp.get("stage"), mp.get("action"), store.get("m1")["status"]))
        return real(jid, stage, pct, msg)
    monkeypatch.setattr(jobs, "progress", spy)
    run_claimed(store, jobs)
    monkeypatch.undo()

    stages = [s[0] for s in seen if s[0] != "claude"]
    stages = [x for i, x in enumerate(stages) if not i or stages[i - 1] != x]  # the AI filter is a correction sub-step
    assert stages == ["correction", "mode", "summary", "report", "slips", "verify", "adjudicate"]  # no notes yet
    corr_msgs = [s[1] for s in seen if s[0] == "correction"]
    assert corr_msgs[0] == "名詞校正中（第 1/1 段）" and "AI 複核可能錯字中（1/1 批）" in corr_msgs
    msgs = {s[0]: s[1] for s in seen}
    assert msgs["mode"] == "判斷講座／會議"
    assert msgs["summary"] == "整理摘要中" and msgs["slips"] == "檢查口誤中" and msgs["verify"] == "驗證中"
    assert msgs["adjudicate"] == "AI 審稿中"
    # the meeting carried the stage + action while summarizing (checked at the next report)
    assert ("summary", "reprocess", "summarizing") in [(s[2], s[3], s[4]) for s in seen]

    m = store.get("m1")
    assert m["status"] == "done" and m["progress"]["stage"] == "done" and m["error"] is None
    assert m["transcriptRaw"] == SEGS
    assert m["transcript"][0]["text"] == "我們談台積電的案子，台積電很大"
    assert m["transcript"][1]["text"] == "Jefry 會負責報價"  # medium: not applied
    corr = {c["wrong"]: c for c in m["corrections"]["items"]}
    assert corr["台積店"]["state"] == "applied" and corr["台積店"]["count"] == 2 and corr["台積店"]["ts"] == [10.0]
    assert corr["Jefry"]["state"] == "pending" and corr["Jefry"]["count"] == 1
    assert "不存在" not in corr  # does not occur in the transcript
    assert m["mode"] == "lecture"
    assert set(m["summary"]) == {"conclusions", "advice", "risks", "cases", "mainPoints", "qa", "reportIntro", "reportPoints"}
    it = m["summary"]["conclusions"][0]
    assert len(it["id"]) == 8 and it["src"][0]["t"] == 10.0
    kinds = {r["kind"]: r for r in m["review"]["items"]}
    assert kinds["term"]["text"] == "Jefry" and kinds["term"]["suggestion"] == "Jeffrey"
    assert kinds["slip"]["text"] == "只要繳五千塊就好" and kinds["slip"]["t"] == 30.0
    assert kinds["slip"]["suggestion"] == "依上下文疑為五千萬"
    assert all(r["state"] == "pending" for r in m["review"]["items"])
    # the summary was made from the CORRECTED transcript, with opus by default
    calls = log_calls(log)
    summ = [c for c in calls if "conclusions" in c[0]["properties"]]
    assert len(summ) == 1 and "台積電" in summ[0][2] and "台積店" not in summ[0][2]
    assert {c[1] for c in calls} == {"opus"}
    corr_call = next(c for c in calls if "wrong" in json.dumps(c[0]))
    assert corr_call[2].startswith("【詞庫】\n股利、")  # settings glossary is the reference


def test_reprocess_regenerates_existing_notes_and_backs_up(sj, fake_env):
    store, jobs = sj
    old_notes = {"outline": [{"title": "舊", "points": []}], "concepts": [], "tables": [], "figures": [],
                 "quiz": [{"q": "舊題", "a": "舊答"}]}
    transcribed(store, jobs, summary={"actionItems": ["舊待辦"], "mainPoints": ["舊重點"], "qa": []},
                notes=old_notes)
    run_claimed(store, jobs)
    m = store.get("m1")
    assert m["alt"]["summary:actionItems"] == ["舊待辦"] and m["alt"]["notes:quiz"] == old_notes["quiz"]
    assert m["notes"]["outline"][0]["title"] == "第一節主題" and m["notes"]["outline"][0]["src"]
    assert isinstance(m["notes"]["generatedAt"], int)


def test_reprocess_keeps_known_corrections_and_rejections(sj, fake_env):
    store, jobs = sj
    transcribed(store, jobs, mode="meeting",
                corrections={"items": [{"wrong": "x", "right": "y", "state": "applied"}], "at": 1})
    assert "corrections" not in store.get("m1")  # a new transcription drops old corrections
    store.update("m1", lambda m: m.update(corrections={"items": [
        {"wrong": "台積店", "right": "台積電", "confidence": "medium", "count": 2, "ts": [10.0],
         "state": "rejected"}], "at": 1}))
    run_claimed(store, jobs)
    m = store.get("m1")
    assert "台積店" in m["transcript"][0]["text"]  # a rejected pair is never re-applied
    assert m["mode"] == "meeting" and set(m["summary"]) == {"actionItems", "mainPoints", "qa", "reportIntro", "reportPoints"}


def test_reprocess_aborts_if_transcript_edited_meanwhile(sj, fake_env, monkeypatch):
    store, jobs = sj
    transcribed(store, jobs)
    real = ai._reprocess

    def edit_then(ctx, payload):
        apply = real(ctx, payload)
        store.update("m1", lambda m: m["transcript"][0].update(text="手動改過"))
        return apply
    monkeypatch.setitem(ai.HANDLERS, "reprocess", edit_then)
    job = run_claimed(store, jobs)
    assert jobs.get(job["id"])["state"] == "failed" and "修改" in jobs.get(job["id"])["error"]
    assert store.get("m1")["transcript"][0]["text"] == "手動改過"


def test_summary_action_progress_has_action(sj, monkeypatch):
    store, jobs = sj
    jid = jobs.enqueue("ai", "m1", {"action": "summary"})
    store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "summarizing", "aiJobId": jid,
                "transcript": SEGS, "summary": None, "progress": {"stage": "summarizing"}})
    seen = []
    real = jobs.progress

    def spy(*a, **k):
        seen.append(dict(store.get("m1")["progress"]))
        return real(*a, **k)
    monkeypatch.setattr(jobs, "progress", spy)
    run_claimed(store, jobs)
    assert any(p.get("action") == "summary" for p in seen)


# ---------------------------------------------------------------- HTTP
@pytest.fixture
def app_client(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, claude_cmd=FAKE)
    return app, TestClient(app)


def base_meeting(**kw):
    items = [{"id": "aaaaaaaa", "text": "重點甲：五千萬", "src": [{"t": 30.0, "quote": "只要繳五千塊就好"}]},
             {"id": "bbbbbbbb", "text": "重點乙：說明", "src": []}]
    m = {"id": "m1", "title": "t", "createdAt": 1, "status": "done", "transcript": [dict(s) for s in SEGS],
         "summary": {"actionItems": ["舊待辦"], "mainPoints": items, "qa": []}}
    num = review_item("number", "summary.mainPoints", "aaaaaaaa", items[0]["text"], "數字找不到", t=30.0)
    name = review_item("name", "summary.mainPoints", "aaaaaaaa", items[0]["text"], "名稱找不到", t=30.0)
    legacy = review_item("name", "summary.actionItems", hash8("舊待辦"), "舊待辦", "名稱找不到")
    term = review_item("term", "transcript", None, "Jefry", "可能聽錯", suggestion="Jeffrey", t=20.0)
    slip = review_item("slip", "transcript", None, "只要繳五千塊就好", "口誤", suggestion="五千萬", t=30.0)
    m["review"] = {"items": [num, name, legacy, term, slip], "at": 1}
    m["corrections"] = {"items": [{"wrong": "Jefry", "right": "Jeffrey", "confidence": "medium",
                                   "count": 1, "ts": [20.0], "state": "pending"}], "at": 1}
    m.update(kw)
    return m, {"num": num["id"], "name": name["id"], "legacy": legacy["id"], "term": term["id"],
               "slip": slip["id"]}


def rv(m, rid):
    return next(r for r in m["review"]["items"] if r["id"] == rid)


def test_review_confirm_edit_delete(app_client):
    app, c = app_client
    m, ids = base_meeting()
    app.state.store.save(m)
    r = c.post(f"/api/meetings/m1/review/{ids['num']}", json={"action": "confirm"})
    assert r.status_code == 200 and rv(r.json(), ids["num"])["state"] == "confirmed"
    assert r.json()["updatedAt"] > 1

    r = c.post(f"/api/meetings/m1/review/{ids['name']}", json={"action": "edit", "text": "重點甲：講者原話五千塊"})
    out = r.json()
    assert out["summary"]["mainPoints"][0] == {"id": "aaaaaaaa", "text": "重點甲：講者原話五千塊",
                                              "src": [{"t": 30.0, "quote": "只要繳五千塊就好"}]}
    assert rv(out, ids["name"])["state"] == "edited"

    # legacy string entry: delete moves it to alt["deleted:<section>"]
    r = c.post(f"/api/meetings/m1/review/{ids['legacy']}", json={"action": "delete"})
    out = r.json()
    assert out["summary"]["actionItems"] == [] and out["alt"]["deleted:summary.actionItems"] == ["舊待辦"]
    assert rv(out, ids["legacy"])["state"] == "deleted"


def test_review_delete_resolves_sibling_doubts(app_client):
    app, c = app_client
    m, ids = base_meeting()
    app.state.store.save(m)
    out = c.post(f"/api/meetings/m1/review/{ids['num']}", json={"action": "delete"}).json()
    assert [x["id"] for x in out["summary"]["mainPoints"]] == ["bbbbbbbb"]
    assert out["alt"]["deleted:summary.mainPoints"][0]["id"] == "aaaaaaaa"
    assert rv(out, ids["num"])["state"] == "deleted" and rv(out, ids["name"])["state"] == "deleted"


def test_review_edit_legacy_string_keeps_siblings_pointing_at_entry(app_client):
    app, c = app_client
    m, ids = base_meeting()
    extra = review_item("calc", "summary.actionItems", hash8("舊待辦"), "舊待辦", "x")
    m["review"]["items"].append(extra)
    app.state.store.save(m)
    out = c.post(f"/api/meetings/m1/review/{ids['legacy']}", json={"action": "edit", "text": "新待辦"}).json()
    assert out["summary"]["actionItems"] == ["新待辦"]
    sib = rv(out, extra["id"])
    assert sib["state"] == "pending" and sib["text"] == "新待辦" and sib["itemId"] == hash8("新待辦")


def test_review_missing_target_is_resolved_not_404(app_client):
    app, c = app_client
    m, ids = base_meeting()
    m["summary"]["mainPoints"] = []  # user removed the item by hand
    app.state.store.save(m)
    r = c.post(f"/api/meetings/m1/review/{ids['num']}", json={"action": "delete"})
    assert r.status_code == 200 and rv(r.json(), ids["num"])["state"] == "deleted"
    r = c.post(f"/api/meetings/m1/review/{ids['name']}", json={"action": "edit", "text": "x"})
    assert r.status_code == 200 and rv(r.json(), ids["name"])["state"] == "edited"


def test_review_term_actions_apply_to_transcript(app_client):
    app, c = app_client
    m, ids = base_meeting()
    app.state.store.save(m)
    out = c.post(f"/api/meetings/m1/review/{ids['term']}", json={"action": "confirm"}).json()
    assert out["transcript"][1]["text"] == "Jeffrey 會負責報價"
    assert out["transcriptRaw"][1]["text"] == "Jefry 會負責報價"  # raw saved before the first change
    c0 = out["corrections"]["items"][0]
    assert c0["state"] == "applied" and c0["right"] == "Jeffrey" and c0["count"] == 1

    m, ids = base_meeting()
    app.state.store.save(m)
    out = c.post(f"/api/meetings/m1/review/{ids['term']}", json={"action": "edit", "text": "Jeffery"}).json()
    assert out["transcript"][1]["text"] == "Jeffery 會負責報價"
    assert out["corrections"]["items"][0]["right"] == "Jeffery"
    assert rv(out, ids["term"])["state"] == "edited"

    m, ids = base_meeting()
    app.state.store.save(m)
    out = c.post(f"/api/meetings/m1/review/{ids['term']}", json={"action": "delete"}).json()
    assert out["transcript"][1]["text"] == "Jefry 會負責報價"
    assert out["corrections"]["items"][0]["state"] == "rejected"


def test_review_slip_and_validation(app_client):
    app, c = app_client
    m, ids = base_meeting()
    app.state.store.save(m)
    out = c.post(f"/api/meetings/m1/review/{ids['slip']}", json={"action": "confirm"}).json()
    assert rv(out, ids["slip"])["state"] == "confirmed" and out["transcript"] == SEGS
    assert c.post("/api/meetings/m1/review/ffffffff", json={"action": "confirm"}).status_code == 404
    assert c.post(f"/api/meetings/m1/review/{ids['num']}", json={"action": "nope"}).status_code == 400
    assert c.post(f"/api/meetings/m1/review/{ids['num']}", json={"action": "edit", "text": " "}).status_code == 400
    assert c.post("/api/meetings/zz/review/x", json={"action": "confirm"}).status_code == 404


def test_reprocess_endpoint_sets_pipeline_state(app_client):
    app, c = app_client
    m, _ = base_meeting()
    app.state.store.save(m)
    r = c.post("/api/meetings/m1/ai", json={"action": "reprocess"})
    assert r.status_code == 200
    jid = r.json()["jobId"]
    m = app.state.store.get("m1")
    assert m["aiJobId"] == jid and m["status"] == "summarizing" and m["progress"]["action"] == "reprocess"
    assert app.state.jobs.get(jid)["payload"] == {"action": "reprocess"}
    assert c.get(f"/api/jobs/{jid}").json()["action"] == "reprocess"
    assert c.post("/api/meetings/m1/ai", json={"action": "reprocess"}).status_code == 409  # already running
    handle_ai_job(app.state.store, app.state.jobs.claim(["ai"], "nas-ai"), FAKE, app.state.jobs)
    m = app.state.store.get("m1")
    assert m["status"] == "done" and m["summary"]["mainPoints"][0]["src"]
    assert m["alt"]["summary:mainPoints"][0]["id"] == "aaaaaaaa"  # old version kept


def test_mode_endpoint(app_client, tmp_path, monkeypatch):
    app, c = app_client
    m, _ = base_meeting(mode="meeting")
    app.state.store.save(m)
    assert c.post("/api/meetings/m1/mode", json={"mode": "x"}).status_code == 400
    r = c.post("/api/meetings/m1/mode", json={"mode": "lecture"})
    jid = r.json()["jobId"]
    m = app.state.store.get("m1")
    assert m["mode"] == "meeting" and m["aiJobId"] == jid and m["status"] == "summarizing"
    assert app.state.jobs.get(jid)["payload"] == {"action": "reprocess", "mode": "lecture",
                                                  "correction": False, "slips": False}
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    handle_ai_job(app.state.store, app.state.jobs.claim(["ai"], "nas-ai"), FAKE, app.state.jobs)
    m = app.state.store.get("m1")
    assert m["mode"] == "lecture"
    assert set(m["summary"]) == {"conclusions", "advice", "risks", "cases", "mainPoints", "qa", "reportIntro", "reportPoints"}
    schemas = [c_[0] for c_ in log_calls(log)]
    # no correction / slips / mode: the summary, the 完整整理 report, then the word-review adjudicate
    # (+ one re-ask of the flags the fake left unanswered)
    assert len(schemas) == 4 and "conclusions" in schemas[0]["properties"]
    assert all("span" in x["properties"]["items"]["items"]["properties"] for x in schemas[2:])
    assert any(r["kind"] == "slip" for r in m["review"]["items"])  # existing slip list kept


def test_restore_raw(app_client, fake_env):
    app, c = app_client
    store, jobs = app.state.store, app.state.jobs
    assert c.post("/api/meetings/m1/restore-raw").status_code == 404
    transcribed(store, jobs)
    run_claimed(store, jobs)
    assert "台積電" in store.get("m1")["transcript"][0]["text"]
    r = c.post("/api/meetings/m1/restore-raw")
    assert r.status_code == 200
    m = store.get("m1")
    assert m["transcript"] == SEGS and m["aiJobId"] == r.json()["jobId"]
    assert {c_["state"] for c_ in m["corrections"]["items"]} == {"pending"}
    assert jobs.get(r.json()["jobId"])["payload"] == {"action": "reprocess", "correction": False}
    run_claimed(store, jobs)
    m = store.get("m1")
    assert m["transcript"] == SEGS  # not re-applied automatically
    terms = sorted(r_["text"] for r_ in m["review"]["items"] if r_["kind"] == "term")
    assert terms == ["Jefry", "台積店"]
    # no raw: 409
    store.update("m1", lambda x: x.pop("transcriptRaw"))
    assert c.post("/api/meetings/m1/restore-raw").status_code == 409


def test_put_cannot_overwrite_review_or_raw(app_client):
    app, c = app_client
    m, _ = base_meeting(transcriptRaw=SEGS, mode="lecture")
    app.state.store.save(m)
    body = dict(app.state.store.get("m1"), review={"items": []}, transcriptRaw=[], mode="meeting", title="新")
    c.put("/api/meetings/m1", json=body)
    m = app.state.store.get("m1")
    assert m["title"] == "新" and len(m["review"]["items"]) == 5 and m["transcriptRaw"] == SEGS
    assert m["mode"] == "lecture"
    got = c.get("/api/meetings/m1").json()
    assert {"transcriptRaw", "corrections", "review", "mode"} <= set(got)


# ---------------------------------------------------------------- settings / glossary
def test_settings_model_migration_and_glossary(data_dir):
    s = Store(data_dir)
    (data_dir / "settings.json").write_text(json.dumps({"claudeModel": "sonnet", "tgNotify": False}), "utf-8")
    got = s.get_settings()
    assert got["claudeModel"] == "opus" and got["modelMigrated"] is True and got["tgNotify"] is False
    assert got["glossary"] == "\n".join(DEFAULT_GLOSSARY) and len(DEFAULT_GLOSSARY) >= 60
    s.set_settings({"claudeModel": "sonnet"})  # an explicit choice after the migration sticks
    assert Store(data_dir).get_settings()["claudeModel"] == "sonnet"
    s.set_settings({"glossary": " 股利 \n\n複委託\n"})
    assert s.get_settings()["glossary"] == "股利\n複委託" and s.glossary_list() == ["股利", "複委託"]
    s.set_settings({"tgNotify": True})  # glossary absent -> kept
    assert s.glossary_list() == ["股利", "複委託"]
    s.set_settings({"glossary": 5})  # wrong type ignored
    assert s.glossary_list() == ["股利", "複委託"]


def test_settings_api_glossary(client):
    assert client.get("/api/settings").json()["glossary"].startswith("股利\n")
    client.put("/api/settings", json={"glossary": "A\nB"})
    client.put("/api/settings", json={"claudeModel": "haiku"})
    got = client.get("/api/settings").json()
    assert got["glossary"] == "A\nB" and got["claudeModel"] == "haiku"


def test_transcribe_payload_has_glossary(app_client, tmp_path):
    app, c = app_client
    store, jobs = app.state.store, app.state.jobs
    store.set_settings({"glossary": "股利\nCRS"})
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x" * 10)
    m = create_meeting(store, jobs, [{"name": "a.m4a", "src": f}])
    assert jobs.get(m["jobId"])["payload"] == {"glossary": ["股利", "CRS"]}
    jobs.cancel(m["jobId"])
    store.update(m["id"], lambda x: x.update(status="failed"))
    r = c.post(f"/api/meetings/{m['id']}/retranscribe")
    assert jobs.get(r.json()["jobId"])["payload"] == {"glossary": ["股利", "CRS"]}


def test_fallback_passes_glossary(sj):
    store, jobs = sj
    assert glossary_hint(["股利", "", "CRS"]) == "股利、CRS"
    assert glossary_hint(["x" * 150, "y" * 60]) == "x" * 150 and glossary_hint([]) is None
    got = {}

    def tr(paths, progress, glossary=None):
        got["g"] = glossary
        return {"segments": [{"speaker": "A", "text": "你好", "t": 0.0}], "durationSec": 1, "engine": "nas-cpu"}
    jid = jobs.enqueue("transcribe", "m1", {"glossary": ["股利"]})
    store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "queued", "jobId": jid,
                "audio": [{"file": "0-a.m4a", "name": "a.m4a", "size": 1}], "transcript": []})
    ft = FallbackThread(store, jobs, tr)
    ft.process(jobs.claim(["transcribe"], "nas-cpu"))
    assert got["g"] == ["股利"]


def test_render_lecture_and_items():
    m = {"title": "講", "mode": "lecture", "transcript": [],
         "summary": {"conclusions": [{"id": "a", "text": "結論一", "src": []}], "advice": [], "risks": ["風險一"],
                     "cases": [], "mainPoints": [], "qa": [{"id": "b", "text": "問：甲？\n答：乙。", "src": []}]}}
    md = meeting_to_markdown(m)
    assert "## 核心結論\n- 結論一" in md and "## 風險提醒\n- 風險一" in md and "## 待辦" not in md
    assert "**Q：甲？**\nA：乙。" in md


def test_retranscribe_reprocess_claimed_while_result_is_being_applied(sj, fake_env, monkeypatch):
    """NAS 2026-10-06 (1791264491261aff4): a RE-transcription of a meeting that already had
    summary / review / corrections / mode. The AI thread claimed the reprocess job in the same second
    it was enqueued, i.e. before apply_transcript_result had stored the new transcript, so the job
    fingerprinted the OLD transcript and failed 「逐字稿在整理期間被修改」 after the whole run."""
    import threading
    import time as _t
    store, jobs = sj
    old = [{"speaker": "說話者 1", "text": "舊的逐字稿內容", "t": 5.0}]
    tjid = jobs.enqueue("transcribe", "m1", {})
    store.save({"id": "m1", "title": "t", "createdAt": 1, "status": "transcribing", "jobId": tjid,
                "transcript": old, "transcriptRaw": old, "mode": "lecture",
                "summary": {"conclusions": [{"id": "a1b2c3d4", "text": "舊結論", "src": [{"t": 5.0}]}]},
                "corrections": {"items": [{"wrong": "x", "right": "y", "state": "applied"}], "at": 1},
                "review": {"items": [], "at": 1}})
    tjob = jobs.claim(["transcribe"], "gpu-worker")
    runs, stored = [], threading.Event()
    real_enqueue = jobs.enqueue
    real_rp = ai._reprocess

    def slow_reprocess(ctx, payload):  # Claude runs for minutes: the result is stored meanwhile
        apply = real_rp(ctx, payload)
        stored.wait(10)
        return apply
    monkeypatch.setitem(ai.HANDLERS, "reprocess", slow_reprocess)

    def enqueue_then_claim(kind, mid, payload):  # the AI thread polls and wins the race
        jid = real_enqueue(kind, mid, payload)
        if kind == "ai":
            th = threading.Thread(target=lambda: runs.append(run_claimed(store, jobs)))
            th.start()
            runs.append(th)
            _t.sleep(0.5)  # the AI thread reads the meeting now (before the result is stored)
        return jid
    monkeypatch.setattr(jobs, "enqueue", enqueue_then_claim)
    assert apply_transcript_result(store, jobs, tjob, {"segments": SEGS, "durationSec": 40, "engine": "gpu"})
    stored.set()
    runs[0].join(30)
    job = runs[1]
    got = jobs.get(job["id"])
    assert got["state"] == "done", got.get("error")
    m = store.get("m1")
    assert m["status"] == "done" and m["error"] is None and m["aiJobId"] == job["id"]
    assert m["transcriptRaw"] == SEGS and m["transcript"][0]["text"] == "我們談台積電的案子，台積電很大"
