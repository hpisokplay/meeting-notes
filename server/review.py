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

from .items import NOTES_LIST_KEYS, ensure_ids, item_text, normalize_src, parse_time
from .storage import BadId, Store
from . import wordreview as W
from .verify import TranscriptIndex, extract_numbers, review_item, verify_meeting

MODES = ("lecture", "meeting")
MAX_TS = 5
ACTIVE_JOB_STATES = ("queued", "running", "claimed")


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- corrections
WINDOW_S = 15.0        # a correction only touches segments within ±15 s of a reported time
MIN_CJK = 3            # shorter CJK strings are too ambiguous to replace automatically
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_LATIN_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")


def _term_re(wrong: str) -> re.Pattern:
    """Latin / digit edges get word boundaries (CIS must not hit CISCO); CJK is plain substring."""
    pre = r"(?<![A-Za-z0-9])" if re.match(r"[A-Za-z0-9]", wrong) else ""
    post = r"(?![A-Za-z0-9])" if re.search(r"[A-Za-z0-9]$", wrong) else ""
    return re.compile(pre + re.escape(wrong) + post)


def _num(t) -> float | None:
    return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else None


def clean_ts(ts) -> list[float]:
    out = []
    for t in ts if isinstance(ts, list) else []:
        t = _num(t) if not isinstance(t, str) else parse_time(t)
        if t is not None and t not in out:
            out.append(t)
    return out


def _in_window(seg, ts: list[float] | None) -> bool:
    if not ts:
        return True
    t = _num(seg.get("t"))
    return t is not None and any(abs(t - x) <= WINDOW_S for x in ts)


def _matches(text: str, wrong: str, right: str) -> list[tuple[int, int]]:
    """Spans of `wrong` in text, except those lying inside an existing occurrence of `right`."""
    rx = _term_re(wrong)
    guard = [(m.start(), m.start() + len(right)) for m in re.finditer(re.escape(right), text)] if right else []
    return [(m.start(), m.end()) for m in rx.finditer(text)
            if not any(a <= m.start() and m.end() <= b for a, b in guard)]


def count_term(transcript, wrong: str, right: str = "", ts: list[float] | None = None) -> tuple[int, list[float]]:
    """Replaceable occurrences of `wrong` (within the ts windows if given) and segment times."""
    if not wrong:
        return 0, []
    n, hit = 0, []
    for s in transcript or []:
        if not isinstance(s, dict) or not _in_window(s, ts):
            continue
        k = len(_matches(str(s.get("text") or ""), wrong, right))
        if k:
            n += k
            t = _num(s.get("t"))
            if t is not None and len(hit) < MAX_TS:
                hit.append(t)
    return n, hit


def replace_term(transcript, wrong: str, right: str, ts: list[float] | None = None) -> tuple[list, int, list[float]]:
    """New transcript with `wrong` -> `right` in segment texts (only within ±WINDOW_S of ts when
    given; never inside an existing `right`), count, segment times."""
    if not wrong or wrong == right:
        return transcript, 0, []
    out, n, hit = [], 0, []
    for s in transcript or []:
        if isinstance(s, dict) and isinstance(s.get("text"), str) and _in_window(s, ts):
            spans = _matches(s["text"], wrong, right)
            if spans:
                text = s["text"]
                for a, b in reversed(spans):
                    text = text[:a] + right + text[b:]
                s = {**s, "text": text}
                n += len(spans)
                t = _num(s.get("t"))
                if t is not None and len(hit) < MAX_TS:
                    hit.append(t)
        out.append(s)
    return out, n, hit


def _numbers(s: str) -> list:
    return [sorted(n["values"]) for n in extract_numbers(s)]


def auto_apply_ok(wrong: str, right: str, ts: list[float], transcript) -> bool:
    """Controller ruling: a high-confidence pair is applied automatically only when ALL hold."""
    if wrong in right or right in wrong:                      # (a) no 股利 -> 股利所得 doubling
        return False
    if not (len(_CJK.findall(wrong)) >= MIN_CJK or _LATIN_TOKEN.match(wrong)):  # (b)
        return False
    if _numbers(wrong) != _numbers(right):                    # (c) never change a number
        return False
    if not ts:                                                # (d) needs windows
        return False
    n, _ = count_term(transcript, wrong, right, ts)
    return 0 < n <= max(3, 2 * len(ts))                       # (e) bounded number of hits


