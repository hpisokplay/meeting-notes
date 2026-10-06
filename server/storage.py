"""File-based storage for meetings. Every write is atomic (.tmp + os.replace)."""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from .gaps import compute_gaps
from .render import meeting_to_docx, meeting_to_markdown

log = logging.getLogger(__name__)

NL = chr(10)
# Domain glossary (轉錄詞庫): Whisper hotwords at ASR time, reference for CORRECTION, name source
# for verify. settings.glossary is a newline-separated string; this is its default.
DEFAULT_GLOSSARY = [
    # 稅務
    "股利", "股利所得", "分離課稅", "綜合所得稅", "最低稅負", "基本所得額", "贈與稅", "遺產稅",
    "房地合一", "實質課稅", "實質課稅原則", "納保法", "境外資金匯回專法", "受控外國公司", "CFC",
    "實際管理處所", "PEM", "稅務居民", "非稅務居民", "183天", "海外所得", "營利事業所得稅",
    "未分配盈餘", "全球最低稅負制", "國稅局", "財政部", "賦稅署", "會計師",
    # 財富管理／金融
    "複委託", "ETF", "OBU", "DBU", "CRS", "共同申報準則", "BVI", "英屬維京群島", "開曼", "開曼群島",
    "境外公司", "控股公司", "信託", "保險", "保單", "家族辦公室", "亞洲資產管理中心", "美股", "台股",
    "美元", "新台幣", "債券", "基金",
    # 半導體／光通訊
    "輝達", "台積電", "日月光", "晶圓", "先進封裝", "CoWoS", "400G", "800G", "1.6T", "CPO", "NPO",
    "Pluggable", "光模組", "矽光子", "Turnkey", "Pilot line", "無塵室", "良率", "產能",
]
DEFAULT_SETTINGS = {"claudeModel": "opus", "fallbackEnabled": True,
                    "fallbackMinutes": 20, "tgNotify": True,
                    "glossary": NL.join(DEFAULT_GLOSSARY)}
OLD_DEFAULT_MODEL = "sonnet"
LIST_FIELDS = ("id", "title", "createdAt", "updatedAt", "group", "durationSec", "status", "progress")


