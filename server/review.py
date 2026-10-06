"""待確認清單 / corrections / reprocess plumbing (2026-10-06 chairman-quality spec).

- Correction helpers: apply ASR "wrong -> right" pairs to the transcript (kept raw in transcriptRaw).
- verify wrapper: verify.verify_meeting + review-state carry-over (incl. user-edited items).
- Review actions (confirm / edit / delete) on summary / notes Items and on term / slip items.
- HTTP: POST /meetings/{id}/review/{rid}, /mode, /restore-raw; start_reprocess() shared with /ai.
"""
from __future__ import annotations

import copy
import re
import time

from fastapi import APIRouter, Body, HTTPException

from .items import NOTES_LIST_KEYS, ensure_ids, item_text, normalize_src
from .storage import BadId, Store
from .verify import TranscriptIndex, merge_review, review_item, verify_meeting

MODES = ("lecture", "meeting")
MAX_TS = 5
ACTIVE_JOB_STATES = ("queued", "running", "claimed")


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- corrections
def _term_re(wrong: str) -> re.Pattern:
    """Latin / digit edges get word boundaries (CIS must not hit CISCO); CJK is plain substring."""
    pre = r"(?<![A-Za-z0-9])" if re.match(r"[A-Za-z0-9]", wrong) else ""
    post = r"(?![A-Za-z0-9])" if re.search(r"[A-Za-z0-9]$", wrong) else ""
    return re.compile(pre + re.escape(wrong) + post)


def count_term(transcript, wrong: str) -> tuple[int, list[float]]:
    """Occurrences of `wrong` in the transcript text and the segment times (first MAX_TS)."""
    if not wrong:
        return 0, []
    rx = _term_re(wrong)
    n, ts = 0, []
    for s in transcript or []:
        if not isinstance(s, dict):
            continue
        k = len(rx.findall(str(s.get("text") or "")))
        if k:
            n += k
            t = s.get("t")
            if isinstance(t, (int, float)) and not isinstance(t, bool) and len(ts) < MAX_TS:
                ts.append(float(t))
    return n, ts


def replace_term(transcript, wrong: str, right: str) -> tuple[list, int, list[float]]:
    """New transcript with every `wrong` -> `right` (segment texts only), count, segment times."""
    if not wrong or wrong == right:
        return transcript, 0, []
    rx = _term_re(wrong)
    out, n, ts = [], 0, []
    for s in transcript or []:
        if isinstance(s, dict) and isinstance(s.get("text"), str):
            new, k = rx.subn(lambda _m: right, s["text"])
            if k:
                s = {**s, "text": new}
                n += k
                t = s.get("t")
                if isinstance(t, (int, float)) and not isinstance(t, bool) and len(ts) < MAX_TS:
                    ts.append(float(t))
        out.append(s)
    return out, n, ts


_DIGITS = re.compile(r"\d")


def merge_corrections(old, proposals: list, transcript) -> tuple[list, dict]:
    """Fold CORRECTION output into meeting.corrections and apply the new high-confidence ones.

    - A (wrong, right) pair already known keeps its state (rejected stays rejected, a pending one is
      never auto-applied: a restore-raw or a user decision must not be undone by a re-run).
    - New high -> applied to the transcript (if it occurs); new medium / low -> pending.
    - A pair whose digits differ is never auto-applied (numbers need a human).
    Returns (new transcript, corrections dict).
    """
    items = [dict(c) for c in (old.get("items") if isinstance(old, dict) else None) or [] if isinstance(c, dict)]
    by_key = {(c.get("wrong"), c.get("right")): c for c in items}
    for p in proposals or []:
        if not isinstance(p, dict):
            continue
        wrong, right = str(p.get("wrong") or "").strip(), str(p.get("right") or "").strip()
        if not wrong or not right or wrong == right:
            continue
        conf = p.get("confidence") if p.get("confidence") in ("high", "medium", "low") else "low"
        if conf == "high" and sorted(_DIGITS.findall(wrong)) != sorted(_DIGITS.findall(right)):
            conf = "medium"
        o = by_key.get((wrong, right))
        if o is not None:
            if o.get("state") == "pending":
                n, ts = count_term(transcript, wrong)
                if n:
                    o.update(count=n, ts=ts)
            continue
        if conf == "high":
            new, n, ts = replace_term(transcript, wrong, right)
            if not n:
                continue
            transcript = new
            c = {"wrong": wrong, "right": right, "confidence": conf, "count": n, "ts": ts, "state": "applied"}
        else:
            n, ts = count_term(transcript, wrong)
            if not n:
                continue
            c = {"wrong": wrong, "right": right, "confidence": conf, "count": n, "ts": ts, "state": "pending"}
        reason = str(p.get("reason") or "").strip()
        if reason:
            c["reason"] = reason
        items.append(c)
        by_key[(wrong, right)] = c
    # pending pairs that no longer occur (e.g. fixed by hand) are dropped; history of the rest kept
    items = [c for c in items if c.get("state") != "pending" or count_term(transcript, c.get("wrong") or "")[0]]
    return transcript, {"items": items, "at": now_ms()}