def merge_corrections(old, proposals: list, transcript) -> tuple[list, dict]:
    """Fold CORRECTION output into meeting.corrections and apply the safe high-confidence ones.

    - A (wrong, right) pair already known keeps its state (rejected stays rejected, a pending one is
      never auto-applied: a restore-raw or a user decision must not be undone by a re-run).
    - New high passing auto_apply_ok -> applied within ±WINDOW_S of its ts; everything else pending.
    Returns (new transcript, corrections dict).
    """
    items = [dict(c) for c in (old.get("items") if isinstance(old, dict) else None) or [] if isinstance(c, dict)]
    by_key = {(c.get("wrong"), c.get("right")): c for c in items}
    applied_rights: set[str] = set()  # no chaining: a pair may not rewrite what another just wrote
    for p in proposals or []:
        if not isinstance(p, dict):
            continue
        wrong, right = str(p.get("wrong") or "").strip(), str(p.get("right") or "").strip()
        if not wrong or not right or wrong == right:
            continue
        conf = p.get("confidence") if p.get("confidence") in ("high", "medium", "low") else "low"
        ts = clean_ts(p.get("ts"))
        if wrong in applied_rights:
            continue
        o = by_key.get((wrong, right))
        if o is not None:
            if o.get("state") == "pending":
                n, _ = count_term(transcript, wrong, right)
                if n:
                    o["count"] = n
            continue
        if conf == "high" and auto_apply_ok(wrong, right, ts, transcript):
            transcript, n, hit = replace_term(transcript, wrong, right, ts)
            applied_rights.add(right)
            c = {"wrong": wrong, "right": right, "confidence": conf, "count": n, "ts": hit, "state": "applied"}
        else:
            n, hit = count_term(transcript, wrong, right)
            if not n:
                continue
            c = {"wrong": wrong, "right": right, "confidence": conf, "count": n, "ts": ts or hit,
                 "state": "pending"}
            if conf == "high":
                c["demoted"] = True  # model said high, the safety rules said "ask a human"
        reason = str(p.get("reason") or "").strip()
        if reason:
            c["reason"] = reason
        items.append(c)
        by_key[(wrong, right)] = c
    # pending pairs that no longer occur (e.g. fixed by hand) are dropped; history of the rest kept
    items = [c for c in items if c.get("state") != "pending"
             or count_term(transcript, c.get("wrong") or "", c.get("right") or "")[0]]
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


def is_grounded(m: dict) -> bool:
    """The meeting has been through the grounded pipeline. Only reprocess / summary set `mode`;
    a review dict alone is NOT a sign (a PUT, notes, enhance or a speaker edit on a legacy meeting
    also write one), otherwise legacy source-less entries would get locked after a second edit."""
    return m.get("mode") in MODES


def verify_review(m: dict, glossary, extra: list | None = None, *, replace_slips: bool = False,
                  legacy: bool | None = None) -> dict:
    """meeting.review after program verification (states of handled items carried over).

    replace_slips: the slip list is being regenerated (`extra`), so old slip items are not carried.
    verify_meeting does the state merge (by id, and by kind/section/itemId for confirmed/edited).
    legacy: None = automatic: a meeting already through the grounded pipeline (is_grounded) is
    strict (every entry without a quote is flagged); a legacy meeting never reprocessed is not.
    """
    if legacy is None:
        legacy = not is_grounded(m)
    prev = m.get("review") if isinstance(m.get("review"), dict) else {}
    prev_items = [r for r in prev.get("items") or [] if isinstance(r, dict)]
    if not replace_slips:  # keep the current slip list: hand it back as `extra`
        extra = [r for r in prev_items if r.get("kind") == "slip"] + list(extra or [])
    items = verify_meeting(m, glossary, extra=list(extra or []), legacy=legacy)
    for r in items:
        if r.get("kind") == "term" and r.get("state") == "pending":
            r["detail"] = term_detail(m, r)
    # word-review cards: flags merged into cards stay merged, leftover cards, card targets refreshed
    items = W.finalize_review(m, items, prev_items)
    return {"items": items, "at": now_ms()}


