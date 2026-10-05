import json

from server.importer import import_dir, merge_legacy
from server.storage import Store


def legacy(mid="L1", **kw):
    m = {"id": mid, "title": "舊會議", "createdAt": 1600000000000, "updatedAt": 1600000005000,
         "group": "g1", "durationSec": 99,
         "transcript": [{"speaker": "A", "text": "你好", "t": "01:02"},
                        {"speaker": "B", "text": "嗨", "fi": 1}, {"speaker": "A", "text": "x", "t": 3725}],
         "summary": {"mainPoints": ["p"]}, "chat": [{"at": 1, "q": "q1", "a": "a1"}],
         "notes": {"a": 1}, "custom": "keep-me"}
    m.update(kw)
    return m


def backup(*ms, **kw):
    d = {"meetings": list(ms), "deleted": [], "deletedAt": {}, "groups": [{"id": "g1", "name": "工作", "color": "#fff"}],
         "groupsDeleted": [], "groupsDeletedAt": {}}
    d.update(kw)
    return d


def test_import_legacy_backup_keeps_everything(tmp_path):
    s = Store(tmp_path)
    s.save({"id": "local1", "title": "本機", "createdAt": 5, "status": "done", "transcript": []})
    rep = merge_legacy(s, backup(legacy()))
    assert rep == {"added": 1, "updated": 0, "kept": 0, "groups": 1}
    m = s.get("L1")
    assert m["status"] == "done" and m["custom"] == "keep-me" and m["updatedAt"] == 1600000005000
    assert [x.get("t") for x in m["transcript"]] == ["01:02", None, 3725] and m["transcript"][1]["fi"] == 1
    assert s.get("local1")["title"] == "本機"
    assert s.get_groups()[0]["name"] == "工作"
    assert any(x["id"] == "L1" for x in json.loads((tmp_path / "index.json").read_text("utf-8")))


def test_import_ignores_deleted_list(tmp_path):
    s = Store(tmp_path)
    s.save({"id": "keep", "title": "k", "createdAt": 1, "status": "done"})
    merge_legacy(s, backup(legacy(), deleted=["keep", "L1"], deletedAt={"keep": 9}, groupsDeleted=["g1"]))
    assert s.get("keep") is not None and s.get("L1") is not None
    assert [g["id"] for g in s.get_groups()] == ["g1"]


def test_merge_newer_wins_unions_chat_keeps_server_fields(tmp_path):
    s = Store(tmp_path)
    s.save({"id": "L1", "title": "本機舊", "createdAt": 1, "updatedAt": 100, "status": "summarizing",
            "jobId": "j", "audio": [{"file": "0-a.m4a"}], "chat": [{"at": 2, "q": "q2"}],
            "alt": {"x": 1}}, touch=False)
    rep = merge_legacy(s, backup(legacy(updatedAt=200)))
    assert rep["updated"] == 1
    m = s.get("L1")
    assert m["title"] == "舊會議" and m["status"] == "summarizing" and m["jobId"] == "j" and m["audio"]
    assert [c["at"] for c in m["chat"]] == [1, 2] and m["alt"] == {"x": 1} and m["notes"] == {"a": 1}
    # older incoming: local kept
    rep = merge_legacy(s, backup(legacy(updatedAt=50, title="更舊")))
    assert rep["kept"] == 1 and s.get("L1")["title"] == "舊會議"


def test_export_roundtrip(client):
    s = client.app.state.store
    merge_legacy(s, backup(legacy("A1"), legacy("A2", title="第二")))
    exp = client.get("/api/export").json()
    assert exp["deleted"] == [] and exp["exportedAt"] and len(exp["meetings"]) == 2
    assert exp["groups"][0]["id"] == "g1"
    assert client.post("/api/import", json=exp).json() == {"added": 0, "updated": 0, "kept": 2, "groups": 0}
    assert client.post("/api/import", json={"x": 1}).status_code == 400


def test_import_dir_moves_done(tmp_path):
    s = Store(tmp_path)
    (tmp_path / "import").mkdir()
    (tmp_path / "import" / "b.json").write_text(json.dumps(backup(legacy())), "utf-8")
    (tmp_path / "import" / "bad.json").write_text("{not json", "utf-8")
    assert import_dir(s) == ["b.json"]
    assert s.get("L1") is not None
    assert (tmp_path / "import" / "done" / "b.json").exists()
    assert not (tmp_path / "import" / "b.json").exists()
    assert (tmp_path / "import" / "bad.json").exists()  # untouched
    assert import_dir(s) == []
