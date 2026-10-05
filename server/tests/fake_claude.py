"""Stand-in for `claude -p` used by the tests.

Mode comes from env FAKE_CLAUDE_MODE:
  auto (default)  structured_output chosen from the --json-schema properties
  limit           exit 1, is_error, "usage limit reached"
  fail            exit 1, generic error
  env             structured_output {"env": [names of env vars]}
  result_only     no structured_output; JSON in `result` (inside a ```json fence)
  sleep           sleep 60 s (timeout / cancel tests)
  custom          FAKE_CLAUDE_STDOUT / FAKE_CLAUDE_STDERR / FAKE_CLAUDE_RC printed verbatim
In auto mode, terms marked 《詞|cat》 in stdin are returned as the term-scan items, and
FAKE_CLAUDE_TR_DROP=n (+ FAKE_CLAUDE_COUNTER=<file>) drops a segment in the first n
transcript translation calls.
FAKE_CLAUDE_LOG=<path> appends {"argv": [...], "stdin": "..."} as one JSON line per call.
"""
import json
import os
import re
import sys
import time


def arg(name):
    a = sys.argv[1:]
    return a[a.index(name) + 1] if name in a else None


def prefix_strings(x, p):
    if isinstance(x, str):
        return p + x
    if isinstance(x, list):
        return [prefix_strings(v, p) for v in x]
    if isinstance(x, dict):
        return {k: prefix_strings(v, p) for k, v in x.items()}
    return x


def bump_counter():
    """FAKE_CLAUDE_COUNTER=<file>: returns how many times this was called before."""
    p = os.environ.get("FAKE_CLAUDE_COUNTER")
    if not p:
        return 0
    n = int(open(p).read() or 0) if os.path.exists(p) else 0
    with open(p, "w") as f:
        f.write(str(n + 1))
    return n


def auto(schema, system, stdin):
    props = set((schema.get("properties") or {}).keys())
    if "translator" in system:
        out = prefix_strings(json.loads(stdin), "[譯]")
        # FAKE_CLAUDE_TR_DROP=n: the first n segment batches come back one segment short
        if "segments" in out and bump_counter() < int(os.environ.get("FAKE_CLAUDE_TR_DROP", "0")):
            out["segments"] = out["segments"][:-1]
        return out
    marked = re.findall(r"《([^|》]+)\|([a-z]+)》", stdin)
    if props == {"items"} and marked and "term" in schema["properties"]["items"]["items"].get("properties", {}):
        return {"items": [{"term": t, "category": c, "fix": ""} for t, c in marked]}
    if props == {"actionItems", "mainPoints", "qa"}:
        return {"actionItems": ["新待辦 [DRI: 待指派]"], "mainPoints": ["新重點：說明"],
                "qa": ["問：新問題？ 答：新答案。"]}
    if props == {"answer"}:
        return {"answer": "會議中沒有提到"}
    if props == {"groups"}:
        return {"groups": [{"terms": ["台積店", "台積殿"], "best": "台積電"}]}
    if props == {"items"}:
        it = schema["properties"]["items"]["items"]
        if it.get("type") == "string":
            return {"items": ["加強一", "加強二"]}
        ip = set(it.get("properties", {}))
        if "src" in ip:
            n = len(re.findall(r"^\d+\. ", stdin, re.M))
            return {"items": [{"text": f"潤飾{i}", "src": [i]} for i in range(1, n + 1)]}
        if "term" in ip:
            return {"items": [{"term": "台積店", "category": "org", "fix": "台積電"},
                              {"term": "Jefry", "category": "person", "fix": "Jeffrey"},
                              {"term": "台積殿", "category": "org", "fix": ""}]}
    notes = {"outline": [{"title": "第一節主題", "anchor": "第一句", "points": ["要點"]}],
             "concepts": [{"term": "概念", "plain": "白話", "why": "原因"}],
             "tables": [{"title": "表", "headers": ["A", "B"], "rows": [["1", "2"]]}],
             "figures": [{"group": "市場規模", "label": "規模", "value": "10 億"}],
             "quiz": [{"q": "題目？", "a": "答案"}]}
    if props and props <= set(notes):
        return {k: notes[k] for k in props}
    return {"unknown_schema": sorted(props)}


def main():
    stdin = sys.stdin.buffer.read().decode("utf-8")
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": sys.argv[1:], "stdin": stdin}, ensure_ascii=False) + "\n")
    mode = os.environ.get("FAKE_CLAUDE_MODE", "auto")
    out = sys.stdout.buffer
    if mode == "sleep":
        time.sleep(60)
        return 0
    if mode == "limit":
        out.write(json.dumps({"type": "result", "is_error": True,
                              "result": "Claude AI usage limit reached|1760000000"}).encode())
        return 1
    if mode == "fail":
        sys.stderr.write("boom: something broke\n")
        return 2
    if mode == "custom":  # FAKE_CLAUDE_STDOUT / FAKE_CLAUDE_STDERR / FAKE_CLAUDE_RC verbatim
        out.write(os.environ.get("FAKE_CLAUDE_STDOUT", "").encode("utf-8"))
        sys.stderr.write(os.environ.get("FAKE_CLAUDE_STDERR", ""))
        return int(os.environ.get("FAKE_CLAUDE_RC", "1"))
    if mode == "env":
        res = {"env": sorted(os.environ.keys())}
    else:
        res = auto(json.loads(arg("--json-schema") or "{}"), arg("--system-prompt") or "", stdin)
    if mode == "result_only":
        body = {"type": "result", "is_error": False,
                "result": "```json\n" + json.dumps(res, ensure_ascii=False) + "\n```"}
    else:
        body = {"type": "result", "is_error": False, "result": "", "structured_output": res}
    out.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