CTX_CHARS = 8
MAX_CTX = 5


def _hms(t: float) -> str:
    s = int(t)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def term_contexts(transcript, wrong: str, right: str = "", limit: int = MAX_CTX) -> list[str]:
    """Up to `limit` occurrences of `wrong` as 「[時間] …前文【wrong】後文…」 (the text a user
    confirm would change, e.g. a look-alike 「保證與否」 for 證與)."""
    out = []
    for s in transcript or []:
        if not isinstance(s, dict):
            continue
        text = str(s.get("text") or "")
        for a, b in _matches(text, wrong, right):
            t = _num(s.get("t"))
            pre = ("…" if a > CTX_CHARS else "") + text[max(0, a - CTX_CHARS):a]
            post = text[b:b + CTX_CHARS] + ("…" if len(text) - b > CTX_CHARS else "")
            out.append((f"[{_hms(t)}] " if t is not None else "") + f"{pre}【{text[a:b]}】{post}")
            if len(out) >= limit:
                return out
    return out


def term_detail(m: dict, r: dict) -> str:
    """Term review detail with the exact total a confirm would change, plus occurrence contexts
    when the term is short (< MIN_CJK CJK chars and not a Latin token) or frequent (> MAX_CTX)."""
    wrong, right = str(r.get("text") or ""), str(r.get("suggestion") or "")
    n, _ = count_term(m.get("transcript"), wrong, right)
    detail = str(r.get("detail") or "")
    detail = re.sub(r"，出現 \d+ 次", "", detail)
    detail += f"確認後全文 {n} 處都會改成「{right}」。"
    short = len(_CJK.findall(wrong)) < MIN_CJK and not _LATIN_TOKEN.match(wrong)
    if short or n > MAX_CTX:
        ctx = term_contexts(m.get("transcript"), wrong, right)
        if ctx:
            more = f"（只列前 {len(ctx)} 處）" if n > len(ctx) else ""
            detail += f"出現位置{more}：" + "；".join(ctx) + "。若其中有不該改的，請用「改正」或「刪除」。"
    return detail


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
    corr = m.get("corrections") if isinstance(m.get("corrections"), dict) else {"items": []}
    items = [c for c in corr.get("items") or [] if isinstance(c, dict)]
    pend = [c for c in items if c.get("wrong") == wrong and c.get("state") == "pending"]
    # a USER decision changes every occurrence (the review detail listed the total and contexts);
    # only matches inside an existing `right` are left alone
    m["transcript"], n, hit_ts = replace_term(m.get("transcript") or [], wrong, right)
    target = next((c for c in items if c.get("wrong") == wrong and c.get("right") == right), None)
    if target is None:
        target = pend[0] if pend else {"wrong": wrong, "confidence": "medium"}
        if not pend:
            items.append(target)
    # every pending pair with this wrong is settled by the user's choice: merged into one entry
    items = [c for c in items if c is target or not (c.get("wrong") == wrong and c.get("state") == "pending")]
    target.update(right=right, state="applied", count=n, ts=hit_ts or target.get("ts") or [])
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


def review_action(m: dict, rid: str, action: str, text: str | None = None) -> dict | None:
    """Mutate meeting m for one review action. KeyError if the review item does not exist;
    ValueError for edit / delete on an item without a target entry (slip etc., term excepted).

    Word-review cards (kind "word"): accept / keep / edit (legacy confirm = accept, delete = keep)
    change the transcript and every summary / notes entry containing the span; returns
    applied {transcript, items}. Legacy items: accept / keep act as confirm; returns None."""
    review = m.get("review") if isinstance(m.get("review"), dict) else {}
    items = [r for r in review.get("items") or [] if isinstance(r, dict)]
    r = next((x for x in items if x.get("id") == rid), None)
    if r is None:
        raise KeyError(rid)
    if W.is_card(r):
        applied = W.card_action(m, r, action, text)
        review["items"] = items
        review["at"] = now_ms()
        m["review"] = review
        return applied
    if action in ("accept", "keep"):
        action = "confirm"
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
        # nothing to edit or delete: 「改正」 / 「刪除」 would only hide the doubt (the transcript and
        # the summary stay as they are), so the only answer is 「知道了」 (confirm)
        raise ValueError("這一項只能按「知道了」")
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