# ---------------------------------------------------------------- src / verify
def relocate_meeting_src(m: dict) -> None:
    """Re-anchor every summary / notes src.t (verify.relocate_src when Task B provides it)."""
    from . import verify as V
    if hasattr(V, "relocate_src"):
        V.relocate_src(m)
        return
    tx = TranscriptIndex(m.get("transcript"), m.get("durationSec"))
    for box in (m.get("summary"), m.get("notes")):
        if isinstance(box, dict):
            for v in box.values():
                if isinstance(v, list):
                    fix_src_times(v, tx)


def fix_src_times(entries: list, tx: TranscriptIndex) -> list:
    """Re-anchor each src.t at where its quote really is (the model's t may be off)."""
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("src"), list):
            continue
        for s in e["src"]:
            q = s.get("quote") if isinstance(s, dict) else None
            if not q:
                continue
            ratio, at, _ = tx.locate(q)
            if ratio >= 0.8 and at is not None:
                s["t"] = float(at)
    return entries


def verify_review(m: dict, glossary, extra: list | None = None, *, replace_slips: bool = False) -> dict:
    """meeting.review after program verification (states of handled items carried over).

    replace_slips: the slip list is being regenerated (`extra`), so old slip items are not carried.
    A regenerated item for an entry the user already edited (same kind/section/itemId and the item
    text equals what the user wrote) stays "edited" instead of coming back as pending.
    """
    prev = m.get("review") if isinstance(m.get("review"), dict) else {}
    prev_items = [r for r in prev.get("items") or [] if isinstance(r, dict)]
    if not replace_slips:  # keep the current slip list: hand it back as `extra`
        extra = [r for r in prev_items if r.get("kind") == "slip"] + list(extra or [])
    items = verify_meeting(m, glossary, extra=list(extra or []))
    items = merge_review(prev_items, items)
    edited = {(r.get("kind"), r.get("section"), r.get("itemId")): r.get("editedText")
              for r in prev_items if r.get("state") == "edited" and r.get("itemId")}
    for r in items:
        if r.get("state") == "pending" and r.get("itemId"):
            et = edited.get((r.get("kind"), r.get("section"), r.get("itemId")))
            if et is not None and et == r.get("text"):
                r["state"] = "edited"
    return {"items": items, "at": now_ms()}


def slip_items(raw: list, tx: TranscriptIndex) -> list[dict]:
    """SLIPS output -> ReviewItems kind "slip" (t re-anchored at the quote)."""
    out = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        quote, issue = str(s.get("quote") or "").strip(), str(s.get("issue") or "").strip()
        if not quote or not issue:
            continue
        src = normalize_src([{"t": s.get("t"), "quote": quote}])
        t = src[0].get("t") if src else None
        ratio, at, _ = tx.locate(quote)
        if ratio >= 0.8 and at is not None:
            t = at
        out.append(review_item("slip", "transcript", None, quote, f"講者可能口誤或前後矛盾：{issue}",
                               suggestion=str(s.get("suggestion") or "").strip() or None, t=t))
    return out


# ---------------------------------------------------------------- entry editing
def _split2(text: str, seps=("：", ":")) -> tuple[str, str]:
    for sep in seps:
        if sep in text:
            a, b = text.split(sep, 1)
            return a.strip(), b.strip()
    return text.strip(), ""


def set_entry_text(entry, text: str):
    """The same kind of entry with its visible text replaced (inverse of items.item_text)."""
    text = text.strip()
    if isinstance(entry, str) or not isinstance(entry, dict):
        return text
    e = dict(entry)
    if isinstance(e.get("text"), str):
        e["text"] = text
    elif "q" in e or "a" in e:
        m = re.match(r"^(?:問|Q)[：:]\s*(.*?)\s*(?:答|A)[：:]\s*(.*)$", text, re.S)
        e["q"], e["a"] = (m.group(1).strip(), m.group(2).strip()) if m else (text, "")
    elif "term" in e:
        e["term"], e["plain"] = _split2(text)
        e.pop("why", None)
    elif "label" in e or "value" in e:
        e["label"], e["value"] = _split2(text)
    elif "headers" in e or "rows" in e:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        rows = [[c.strip() for c in ln.split("｜")] for ln in lines]
        if len(rows) >= 2 and len(rows[0]) == 1:
            e["title"], e["headers"], e["rows"] = lines[0].strip(), rows[1], rows[2:]
        else:
            e["title"], e["headers"], e["rows"] = "", rows[0] if rows else [], rows[1:]
    elif "title" in e or "points" in e:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        e["title"], e["points"] = (lines[0] if lines else ""), lines[1:]
    else:
        e["text"] = text
    return e


