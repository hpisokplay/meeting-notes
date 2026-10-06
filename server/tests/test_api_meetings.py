import json
import time


def test_api_list_get_put_delete(client, meeting):
    mid = meeting["id"]
    r = client.put(f"/api/meetings/{mid}", json=meeting)
    assert r.status_code == 200 and r.json()["updatedAt"] > 1700000000000
    lst = client.get("/api/meetings").json()
    assert [m["id"] for m in lst] == [mid] and "transcript" not in lst[0]
    full = client.get(f"/api/meetings/{mid}").json()
    assert len(full["transcript"]) == 3
    assert client.get("/api/meetings/zzz").status_code == 404
    assert client.delete(f"/api/meetings/{mid}").status_code == 200
    assert client.get(f"/api/meetings/{mid}").status_code == 404
    assert client.get("/api/meetings").json() == []
    assert client.delete(f"/api/meetings/{mid}").status_code == 404


def test_put_rejects_id_mismatch(client, meeting):
    assert client.put("/api/meetings/other", json=meeting).status_code == 400


def test_api_groups_roundtrip(client):
    assert client.get("/api/groups").json() == []
    g = [{"id": "g1", "name": "客戶", "color": "#123456"}]
    assert client.put("/api/groups", json=g).status_code == 200
    assert client.get("/api/groups").json() == g


def test_settings_roundtrip(client):
    assert client.get("/api/settings").json()["claudeModel"] == "opus"  # new default (2026-10-06)
    client.put("/api/settings", json={"claudeModel": "sonnet"})
    assert client.get("/api/settings").json()["claudeModel"] == "sonnet"


def test_status_shape(client, data_dir):
    s = client.get("/api/status").json()
    assert s["version"] == "v100" and s["worker"]["online"] is False
    assert s["queue"] == 0 and s["claudeModel"] == "opus"
    assert isinstance(s["diskFreeGB"], (int, float))
    (data_dir / "jobs" / "queue").mkdir(parents=True)
    (data_dir / "jobs" / "worker.json").write_text(json.dumps({"lastSeen": time.time()}))
    (data_dir / "jobs" / "queue" / "a.json").write_text("{}")
    s = client.get("/api/status").json()
    assert s["worker"]["online"] is True and s["queue"] == 1
    (data_dir / "jobs" / "worker.json").write_text(json.dumps({"lastSeen": time.time() - 120}))
    assert client.get("/api/status").json()["worker"]["online"] is False


def test_static_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "DD-INDEX" in r.text
    assert "create_app" not in client.get("/%2e%2e/server/app.py").text
    assert client.get("/api/nope").status_code == 404


def test_static_allowlist(client, static_dir):
    (static_dir / "js").mkdir()
    (static_dir / "js" / "app.js").write_text("//x")
    (static_dir / "manifest.webmanifest").write_text("{}")
    (static_dir / ".git").mkdir()
    (static_dir / ".git" / "config").write_text("secret")
    (static_dir / "server").mkdir()
    (static_dir / "server" / "app.py").write_text("secret")
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/manifest.webmanifest").status_code == 200
    for p in ("/.git/config", "/server/app.py", "/docs/x", "/.superpowers/x", "/js/../server/app.py"):
        assert client.get(p).status_code == 404, p


def test_corrupt_worker_json_and_bad_ids(client, data_dir):
    (data_dir / "jobs").mkdir(parents=True)
    (data_dir / "jobs" / "worker.json").write_text("{half")
    assert client.get("/api/status").json()["worker"]["online"] is False
    assert client.get("/api/meetings/.hidden").status_code == 400
    assert client.delete("/api/meetings/.hidden").status_code == 400
    assert client.put("/api/meetings/.hidden", json={"id": ".hidden"}).status_code == 400
    assert client.get("/api/meetings/.hidden/audio/0").status_code == 400
    (data_dir / "meetings" / "bad.json").write_text("{half")
    assert client.get("/api/meetings").status_code == 200


def test_static_encoded_traversal_blocked(client, static_dir):
    (static_dir / "js").mkdir(exist_ok=True)
    (static_dir / "js" / "app.js").write_text("//x")
    (static_dir / "css").mkdir(exist_ok=True)
    (static_dir / "server").mkdir(exist_ok=True)
    (static_dir / "server" / "app.py").write_text("SECRET_SRC")
    (static_dir / ".git").mkdir(exist_ok=True)
    (static_dir / ".git" / "config").write_text("SECRET_GIT")
    for p in ("/js/%2e%2e/server/app.py", "/js/..%2fserver/app.py",
              "/js/%2e%2e/.git/config", "/css/..%5cserver%5capp.py",
              "/js/%252e%252e/server/app.py", "/js/%2e%2e%2fserver%2fapp.py"):
        r = client.get(p)
        assert r.status_code == 404, p
        assert "SECRET" not in r.text, p
    assert client.get("/js/app.js").status_code == 200