def accept_high(m: dict) -> dict:
    """Apply every eligible pending card (see W.accept_high_eligible) with the single-accept logic, in
    list order. Returns {cards, transcript, items}."""
    review = m.get("review") if isinstance(m.get("review"), dict) else {}
    out = {"cards": 0, "transcript": 0, "items": 0}
    for r in [x for x in review.get("items") or [] if W.accept_high_eligible(x)]:
        a = review_action(m, r["id"], "accept")
        out["cards"] += 1
        out["transcript"] += a.get("transcript", 0)
        out["items"] += a.get("items", 0)
    return out


def _batch_cards(review_items: list, ids) -> list[dict]:
    """Pending cards named by card id or checklist row id (W.review_rows), list order, no repeats."""
    ids = [str(x) for x in ids or [] if isinstance(x, (str, int))]
    rows = {row["id"]: row["cards"] for row in W.review_rows(review_items)}
    want: list[str] = []
    for x in ids:
        for cid in rows.get(x, [x]):
            if cid not in want:
                want.append(cid)
    by_id = {r["id"]: r for r in review_items if W.is_card(r) and r.get("state") == "pending"}
    return [by_id[c] for c in want if c in by_id]


def apply_batch(m: dict, accept, keep) -> dict:
    """The checklist's one confirm: accept (single-accept logic) every card / row in `accept`, keep
    (original is right) every one in `keep` (an id in both counts as accept). No re-verify here.
    Returns {cards, kept, transcript, items}."""
    review = m.get("review") if isinstance(m.get("review"), dict) else {}
    items = [r for r in review.get("items") or [] if isinstance(r, dict)]
    acc = _batch_cards(items, accept)
    acc_ids = {r["id"] for r in acc}
    kp = [r for r in _batch_cards(items, keep) if r["id"] not in acc_ids]
    out = {"cards": 0, "kept": 0, "transcript": 0, "items": 0}
    for r in acc:
        if r.get("state") != "pending":  # settled meanwhile (cannot happen inside one batch, defensive)
            continue
        a = review_action(m, r["id"], "accept")
        out["cards"] += 1
        out["transcript"] += a.get("transcript", 0)
        out["items"] += a.get("items", 0)
    for r in kp:
        if r.get("state") == "pending":
            review_action(m, r["id"], "keep")
            out["kept"] += 1
    return out


