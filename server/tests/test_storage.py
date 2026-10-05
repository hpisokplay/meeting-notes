import json

from server.render import meeting_to_docx, meeting_to_markdown
from server.storage import Store


def test_save_get_roundtrip(data_dir, meeting):
    s = Store(data_dir)
    saved = s.save(meeting)
    assert isinstance(saved["updatedAt"], int) and saved["updatedAt"] > 1700000000000
    got = s.get(meeting["id"])
    assert got["title"] == "測試會議"
    assert got["transcript"][1] == {"speaker": "B", "text": "第二句", "fi": 1}
    assert got["transcript"][0]["t"] == "01:02"
    assert s.get("nope") is None
    assert (data_dir / "meetings" / f"{meeting['id']}.md").exists()
    assert (data_dir / "exports" / f"{meeting['id']}.docx").exists()


def test_atomic_write_leaves_no_tmp(data_dir, meeting):
    s = Store(data_dir)
    s.save(meeting)
    s.set_groups([{"id": "g1", "name": "A", "color": "#fff"}])
    s.set_settings({"claudeModel": "opus"})
    assert [p for p in data_dir.rglob("*") if p.name.endswith(".tmp")] == []


def test_trash_moves_file_never_deletes(data_dir, meeting):
    s = Store(data_dir)
    s.save(meeting)
    s.trash(meeting["id"])
    assert s.get(meeting["id"]) is None
    assert (data_dir / "trash" / f"{meeting['id']}.json").exists()
    assert not (data_dir / "meetings" / f"{meeting['id']}.json").exists()
    assert s.list_meetings() == []
    # trashing the same id again keeps both copies
    s.save(meeting)
    s.trash(meeting["id"])
    assert len(list((data_dir / "trash").glob("*.json"))) == 2


def test_index_json_lists_meetings(data_dir, meeting):
    s = Store(data_dir)
    s.save(meeting)
    idx = json.loads((data_dir / "index.json").read_text(encoding="utf-8"))
    assert idx[0]["id"] == meeting["id"]
    assert idx[0]["title"] == "測試會議" and idx[0]["group"] == "g1"
    assert "transcript" not in idx[0]
    lst = s.list_meetings()
    assert "transcript" not in lst[0]
    assert {"id", "title", "createdAt", "updatedAt", "group", "durationSec",
            "status", "progress"} <= set(lst[0])


def test_update_locked_rmw(data_dir, meeting):
    s = Store(data_dir)
    s.save(meeting)
    out = s.update(meeting["id"], lambda m: m.update(title="新標題"))
    assert out["title"] == "新標題" and s.get(meeting["id"])["title"] == "新標題"


def test_settings_defaults_and_new_id(data_dir):
    s = Store(data_dir)
    assert s.get_settings() == {"claudeModel": "sonnet", "fallbackEnabled": True,
                                "fallbackMinutes": 20, "tgNotify": True}
    s.set_settings({"claudeModel": "opus"})
    assert s.get_settings()["claudeModel"] == "opus"
    assert s.get_settings()["fallbackMinutes"] == 20
    a, b = s.new_id(), s.new_id()
    assert a != b and len(a) == 17 and s.get_groups() == []


def test_render_md_legacy_timestamps(meeting, tmp_path):
    md = meeting_to_markdown(meeting)
    assert md.startswith("# 測試會議")
    assert "第一句" in md and "第二句" in md
    assert "日期：2023-11-15 06:13（台北）" in md
    for h in ("## 待辦", "## 重點", "## Q&A", "## 逐字稿"):
        assert h in md
    assert "\n[01:02:05] A: 第三句" in md  # 3725 s numeric
    assert "\nA: 第一句" in md              # legacy "MM:SS" string: time omitted
    assert "\nB: 第二句" in md              # no t at all (fi only)
    p = tmp_path / "x.docx"
    meeting_to_docx(meeting, p)
    assert p.stat().st_size > 1000


def test_save_survives_xml_invalid_chars(data_dir, meeting):
    s = Store(data_dir)
    meeting["transcript"][0]["text"] = "壞\x0b字\x01元"
    meeting["title"] = "標\x0c題"
    s.save(meeting)
    assert s.get(meeting["id"])["transcript"][0]["text"] == "壞\x0b字\x01元"
    assert (data_dir / "exports" / f"{meeting['id']}.docx").exists()
    idx = json.loads((data_dir / "index.json").read_text(encoding="utf-8"))
    assert idx[0]["id"] == meeting["id"]
    s.update(meeting["id"], lambda m: m.update(group="g2"))


def test_derived_failure_never_blocks_json_save(data_dir, meeting, monkeypatch):
    import server.storage as st
    def boom(*a, **k):
        raise RuntimeError("render broke")
    monkeypatch.setattr(st, "meeting_to_docx", boom)
    monkeypatch.setattr(st, "meeting_to_markdown", boom)
    s = Store(data_dir)
    s.save(meeting)
    assert s.get(meeting["id"])["title"] == "測試會議"
    assert json.loads((data_dir / "index.json").read_text(encoding="utf-8"))[0]["id"] == meeting["id"]


def test_corrupt_json_skipped(data_dir, meeting):
    s = Store(data_dir)
    s.save(meeting)
    (data_dir / "meetings" / "bad.json").write_text("{half", encoding="utf-8")
    assert [m["id"] for m in s.list_meetings()] == [meeting["id"]]
    assert s.get("bad") is None
    s.save({**meeting, "id": "other1"})  # index rebuild tolerates the bad file
    assert len(json.loads((data_dir / "index.json").read_text(encoding="utf-8"))) == 2
