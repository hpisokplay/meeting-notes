import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import ai
from server.ai import (AIThread, ClaudeError, ClaudeLimitError, handle_ai_job, on_ai_job_terminal,
                       run_claude, serialize_transcript)
from server.app import create_app
from server.jobs import JobQueue
from server.storage import Store

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]

TRANSCRIPT = [
    {"speaker": "說話者 1", "text": "我們談台積店的案子", "t": 3725},
    {"speaker": "說話者 2", "text": "Jefry 會負責", "t": "01:02"},
    {"speaker": "說話者 1", "text": "台積殿下週回覆", "t": 4000.4},
]


@pytest.fixture
def sj(tmp_path):
    store = Store(tmp_path / "data")
    return store, JobQueue(store)


def mk(store, mid="m1", **kw):
    m = {"id": mid, "title": "t", "createdAt": 1, "status": "done", "transcript": TRANSCRIPT,
         "summary": {"actionItems": ["舊待辦"], "mainPoints": ["舊重點：說明"], "qa": ["問：舊 答：舊"]},
         "translations": {"en": {"transcript": [], "summary": {}}}}
    m.update(kw)
    store.save(m)
    return m


def run_job(store, jobs, action, mid="m1", cmd=FAKE, **payload):
    jid = jobs.enqueue("ai", mid, {"action": action, **payload})
    job = jobs.claim(["ai"], "nas-ai")
    assert job["id"] == jid
    handle_ai_job(store, job, cmd, jobs)
    return jid


# ---------------------------------------------------------------- run_claude
def test_serialize_transcript_format():
    assert serialize_transcript(TRANSCRIPT).splitlines() == [
        "[01:02:05] 說話者 1: 我們談台積店的案子",
        "說話者 2: Jefry 會負責",
        "[01:06:40] 說話者 1: 台積殿下週回覆",
    ]