def _section_list(m: dict, section: str):
    """(container dict, key, list) for "summary.<key>" / "notes.<key>", or None."""
    area, _, key = section.partition(".")
    if area == "summary":
        box = m.get("summary")
    elif area == "notes" and key in NOTES_LIST_KEYS:
        box = m.get("notes")
    else:
        return None
    if not isinstance(box, dict) or not isinstance(box.get(key), list):
        return None
    return box, key, box[key]


def _find(m: dict, section: str, item_id: str):
    """-> (container, key, list, index) of the entry whose ensure_ids id is item_id, or None."""
    got = _section_list(m, section)
    if not got or not item_id:
        return None
    box, key, lst = got
    ids = [it["id"] for it in ensure_ids(lst, notes=section.startswith("notes."))]
    if item_id not in ids:
        return None
    return box, key, lst, ids.index(item_id)


def _apply_term(m: dict, wrong: str, right: str) -> None:
    """Apply wrong -> right to the transcript (raw saved first) and record it as applied."""
    if not (isinstance(m.get("transcriptRaw"), list) and m["transcriptRaw"]):
        m["transcriptRaw"] = copy.deepcopy(m.get("transcript") or [])
    m["transcript"], n, ts = replace_term(m.get("transcript") or [], wrong, right)
    corr = m.get("corrections") if isinstance(m.get("corrections"), dict) else {"items": []}
    items = [c for c in corr.get("items") or [] if isinstance(c, dict)]
    hit = next((c for c in items if c.get("wrong") == wrong and c.get("state") == "pending"), None)
    if hit is None:
        hit = {"wrong": wrong, "confidence": "medium"}
        items.append(hit)
    hit.update(right=right, state="applied", count=n, ts=ts or hit.get("ts") or [])
    m["corrections"] = {"items": items, "at": now_ms()}
    m["translations"] = {}


def _reject_term(m: dict, wrong: str) -> None:
    corr = m.get("corrections") if isinstance(m.get("corrections"), dict) else None
    if not corr:
        return
    for c in corr.get("items") or []:
        if isinstance(c, dict) and c.get("wrong") == wrong and c.get("state") == "pending":
            c["state"] = "rejected"
    corr["at"] = now_ms()


def review_action(m: dict, rid: str, action: str, text: str | None = None) -> None:
    """Mutate meeting m for one review action. KeyError if the review item does not exist."""
    review = m.get("review") if isinstance(m.get("review"), dict) else {}
    items = [r for r in review.get("items") or [] if isinstance(r, dict)]
    r = next((x for x in items if x.get("id") == rid), None)
    if r is None:
        raise KeyError(rid)
    kind, section, item_id = r.get("kind"), str(r.get("section") or ""), r.get("itemId")

    if kind == "term":
        wrong = str(r.get("text") or "")
        if action == "confirm":
            if r.get("suggestion"):
                _apply_term(m, wrong, str(r["suggestion"]))
            r["state"] = "confirmed"
        elif action == "edit":
            _apply_term(m, wrong, text)
            r.update(state="edited", editedText=text, suggestion=text)
        else:
            _reject_term(m, wrong)
            r["state"] = "deleted"
    elif action == "confirm":
        r["state"] = "confirmed"
    elif not item_id or not section.startswith(("summary.", "notes.")):  # slip etc.: no target entry
        r["state"] = "edited" if action == "edit" else "deleted"
        if action == "edit":
            r["editedText"] = text
    else:
        hit = _find(m, section, item_id)
        siblings = [x for x in items if x is not r and x.get("section") == section
                    and x.get("itemId") == item_id and x.get("state") == "pending"]
        if action == "delete":
            if hit:
                box, key, lst, i = hit
                gone = lst.pop(i)
                m.setdefault("alt", {}).setdefault(f"deleted:{section}", []).append(gone)
                m["translations"] = {}
            r["state"] = "deleted"
            for x in siblings:
                x["state"] = "deleted"
        else:  # edit
            new_id = item_id
            if hit:
                box, key, lst, i = hit
                lst[i] = set_entry_text(lst[i], text)
                new_id = ensure_ids(lst, notes=section.startswith("notes."))[i]["id"]
                m["translations"] = {}
            r.update(state="edited", editedText=text, itemId=new_id)
            for x in siblings:  # other doubts about the same entry stay pending, on the new text
                x.update(text=text, itemId=new_id)
    review["items"] = items
    review["at"] = now_ms()
    m["review"] = review


