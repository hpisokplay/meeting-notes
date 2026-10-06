import json

from server import notify as N
from server.storage import Store


def setup_tg(tmp_path, monkeypatch):
    d = tmp_path / "tg"
    d.mkdir()
    (d / "token").write_text("TOK", "utf-8")
    (d / "allowed_ids").write_text("123 456\n", "utf-8")
    monkeypatch.setenv("MEETINGS_TG_DIR", str(d))


def test_notify_noop_without_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETINGS_TG_DIR", str(tmp_path / "nothing"))
    assert N.notify("hi") is False


def test_notify_posts_first_id_and_honours_setting(tmp_path, monkeypatch):
    setup_tg(tmp_path, monkeypatch)
    seen = {}

    class R:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake(req, timeout):
        seen.update(url=req.full_url, body=json.loads(req.data), timeout=timeout)
        return R()
    monkeypatch.setattr(N.urllib.request, "urlopen", fake)
    s = Store(tmp_path / "d")
    assert N.notify("嗨", s) is True
    assert seen["url"].endswith("botTOK/sendMessage") and seen["body"]["chat_id"] == "123"
    assert seen["timeout"] == 10
    s.set_settings({"tgNotify": False})
    seen.clear()
    assert N.notify("嗨", s) is False and not seen


def test_notify_never_raises_on_network_error(tmp_path, monkeypatch):
    setup_tg(tmp_path, monkeypatch)
    monkeypatch.setattr(N.urllib.request, "urlopen", lambda *a, **k: 1 / 0)
    assert N.notify("x") is False


def test_hooks_texts(tmp_path):
    s = Store(tmp_path)
    s.save({"id": "m1", "title": "週會", "createdAt": 1, "status": "done", "aiJobId": "a1", "jobId": "t1"})
    sent = []
    send = lambda t, st=None: sent.append(t)
    assert N.on_job_done(s, {"id": "a1", "kind": "ai", "meeting": "m1"}, send)
    assert sent[-1] == "✅《週會》轉好了\nhttps://ddys.tail95e8ec.ts.net:9443/#/m/m1"
    assert not N.on_job_done(s, {"id": "other", "kind": "ai", "meeting": "m1"}, send)
    assert N.on_job_terminal(s, {"id": "t1", "kind": "transcribe", "meeting": "m1", "error": "壞了"}, "failed", send)
    assert sent[-1] == "❌《週會》轉錄失敗：壞了"
    assert N.on_job_terminal(s, {"id": "a1", "kind": "ai", "meeting": "m1"}, "failed", send)
    assert sent[-1].startswith("✅")
    assert not N.on_job_terminal(s, {"id": "t1", "kind": "transcribe", "meeting": "m1"}, "cancelled", send)
