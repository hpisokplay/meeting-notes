"""run_claude must hand the whole stdin to a child that starts reading late.

Real failure (2026-10-14 .. 2026-11-10, every meeting): on the NAS a pipe holds only 8 KB and a
cold-started `claude` takes more than a second before it reads stdin. The 1-second
communicate(input=..., timeout=...) retry loop stopped writing after its first timeout (a retry
with input=None never writes the rest nor closes stdin), so the child waited for end-of-input
until the 30-minute timeout: 「名詞校正失敗，已略過：Claude 逾時（超過 30 分鐘）」.
Red on Linux with the old loop; Windows writes stdin differently and never showed it."""
import json
import sys

from server.ai import run_claude

SLOW_READER = (
    "import json, sys, time\n"
    "time.sleep(2.5)\n"  # longer than ai.ABORT_CHECK_S: the first communicate() times out
    "d = sys.stdin.buffer.read()\n"
    "print(json.dumps({'structured_output': {'n': len(d), 'tail': d[-3:].decode()}}))\n"
)


def test_late_reading_child_gets_all_of_stdin(tmp_path):
    child = tmp_path / "slow_reader.py"
    child.write_text(SLOW_READER, encoding="utf-8")
    user = "逐字稿" * 100_000 + "END"  # ~900 KB: far beyond any pipe buffer
    r = run_claude("sys", user, {"type": "object"}, "opus", [sys.executable, str(child)], timeout=30)
    assert r == {"n": len(user.encode("utf-8")), "tail": "END"}
