"""完整整理 (full report) helpers — pure functions, no AI calls.

The AI returns {intro, points:[{heading,title,text,src}]}. We store it as two flat Item lists
(summary.reportIntro / summary.reportPoints) so verify / edit / jump-to-source keep working:
group = heading, text = "title：text".
"""
from __future__ import annotations

try:  # imported as server.report (app / ai.py) or as top-level `report` (tests run from server/)
    from .items import ensure_ids, item_text, normalize_src
except ImportError:
    from items import ensure_ids, item_text, normalize_src

# Same value as ai.MAX_SRC; defined locally so this pure module never imports ai.py (cycle).
MAX_SRC = 3
OTHER_HEADING = "其他"


def _s(x) -> str:
    return x.strip() if isinstance(x, str) else ""


def normalize_report(raw) -> dict:
    """AI output -> {"reportIntro": [Item], "reportPoints": [Item]} (empty text dropped)."""
    raw = raw if isinstance(raw, dict) else {}
    intro = raw.get("intro")
    intro_items = []
    if isinstance(intro, dict) and _s(intro.get("text")):
        intro_items = ensure_ids([{"text": _s(intro["text"]), "src": normalize_src(intro.get("src"))}])
    pts = []
    for p in raw.get("points") if isinstance(raw.get("points"), list) else []:
        if not isinstance(p, dict):
            continue
        # a 「：」 inside the title would make the later first-colon split cut the title short
        text, title = _s(p.get("text")), _s(p.get("title")).replace("：", "・")
        if not text:
            continue
        pts.append({"text": f"{title}：{text}" if title else text,
                    "group": _s(p.get("heading")) or OTHER_HEADING,
                    "src": normalize_src(p.get("src"))})
    return {"reportIntro": intro_items, "reportPoints": ensure_ids(pts)}


def merge_reports(parts) -> dict:
    """Merge normalize_report() results: first intro; points grouped by first-seen heading,
    same text de-duplicated with src merged (<= MAX_SRC). Pure code, never rewrites text."""
    parts = [p for p in parts or [] if isinstance(p, dict)]
    intro = []
    for p in parts:
        if p.get("reportIntro"):
            intro = list(p["reportIntro"])[:1]
            break
    order: list[str] = []
    by_group: dict[str, list[dict]] = {}
    seen: dict[str, dict] = {}
    for p in parts:
        for it in p.get("reportPoints") or []:
            g = it.get("group") or OTHER_HEADING
            text = item_text(it)
            key = text
            if key in seen:
                have = seen[key]["src"]
                for s in it.get("src") or []:
                    if len(have) >= MAX_SRC:
                        break
                    if s not in have:
                        have.append(s)
                continue
            new = {"text": text, "group": g, "src": list(it.get("src") or [])[:MAX_SRC]}
            seen[key] = new
            if g not in by_group:
                order.append(g)
                by_group[g] = []
            by_group[g].append(new)
    flat = [x for g in order for x in by_group[g]]
    return {"reportIntro": intro, "reportPoints": ensure_ids(flat)}


def has_report(summary) -> bool:
    """A meeting counts as having a full report when reportPoints has at least one entry."""
    pts = summary.get("reportPoints") if isinstance(summary, dict) else None
    return isinstance(pts, list) and len(pts) > 0
