#!/usr/bin/env python3
"""Import an old GitHub-backup JSON into the NAS server.

Usage: python tools/import_legacy.py <file.json> [--server http://127.0.0.1:8778]
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="匯入舊版備份 JSON 到會議紀錄伺服器")
    ap.add_argument("file")
    ap.add_argument("--server", default="http://127.0.0.1:8778")
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        with open(a.file, "rb") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"讀不到備份檔：{e}", file=sys.stderr)
        return 1
    req = urllib.request.Request(a.server.rstrip("/") + "/api/import",
                                 data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            rep = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"伺服器回應 {e.code}：{e.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"連不上伺服器：{e}", file=sys.stderr)
        return 1
    print(f"匯入完成：新增 {rep.get('added')}、更新 {rep.get('updated')}、保留 {rep.get('kept')}、分類 {rep.get('groups')}")
    print(json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
