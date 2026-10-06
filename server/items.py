"""Item helpers (pure): ids, legacy normalisation, src cleanup, text extraction.

Item (data contract):  {id: "<8 hex>", text: str, src: [{t: float_seconds, quote: str}], group?: str}
Legacy summary entries are plain strings (or {q, a} dicts for old Q&A) -> normalised to Items.
Notes entries (outline / concepts / tables / figures / quiz objects) keep their own fields and
gain `id` and `src`; `item_text()` gives the flat text used for verification.
"""
from __future__ import annotations

import hashlib
import re

SUMMARY_KEYS_MEETING = ("actionItems", "mainPoints", "qa")
SUMMARY_KEYS_LECTURE = ("conclusions", "advice", "risks", "cases", "mainPoints", "qa")
NOTES_LIST_KEYS = ("outline", "concepts", "tables", "figures", "quiz")

_HEX8 = re.compile(r"^[0-9a-f]{8}$")
_NOTES_FIELDS = ("title", "points", "term", "label", "value", "headers", "rows", "q", "a")


def hash8(s: str) -> str:
    return hashlib.sha1(str(s).encode("utf-8")).hexdigest()[:8]


def parse_time(t) -> float | None:
    """Seconds from a number or a "SS" / "MM:SS" / "HH:MM:SS" string; None if unusable."""
    if isinstance(t, bool):
        return None
    if isinstance(t, (int, float)):
        return float(t) if t >= 0 else None
    if isinstance(t, str):
        s = t.strip().strip("[]")
        if not s:
            return None
        try:
            parts = [float(p) for p in s.split(":")]
        except ValueError:
            return None
        if len(parts) > 3 or any(p < 0 for p in parts):
            return None
        sec = 0.0
        for p in parts:
            sec = sec * 60 + p
        return sec
    return None


def normalize_src(src) -> list[dict]:
    """Keep only well-formed sources: {t: float_seconds, quote: str}.

    t may arrive as a number or "HH:MM:SS" and is coerced to seconds; an entry without a usable
    t keeps only its quote (verify can still locate it); entries with neither are dropped.
    """
    out = []
    for s in src if isinstance(src, list) else []:
        if not isinstance(s, dict):
            continue
        quote = str(s.get("quote") or "").strip()
        t = parse_time(s.get("t"))
        if t is not None:
            out.append({"t": t, "quote": quote})
        elif quote:
            out.append({"quote": quote})
    return out


def _qa_text(q, a) -> str:
    q, a = str(q or "").strip(), str(a or "").strip()
    return f"問：{q}\n答：{a}" if a else f"問：{q}"


def item_text(x) -> str:
    """Flat text of any summary/notes entry (legacy string, Item, or notes object)."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if not isinstance(x, dict):
        return str(x)
    if isinstance(x.get("text"), str):
        return x["text"]
    if "q" in x or "a" in x:  # quiz / legacy Q&A object
        return _qa_text(x.get("q"), x.get("a"))
    if "term" in x:  # concept
        parts = [str(x.get("term") or ""), str(x.get("plain") or ""), str(x.get("why") or "")]
        return "：".join(p for p in parts[:2] if p) + (f"（{parts[2]}）" if parts[2] else "")
    if "label" in x or "value" in x:  # figure
        return f"{x.get('label') or ''}：{x.get('value') or ''}"
    if "headers" in x or "rows" in x:  # table
        lines = [str(x.get("title") or "")]
        lines.append("｜".join(str(h) for h in x.get("headers") or []))
        for r in x.get("rows") or []:
            lines.append("｜".join(str(c) for c in (r if isinstance(r, list) else [r])))
        return "\n".join(line for line in lines if line)
    if "title" in x or "points" in x:  # outline section
        pts = [item_text(p) for p in x.get("points") or []]
        return "\n".join([str(x.get("title") or "")] + pts).strip()
    return ""


def normalize_item(x) -> dict:
    """Any summary entry -> Item {id, text, src[, group]}.

    str -> {id: sha1(text)[:8], text, src: []}; legacy {q, a} -> "問：…\\n答：…";
    an Item keeps a valid id, otherwise the id is derived from the text.
    """
    if isinstance(x, dict):
        text = item_text(x)
        it = {"id": x["id"] if isinstance(x.get("id"), str) and _HEX8.match(x["id"]) else hash8(text),
              "text": text, "src": normalize_src(x.get("src"))}
        g = x.get("group")
        if isinstance(g, str) and g.strip():
            it["group"] = g.strip()
        return it
    text = "" if x is None else str(x)
    return {"id": hash8(text), "text": text, "src": []}


def normalize_entry(x) -> dict:
    """Notes object (outline/concept/table/figure/quiz) -> same object + id + clean src.
    Plain strings / Items go through normalize_item."""
    if (isinstance(x, dict) and not isinstance(x.get("text"), str)
            and any(k in x for k in _NOTES_FIELDS)):
        out = dict(x)
        out["src"] = normalize_src(x.get("src"))
        if not (isinstance(x.get("id"), str) and _HEX8.match(x["id"])):
            out["id"] = hash8(item_text(x))
        return out
    return normalize_item(x)


def ensure_ids(items, *, notes: bool = False) -> list[dict]:
    """Normalise a list and make every id unique within it (collisions re-hashed with an index).

    notes=False: every entry becomes an Item (summary sections).
    notes=True : notes objects keep their fields (outline/concepts/tables/figures/quiz).
    """
    out, seen = [], set()
    for i, x in enumerate(items if isinstance(items, list) else []):
        it = normalize_entry(x) if notes else normalize_item(x)
        if it["id"] in seen:
            k = 1
            while True:
                nid = hash8(f"{item_text(it)}#{i}#{k}")
                if nid not in seen:
                    break
                k += 1
            it["id"] = nid
        seen.add(it["id"])
        out.append(it)
    return out


def iter_items(meeting: dict):
    """Yield (section, entry, itemId, text, src) for every summary / notes entry.

    section is "summary.<key>" / "notes.<key>". itemId matches ensure_ids() on the stored list
    (legacy strings get their normalize_item id), so review actions can find the entry again.
    """
    summ = meeting.get("summary") if isinstance(meeting.get("summary"), dict) else {}
    for key, lst in summ.items():
        if isinstance(lst, list):
            for x, it in zip(lst, ensure_ids(lst)):
                yield f"summary.{key}", x, it["id"], it["text"], it["src"]
    notes = meeting.get("notes") if isinstance(meeting.get("notes"), dict) else {}
    for key in NOTES_LIST_KEYS:
        lst = notes.get(key)
        if isinstance(lst, list):
            for x, it in zip(lst, ensure_ids(lst, notes=True)):
                yield f"notes.{key}", x, it["id"], item_text(it), it.get("src") or []
