import os
import tarfile
import time
from datetime import datetime

from server.backup import TZ, prune, run_backup, seconds_until_next
from server.main import restart_requested
from server.storage import Store


def ts(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp()


def test_backup_contents_exclude_audio(tmp_path):
    s = Store(tmp_path / "data")
    s.save({"id": "m1", "title": "t", "createdAt": 1, "status": "done"})
    s.set_groups([{"id": "g"}])
    (s.path("audio") / "m1").mkdir()
    (s.path("audio") / "m1" / "0-a.m4a").write_bytes(b"x" * 10)
    out = run_backup(s, tmp_path / "bk", now=ts(2026, 10, 6, 3, 40))
    assert out.name == "meetings-20261006-0340.tar.gz"
    names = tarfile.open(out).getnames()
    assert "meetings/m1.json" in names and "groups.json" in names and "index.json" in names
    assert not any(n.startswith("audio") or "m4a" in n for n in names)
    assert not list((tmp_path / "bk").glob("*.tmp"))


def test_backup_keeps_newest_14(tmp_path):
    s = Store(tmp_path / "data")
    for d in range(1, 21):
        run_backup(s, tmp_path / "bk", now=ts(2026, 9, d, 3, 40))
    left = sorted(p.name for p in (tmp_path / "bk").glob("*.tar.gz"))
    assert len(left) == 14 and left[0] == "meetings-20260907-0340.tar.gz" and left[-1] == "meetings-20260920-0340.tar.gz"
    assert prune(tmp_path / "bk") == []


def test_seconds_until_next_taipei_0340():
    assert seconds_until_next(ts(2026, 10, 6, 3, 0)) == 40 * 60
    assert seconds_until_next(ts(2026, 10, 6, 3, 40)) == 24 * 3600
    assert seconds_until_next(ts(2026, 10, 6, 23, 40)) == 4 * 3600


def test_restart_requested_decision(tmp_path):
    f = tmp_path / ".restart"
    start = time.time()
    assert restart_requested(f, start) is False  # missing
    f.write_text("")
    os.utime(f, (start - 100, start - 100))
    assert restart_requested(f, start) is False  # stale marker from before this boot
    os.utime(f, (start + 1, start + 1))
    assert restart_requested(f, start) is True