# ---------------------------------------------------------------- pipeline job start
def start_reprocess(store: Store, jobs, mid: str, payload: dict | None = None, prepare=None,
                    action: str = "reprocess") -> dict:
    """Enqueue an ai "reprocess" (or "deep_correct") job as the meeting's pipeline job (status "summarizing", aiJobId).

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
        jid = jobs.enqueue("ai", mid, {"action": action, **(payload or {})})
        made.append(jid)
        m.update(status="summarizing", aiJobId=jid, error=None,
                 progress={"stage": "queued", "pct": 0, "msg": "", "action": action,
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

    @r.post("/meetings/{mid}/review/accept-high")
    def review_accept_high(mid: str):
        got: dict = {}

        def apply(m):
            cur = get_jobs().read(m.get("aiJobId")) if m.get("aiJobId") else None
            if m.get("status") == "summarizing" and cur and cur.get("state") in ACTIVE_JOB_STATES:
                raise HTTPException(409, "整理中，請稍後再確認")
            got["applied"] = accept_high(m)
            if got["applied"]["cards"]:
                m["review"] = verify_review(m, store.glossary_list())  # one pure re-verify
        try:
            saved = store.update(mid, apply)
            return {**saved, "applied": got["applied"]}
        except BadId:
            raise HTTPException(400, "會議 id 不合法")
        except KeyError:
            raise HTTPException(404, "找不到這場會議")

    @r.post("/meetings/{mid}/review/apply-batch")
    def review_apply_batch(mid: str, body: dict = Body(...)):
        accept, keep = body.get("accept") or [], body.get("keep") or []
        if not isinstance(accept, list) or not isinstance(keep, list):
            raise HTTPException(400, "accept / keep 必須是清單")
        got: dict = {}

        def apply(m):
            cur = get_jobs().read(m.get("aiJobId")) if m.get("aiJobId") else None
            if m.get("status") == "summarizing" and cur and cur.get("state") in ACTIVE_JOB_STATES:
                raise HTTPException(409, "整理中，請稍後再確認")
            got["applied"] = apply_batch(m, accept, keep)
            if got["applied"]["cards"] or got["applied"]["kept"]:
                m["review"] = verify_review(m, store.glossary_list())  # one pure re-verify
        try:
            saved = store.update(mid, apply)
            return {**saved, "applied": got["applied"]}
        except BadId:
            raise HTTPException(400, "會議 id 不合法")
        except KeyError:
            raise HTTPException(404, "找不到這場會議")

    @r.post("/meetings/{mid}/review/{rid}")
    def review(mid: str, rid: str, body: dict = Body(...)):
        action = body.get("action")
        if action not in ("accept", "keep", "edit", "confirm", "delete"):
            raise HTTPException(400, "未知的動作")
        text = body.get("text")
        if action == "edit" and (not isinstance(text, str) or not text.strip()):
            raise HTTPException(400, "請輸入改正後的內容")
        text = text.strip() if isinstance(text, str) else None
        got: dict = {}

        def apply(m):
            cur = get_jobs().read(m.get("aiJobId")) if m.get("aiJobId") else None
            if m.get("status") == "summarizing" and cur and cur.get("state") in ACTIVE_JOB_STATES:
                raise HTTPException(409, "整理中，請稍後再確認")
            try:
                applied = review_action(m, rid, action, text)
            except ValueError as e:
                raise HTTPException(400, str(e))
            if applied is not None:
                got["applied"] = applied
                # pure program re-verify (no AI): handled cards / merged flags are carried over
                m["review"] = verify_review(m, store.glossary_list())
        try:
            saved = store.update(mid, apply)
            return {**saved, "applied": got["applied"]} if "applied" in got else saved
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
        # meeting.mode changes only when the job's final write succeeds (a failed run keeps the old one)
        return start_reprocess(store, get_jobs(), mid, {"mode": mode, "correction": False, "slips": False})

    @r.post("/meetings/{mid}/restore-raw")
    def restore_raw(mid: str):
        def prepare(m):
            raw = m.get("transcriptRaw")
            if not (isinstance(raw, list) and raw):
                raise HTTPException(409, "沒有原始逐字稿可以還原")
            # one slot holding the latest pre-restore transcript (user edits / applied terms)
            m.setdefault("alt", {})["transcript:beforeRestore"] = copy.deepcopy(m.get("transcript") or [])
            m["transcript"] = copy.deepcopy(raw)
            corr = m.get("corrections") if isinstance(m.get("corrections"), dict) else None
            if corr:
                for c in corr.get("items") or []:
                    if isinstance(c, dict):
                        c["state"] = "pending"
                        n, occ = count_term(raw, str(c.get("wrong") or ""), str(c.get("right") or ""))
                        c["count"], c["ts"] = n, clean_ts(c.get("ts")) or occ
                corr["at"] = now_ms()
            rv = m.get("review") if isinstance(m.get("review"), dict) else None
            if rv:  # term items are rebuilt (pending) from the reset corrections by the verify below
                rv["items"] = [x for x in rv.get("items") or [] if not (isinstance(x, dict) and x.get("kind") == "term")]
            m["translations"] = {}
            # verify now: the reset corrections are pending term items again, so the export gate is
            # closed during the job (and stays closed if it fails)
            m["review"] = verify_review(m, store.glossary_list())
        # no automatic correction afterwards: every correction is now a pending review item
        return start_reprocess(store, get_jobs(), mid, {"correction": False}, prepare=prepare)

    return r


__all__ = ["build_review_router", "start_reprocess", "merge_corrections", "verify_review", "slip_items",
           "fix_src_times", "replace_term", "count_term", "set_entry_text", "review_action", "apply_batch", "item_text"]