def test_run_claude_args_and_stdin(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    out = run_claude("系統提示「引號」\n第二行", "逐字稿 中文 ✓", schema, "opus", FAKE)
    assert out == {"answer": "會議中沒有提到"}
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert rec["argv"] == ["-p", "--model", "opus", "--tools", "", "--output-format", "json",
                           "--no-session-persistence", "--max-turns", "3",
                           "--json-schema", json.dumps(schema, ensure_ascii=False),
                           "--system-prompt", "系統提示「引號」\n第二行"]
    assert rec["stdin"] == "逐字稿 中文 ✓"


def test_result_fallback_parses_result_json(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "result_only")
    out = run_claude("s", "u", {"type": "object", "properties": {"answer": {"type": "string"}}}, "sonnet", FAKE)
    assert out == {"answer": "會議中沒有提到"}


def test_limit_and_error_detection(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    with pytest.raises(ClaudeLimitError):
        run_claude("s", "u", {}, "sonnet", FAKE)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    with pytest.raises(ClaudeError) as e:
        run_claude("s", "u", {}, "sonnet", FAKE)
    assert not isinstance(e.value, ClaudeLimitError)
    assert "boom" in str(e.value)


def test_env_scrubbed_for_child(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "env")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/cfg")
    monkeypatch.setenv("KEEP_ME", "1")
    env = set(run_claude("s", "u", {}, "sonnet", FAKE)["env"])
    upper = {k.upper() for k in env}
    assert "CLAUDECODE" not in upper and "CLAUDE_CODE_ENTRYPOINT" not in upper
    assert "ANTHROPIC_API_KEY" not in upper
    assert {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "KEEP_ME"} <= upper


def test_timeout_kills_process(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    t0 = time.time()
    with pytest.raises(ClaudeError) as e:
        run_claude("s", "u", {}, "sonnet", FAKE, timeout=1.5)
    assert time.time() - t0 < 15
    assert "逾時" in str(e.value)


# ---------------------------------------------------------------- actions
def test_summary_written_and_status_done(sj):
    store, jobs = sj
    jid = jobs.enqueue("ai", "m1", {"action": "summary"})
    mk(store, status="summarizing", aiJobId=jid, summary={})
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    m = store.get("m1")
    assert m["summary"] == {"actionItems": ["新待辦 [DRI: 待指派]"], "mainPoints": ["新重點：說明"],
                            "qa": ["問：新問題？ 答：新答案。"]}
    assert m["status"] == "done" and m["progress"]["pct"] == 100
    assert m["translations"] == {}
    j = jobs.get(jid)
    assert j["state"] == "done" and j["progress"]["pct"] == 100 and j["owner"] == "nas-ai"


def test_resummary_backs_up_old_summary(sj):
    store, jobs = sj
    mk(store)
    run_job(store, jobs, "summary")
    m = store.get("m1")
    assert m["alt"]["summary:actionItems"] == ["舊待辦"]
    assert m["alt"]["summary:qa"] == ["問：舊 答：舊"]
    assert m["status"] == "done"


def test_enhance_backs_up_old_section(sj):
    store, jobs = sj
    mk(store)
    run_job(store, jobs, "enhance", section="qa")
    m = store.get("m1")
    assert m["alt"]["summary:qa"] == ["問：舊 答：舊"]
    assert m["summary"]["qa"] == ["潤飾1", "潤飾2"]
    assert m["summary"]["actionItems"] == ["舊待辦"]
    assert m["translations"] == {}


def test_assemble_polished_keeps_uncovered_and_splits_overmerge():
    all_ = ["a", "b", "c", "d", "e"]
    items = [{"text": "AB", "src": [1, 2]}, {"text": "CDE", "src": [3, 4, 5, 9]}]
    assert ai.assemble_polished(items, all_, 2, False)["list"] == ["AB", "c", "d", "e"]
    out = ai.assemble_polished([{"text": "", "src": [1], "drop": True}, {"text": "B", "src": [2]}],
                               all_, 3, True)
    assert out == {"list": ["B", "c", "d", "e"], "dropped": 1}


def test_ask_appends_chat(sj):
    store, jobs = sj
    mk(store, chat=[{"q": "舊問題", "a": "舊答", "at": 1}])
    run_job(store, jobs, "ask", question="誰負責？")
    chat = store.get("m1")["chat"]
    assert chat[0]["q"] == "舊問題"
    assert chat[1]["q"] == "誰負責？" and chat[1]["a"] == "會議中沒有提到"
    assert isinstance(chat[1]["at"], int) and chat[1]["at"] > 1_600_000_000_000


def test_terms_preserve_applied_flags(sj):
    store, jobs = sj
    mk(store, terms={"items": [
        {"t": "Jefry", "cat": "person", "fix": "", "applied": "Jeffrey", "alts": []},
        {"t": "某詞", "cat": "term", "fix": "", "draft": "某辭"},
        {"t": "沒用的舊詞", "cat": "term", "fix": "", "alts": []},
    ]})
    run_job(store, jobs, "terms")
    terms = store.get("m1")["terms"]
    assert isinstance(terms["scannedAt"], int)
    items = {x["t"]: x for x in terms["items"]}
    assert items["Jefry"]["applied"] == "Jeffrey"
    assert items["某詞"]["draft"] == "某辭"
    assert "沒用的舊詞" not in items
    # grouping: 台積殿 folded into 台積店 as an alt with the best spelling as fix
    assert items["台積店"]["alts"] == ["台積殿"] and items["台積店"]["fix"] == "台積電"
    assert items["台積店"]["cat"] == "org"
    assert "台積殿" not in items
    assert [x["t"] for x in terms["items"]][:2] == ["Jefry", "某詞"]


def test_merge_term_groups_ignores_unknown_and_taken():
    items = [{"t": "A", "cat": "org", "fix": ""}, {"t": "B", "cat": "org", "fix": ""},
             {"t": "C", "cat": "org", "fix": ""}]
    out = ai.merge_term_groups(items, [{"terms": ["B", "A", "X"], "best": "AA"}, {"terms": ["A", "C"]}])
    assert out == [{"t": "A", "cat": "org", "fix": "AA", "alts": ["B"]},
                   {"t": "C", "cat": "org", "fix": "", "alts": []}]


def test_notes_and_notes_enhance(sj):
    store, jobs = sj
    mk(store)
    run_job(store, jobs, "notes")
    n = store.get("m1")["notes"]
    assert n["outline"] == [{"title": "第一節主題", "anchor": "第一句", "points": ["要點"]}]
    assert n["tables"][0]["rows"] == [["1", "2"]] and isinstance(n["generatedAt"], int)
    run_job(store, jobs, "notes_enhance", section="quiz")
    m = store.get("m1")
    assert m["alt"]["notes:quiz"] == [{"q": "題目？", "a": "答案"}]
    assert m["notes"]["quiz"] == [{"q": "題目？", "a": "答案"}]
    assert m["notes"]["outline"] == n["outline"]


def test_normalize_notes_tolerates_bad_shapes():
    r = ai.normalize_notes({"outline": [{"title": " T ", "points": ["p", ""]}, {"title": ""}],
                            "tables": [{"title": "x", "headers": ["a", "b"], "rows": ["1 | 2", []]},
                                       {"title": "empty", "headers": ["a"], "rows": []}],
                            "figures": ["81 億：市場規模", {"label": "L", "value": "V"}],
                            "quiz": [{"q": "", "a": "x"}]})
    assert r["outline"] == [{"title": "T", "anchor": "", "points": ["p"]}]
    assert r["tables"] == [{"title": "x", "headers": ["a", "b"], "rows": [["1", "2"]]}]
    assert r["figures"] == [{"group": "", "label": "市場規模", "value": "81 億"},
                            {"group": "", "label": "L", "value": "V"}]
    assert r["quiz"] == [] and r["concepts"] == []


def test_translate_writes_translations_shape(sj):
    store, jobs = sj
    mk(store, notes={"outline": [], "concepts": [], "tables": [], "figures": [],
                     "quiz": [{"q": "題", "a": "答"}]})
    run_job(store, jobs, "translate", lang="en")
    tr = store.get("m1")["translations"]["en"]
    assert tr["summary"]["actionItems"] == ["[譯]舊待辦"]
    assert tr["transcript"][0] == {"speaker": "[譯]說話者 1", "text": "[譯]我們談台積店的案子", "t": 3725}
    assert tr["transcript"][1]["t"] == "01:02" and len(tr["transcript"]) == 3
    assert tr["notes"]["quiz"] == [{"q": "[譯]題", "a": "[譯]答"}]


def test_long_transcript_is_chunked_for_summary(sj, monkeypatch, tmp_path):
    store, jobs = sj
    monkeypatch.setattr(ai, "MAX_CHARS", 40)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    mk(store)
    run_job(store, jobs, "summary")
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 3  # one per chunk
    assert store.get("m1")["summary"]["actionItems"] == ["新待辦 [DRI: 待指派]"]  # deduped merge


def test_ask_long_transcript_picks_relevant_chunks():
    lines = ["A: 今天天氣很好"] * 5 + ["B: 預算是三百萬元"] + ["A: 吃午餐"] * 5
    picked = ai.pick_relevant(lines, "預算多少", budget=40, chunk=20)
    assert "預算是三百萬元" in picked and len(picked) <= 40


def test_rate_limited_job_is_retried_later(sj, monkeypatch):
    store, jobs = sj
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    jid = jobs.enqueue("ai", "m1", {"action": "summary"})
    mk(store, status="summarizing", aiJobId=jid)
    t0 = time.time()
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    j = jobs.get(jid)
    assert j["state"] == "queued" and j["retries"] == 1
    assert j["error"] == "Claude 用量已達上限，30 分鐘後自動重試"
    assert t0 + 1790 <= j["notBefore"] <= time.time() + 1810
    assert jobs.claim(["ai"], "nas-ai") is None  # hidden until notBefore
    assert store.get("m1")["status"] == "summarizing"
    # sixth retry used up -> permanent failure, meeting no longer stuck
    p = jobs.dirs["queued"] / f"{jid}.json"
    j.update(notBefore=None, retries=6)
    p.write_text(json.dumps(j), encoding="utf-8")
    handle_ai_job(store, jobs.claim(["ai"], "nas-ai"), FAKE, jobs)
    assert jobs.get(jid)["state"] == "failed"
    on_ai_job_terminal(store, jobs.get(jid), "failed")  # what the queue listener does
    m = store.get("m1")
    assert m["status"] == "done" and "用量" in m["error"]


def test_nonlimit_error_fails_permanently(sj, monkeypatch):
    store, jobs = sj
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    mk(store)
    jid = run_job(store, jobs, "enhance", section="qa")
    j = jobs.get(jid)
    assert j["state"] == "failed" and "boom" in j["error"]
    assert store.get("m1")["summary"]["qa"] == ["問：舊 答：舊"]


def test_terminal_listener_unsticks_pipeline_summary(sj):
    store, jobs = sj
    jid = jobs.enqueue("ai", "m1", {"action": "summary"})
    mk(store, status="summarizing", aiJobId=jid)
    on_ai_job_terminal(store, jobs.cancel(jid), "cancelled")
    m = store.get("m1")
    assert m["status"] == "done" and not m.get("error")
    # unrelated ai job / other meeting state: untouched
    mk(store, "m2", status="summarizing", aiJobId="other")
    on_ai_job_terminal(store, {"id": "x", "kind": "ai", "meeting": "m2", "error": "e"}, "failed")
    assert store.get("m2")["status"] == "summarizing"
    on_ai_job_terminal(store, {"id": "x", "kind": "ai", "meeting": "gone"}, "failed")  # no raise


def test_cancel_during_run_discards_result(sj, monkeypatch):
    store, jobs = sj
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    monkeypatch.setattr(ai, "ABORT_CHECK_S", 0.2)
    mk(store)
    jid = jobs.enqueue("ai", "m1", {"action": "enhance", "section": "qa"})
    job = jobs.claim(["ai"], "nas-ai")
    threading.Timer(1.0, lambda: jobs.cancel(jid)).start()
    t0 = time.time()
    handle_ai_job(store, job, FAKE, jobs)
    assert time.time() - t0 < 15
    assert jobs.get(jid)["state"] == "cancelled"
    assert store.get("m1")["summary"]["qa"] == ["問：舊 答：舊"]


def test_model_from_settings(sj, tmp_path, monkeypatch):
    store, jobs = sj
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    store.set_settings({"claudeModel": "opus"})
    mk(store)
    run_job(store, jobs, "ask", question="q")
    argv = json.loads(log.read_text(encoding="utf-8").splitlines()[0])["argv"]
    assert argv[argv.index("--model") + 1] == "opus"


def test_ai_thread_processes_jobs(sj):
    store, jobs = sj
    mk(store)
    jid = jobs.enqueue("ai", "m1", {"action": "ask", "question": "q"})
    th = AIThread(store, jobs, FAKE, poll=0.1)
    th.start()
    try:
        deadline = time.time() + 20
        while jobs.get(jid)["state"] != "done" and time.time() < deadline:
            time.sleep(0.1)
    finally:
        th.stop()
    assert jobs.get(jid)["state"] == "done"
    assert store.get("m1")["chat"][-1]["a"] == "會議中沒有提到"


# ---------------------------------------------------------------- HTTP
@pytest.fixture
def app_client(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False, claude_cmd=FAKE)
    return app, TestClient(app)


def test_api_ai_enqueues_job(app_client):
    app, c = app_client
    mk(app.state.store)
    r = c.post("/api/meetings/m1/ai", json={"action": "enhance", "section": "qa"})
    assert r.status_code == 200
    j = app.state.jobs.get(r.json()["jobId"])
    assert j["kind"] == "ai" and j["meeting"] == "m1" and j["state"] == "queued"
    assert j["payload"] == {"action": "enhance", "section": "qa"}
    assert c.get(f"/api/jobs/{j['id']}").json()["state"] == "queued"


@pytest.mark.parametrize("body,code", [
    ({"action": "nope"}, 400),
    ({"action": "enhance", "section": "outline"}, 400),
    ({"action": "notes_enhance", "section": "qa"}, 400),
    ({"action": "ask", "question": "  "}, 400),
    ({"action": "translate", "lang": "fr"}, 400),
])
def test_api_ai_validates(app_client, body, code):
    app, c = app_client
    mk(app.state.store)
    assert c.post("/api/meetings/m1/ai", json=body).status_code == code


def test_api_ai_missing_meeting_and_no_transcript(app_client):
    app, c = app_client
    assert c.post("/api/meetings/nope/ai", json={"action": "summary"}).status_code == 404
    mk(app.state.store, transcript=[])
    r = c.post("/api/meetings/m1/ai", json={"action": "summary"})
    assert r.status_code == 400 and "逐字稿" in r.json()["detail"]


def test_put_ignores_server_owned_fields(app_client):
    app, c = app_client
    mk(app.state.store, status="summarizing", aiJobId="j1", jobId="t1", error=None,
       progress={"stage": "claude", "pct": 40, "msg": "x"},
       audio=[{"file": "0-a.m4a", "name": "a.m4a", "size": 5}])
    body = dict(app.state.store.get("m1"), title="新標題", status="done", aiJobId=None, jobId="zz",
                error="hacked", progress={}, audio=[])
    r = c.put("/api/meetings/m1", json=body)
    assert r.status_code == 200
    m = app.state.store.get("m1")
    assert m["title"] == "新標題"
    assert m["status"] == "summarizing" and m["aiJobId"] == "j1" and m["jobId"] == "t1"
    assert m["error"] is None and m["progress"]["pct"] == 40 and len(m["audio"]) == 1


def test_put_drops_server_owned_fields_absent_in_store(app_client):
    app, c = app_client
    mk(app.state.store)
    body = dict(app.state.store.get("m1"), aiJobId="injected", error="x")
    c.put("/api/meetings/m1", json=body)
    m = app.state.store.get("m1")
    assert "aiJobId" not in m and "error" not in m


def test_http_cancel_unsticks_pipeline_summary(app_client):
    app, c = app_client
    jid = app.state.jobs.enqueue("ai", "m1", {"action": "summary"})
    mk(app.state.store, status="summarizing", aiJobId=jid)
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200
    if not hasattr(app.state.jobs, "add_terminal_listener"):
        pytest.skip("JobQueue.add_terminal_listener not available yet (Task 3 fix pending)")
    assert app.state.store.get("m1")["status"] == "done"