# ---------------------------------------------------------------- pipeline job start
def start_reprocess(store: Store, jobs, mid: str, payload: dict | None = None, prepare=None) -> dict:
    """Enqueue an ai "reprocess" job as the meeting's pipeline job (status "summarizing", aiJobId).

    prepare(m) runs first under the meeting lock (may raise HTTPException). 409 while a transcription
    or another pipeline job is active. Returns {"jobId"}.
    """
    made: list = []

    def apply(m):
        if m.get("status") in ("queued", "transcribing"):
            raise HTTPException(409, "還在轉錄中，請等轉錄完成")
        cur = jobs.read(m.get("aiJobId")) if m.get("aiJobId") else None
        if m.get("status") == "summarizing" and cur and cur.get("state") in ACTIVE_JOB_STATES:
            raise HTTPException(409, "正在整理中，請等目前的工作完成")
        if not (isinstance(m.get("transcript"), list) and m["transcript"]):
            raise HTTPException(400, "這場沒有逐字稿，無法使用 AI 功能")
        if prepare:
            prepare(m)
        jid = jobs.enqueue("ai", mid, {"action": "reprocess", **(payload or {})})
        made.append(jid)
        m.update(status="summarizing", aiJobId=jid, error=None,
                 progress={"stage": "queued", "pct": 0, "msg": "", "action": "reprocess",
                           "engine": (m.get("progress") or {}).get("engine", "")})
    try:
        store.update(mid, apply)
    except KeyError:
        raise HTTPException(404, "找不到這場會議")
    except BadId:
        raise HTTPException(400, "會議 id 不合法")
    except BaseException:
        for jid in made:
            jobs.cancel(jid, notify=False)
        raise
    return {"jobId": made[0]}


# ---------------------------------------------------------------- HTTP
def build_review_router(store: Store, get_jobs) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.post("/meetings/{mid}/review/{rid}")
    def review(mid: str, rid: str, body: dict = Body(...)):
        action = body.get("action")
        if action not in ("confirm", "edit", "delete"):
            raise HTTPException(400, "未知的動作")
        text = body.get("text")
        if action == "edit" and (not isinstance(text, str) or not text.strip()):
            raise HTTPException(400, "請輸入改正後的內容")
        text = text.strip() if isinstance(text, str) else None
        try:
            return store.update(mid, lambda m: review_action(m, rid, action, text))
        except BadId:
            raise HTTPException(400, "會議 id 不合法")
        except KeyError as e:
            if e.args and e.args[0] == rid:
                raise HTTPException(404, "找不到這個待確認項目")
            raise HTTPException(404, "找不到這場會議")

    @r.post("/meetings/{mid}/mode")
    def set_mode(mid: str, body: dict = Body(...)):
        mode = body.get("mode")
        if mode not in MODES:
            raise HTTPException(400, "模式只能是講座或會議")
        # transcript unchanged: no new correction / slip pass, only summary (+ notes) and verify
        return start_reprocess(store, get_jobs(), mid, {"correction": False, "slips": False},
                               prepare=lambda m: m.update(mode=mode))

    @r.post("/meetings/{mid}/restore-raw")
    def restore_raw(mid: str):
        def prepare(m):
            raw = m.get("transcriptRaw")
            if not (isinstance(raw, list) and raw):
                raise HTTPException(409, "沒有原始逐字稿可以還原")
            m["transcript"] = copy.deepcopy(raw)
            corr = m.get("corrections") if isinstance(m.get("corrections"), dict) else None
            if corr:
                for c in corr.get("items") or []:
                    if isinstance(c, dict):
                        c["state"] = "pending"
                        c["count"], c["ts"] = count_term(raw, str(c.get("wrong") or ""))
                corr["at"] = now_ms()
            m["translations"] = {}
        # no automatic correction afterwards: every correction is now a pending review item
        return start_reprocess(store, get_jobs(), mid, {"correction": False}, prepare=prepare)

    return r


__all__ = ["build_review_router", "start_reprocess", "merge_corrections", "verify_review", "slip_items",
           "fix_src_times", "replace_term", "count_term", "set_entry_text", "review_action", "item_text"]