class BadId(ValueError):
    """Meeting id failed validation."""


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{secrets.token_hex(3)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.error("corrupt json skipped: %s (%s)", path, e)
        return default


PENDING_MD_LINE = "⚠️ 尚有 {n} 項待確認，內容未經確認，請勿轉交"


def pending_review_count(m: dict) -> int:
    """Pending items of meeting.review (the export gate)."""
    rv = m.get("review") if isinstance(m, dict) else None
    items = rv.get("items") if isinstance(rv, dict) else None
    return sum(1 for r in items or [] if isinstance(r, dict) and r.get("state") == "pending")


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        for d in ("meetings", "exports", "trash", "audio", "uploads", "inbox"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._index_lock = threading.Lock()

    # ---- helpers
    def path(self, *parts) -> Path:
        return self.root.joinpath(*parts)

    def _lock(self, mid: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(mid, threading.RLock())

    def new_id(self) -> str:
        return f"{int(time.time() * 1000)}{secrets.token_hex(2)}"

    def _mpath(self, mid: str) -> Path:
        if not mid or "/" in mid or "\\" in mid or mid.startswith("."):
            raise BadId("bad meeting id")
        return self.path("meetings", f"{mid}.json")

    # ---- meetings
    def get(self, mid: str) -> dict | None:
        return read_json(self._mpath(mid))  # ValueError on a bad id

    def get_settled(self, mid: str) -> dict | None:
        """get() after any update() in progress on this meeting has been saved."""
        with self._lock(mid):
            return self.get(mid)

    def _all(self) -> list[dict]:
        out = []
        for p in self.path("meetings").glob("*.json"):
            m = read_json(p)
            if isinstance(m, dict):
                out.append(m)
        out.sort(key=lambda m: m.get("createdAt") or 0, reverse=True)
        return out

    def list_meetings(self) -> list[dict]:
        return [{k: m.get(k) for k in LIST_FIELDS} for m in self._all()]

    def _write_index(self) -> None:
        with self._index_lock:
            idx = [{"id": m.get("id"), "title": m.get("title"), "createdAt": m.get("createdAt"),
                    "group": m.get("group"), "durationSec": m.get("durationSec"),
                    "status": m.get("status")} for m in self._all()]
            atomic_write_json(self.path("index.json"), idx)

    def save(self, m: dict, touch: bool = True) -> dict:
        mid = m["id"]
        with self._lock(mid):
            # every transcript save recomputes the unrecognised stretches (word-review spec)
            if isinstance(m.get("transcript"), list) and m["transcript"]:
                m["gaps"] = compute_gaps(m["transcript"], m.get("durationSec"))
            else:
                m.pop("gaps", None)
            if touch or not m.get("updatedAt"):
                m["updatedAt"] = int(time.time() * 1000)
            atomic_write_json(self._mpath(mid), m)
            self._write_derived(mid, m)
        try:
            self._write_index()
        except Exception:
            log.exception("index.json rebuild failed")
        return m

    def _write_derived(self, mid: str, m: dict) -> None:
        """md / docx are best-effort: the json is already safely saved.

        Export gate (same as the web page): while the 待確認清單 has pending items there is NO
        exports/<id>.docx (a stale one is removed: derived, regenerated once all are handled) and
        the md starts with a do-not-forward warning line.
        """
        n = pending_review_count(m)
        try:
            md = meeting_to_markdown(m)
            if n:
                md = PENDING_MD_LINE.format(n=n) + "\n\n" + md
            atomic_write_bytes(self.path("meetings", f"{mid}.md"), md.encode("utf-8"))
        except Exception:
            log.exception("markdown render failed for %s", mid)
        if n:
            try:
                self.path("exports", f"{mid}.docx").unlink(missing_ok=True)
            except OSError:
                log.exception("stale docx removal failed for %s", mid)
            return
        docx_tmp = self.path("exports", f"{mid}.docx.{secrets.token_hex(3)}.tmp")
        try:
            docx_tmp.parent.mkdir(parents=True, exist_ok=True)
            meeting_to_docx(m, docx_tmp)
            os.replace(docx_tmp, self.path("exports", f"{mid}.docx"))
        except Exception:
            log.exception("docx render failed for %s", mid)
        finally:
            docx_tmp.unlink(missing_ok=True)

    def update(self, mid: str, fn: Callable[[dict], None]) -> dict:
        with self._lock(mid):
            m = self.get(mid)
            if m is None:
                raise KeyError(mid)
            fn(m)
            return self.save(m)

    def trash(self, mid: str) -> None:
        with self._lock(mid):
            src = self._mpath(mid)
            if not src.exists():
                raise KeyError(mid)
            dst = self.path("trash", f"{mid}.json")
            if dst.exists():
                dst = self.path("trash", f"{mid}.{int(time.time() * 1000)}.json")
            shutil.move(str(src), str(dst))
            # derived files are regenerated from the json, so only these two are dropped
            for extra in (self.path("meetings", f"{mid}.md"), self.path("exports", f"{mid}.docx")):
                extra.unlink(missing_ok=True)
        self._write_index()

    # ---- groups / settings
    def get_groups(self) -> list:
        return read_json(self.path("groups.json"), [])

    def set_groups(self, groups: list) -> None:
        atomic_write_json(self.path("groups.json"), groups)

    def get_settings(self) -> dict:
        """Defaults + saved settings. One-time migration: a saved claudeModel still equal to the old
        default "sonnet" becomes "opus" (flag modelMigrated, so a later explicit choice sticks)."""
        p = self.path("settings.json")
        raw = read_json(p, {}) or {}
        if not raw.get("modelMigrated"):
            if raw.get("claudeModel") in (None, OLD_DEFAULT_MODEL):
                raw.pop("claudeModel", None)
            raw["modelMigrated"] = True
            if p.exists():
                try:
                    atomic_write_json(p, raw)
                except OSError:
                    log.exception("settings migration write failed")
        s = dict(DEFAULT_SETTINGS)
        s.update(raw)
        if not isinstance(s.get("glossary"), str):
            g = s.get("glossary")
            s["glossary"] = NL.join(str(x) for x in g) if isinstance(g, list) else DEFAULT_SETTINGS["glossary"]
        return s

    def set_settings(self, patch: dict) -> dict:
        s = self.get_settings()
        patch = {k: v for k, v in patch.items() if k in DEFAULT_SETTINGS}
        if "glossary" in patch:
            g = patch["glossary"]
            if isinstance(g, list):
                g = NL.join(str(x) for x in g)
            if not isinstance(g, str):
                patch.pop("glossary")
            else:
                patch["glossary"] = NL.join(x.strip() for x in g.splitlines() if x.strip())
        s.update(patch)
        atomic_write_json(self.path("settings.json"), s)
        return s

    def glossary_list(self) -> list[str]:
        """settings.glossary as a list (one term per line, blanks dropped)."""
        g = self.get_settings().get("glossary")
        return [x.strip() for x in (g or "").splitlines() if x.strip()]
