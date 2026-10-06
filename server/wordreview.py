"""Word-level review cards — ReviewItem v2 (docs/superpowers/specs/2026-10-06-word-review-design.md).

A card is one review item about one span of text:
  {id, kind: "word", origin: "ai"|"leftover", span, action: "replace"|"keep"|"delete", suggestion,
   reason, confidence, targets: [{where, itemId?, count}], context, sources, fromFlags,
   state, + legacy fields section / itemId / text / detail / t}
Program flags (verify kinds + term + slip) the AI answered get state "merged" + mergedInto=<card id>.

Pure helpers: adjudicate input, normalisation of the AI output, leftover cards for partially
applied corrections, card actions (accept / keep / edit) on transcript + summary + notes.
"""
from __future__ import annotations

import re
import time
from collections import Counter

from .items import NOTES_LIST_KEYS, hash8, iter_items, parse_time
from .verify import (_CJK, _NAME_FOLLOW, _NAME_STOP, _NAME_TAIL_BAD, _SURNAME_WORDS, _SURNAMES, _TITLES,
                     TranscriptIndex, _nfkc, _person_candidates, norm)

WORD_KIND = "word"
CARD_ACTIONS = ("replace", "keep", "delete")
FLAG_KINDS = ("quote", "number", "name", "calc", "term", "slip")
CONFS = ("high", "medium", "low")
MERGED, STALE = "merged", "stale"
OPEN_STATES = ("pending", STALE)      # cards a new adjudicate run replaces
CTX_MAX = 40
REASON_MAX = 200
CONTEXT_S = 60.0
MAX_NAMES = 80
MAX_VARIANTS = 4
SKIP_KEYS = {"id", "src", "group", "anchor"}  # entry fields that are not visible text


def now_ms() -> int:
    return int(time.time() * 1000)


def _hms(t) -> str:
    s = int(t or 0)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _kept_span(r: dict) -> bool:
    """A handled card by which the user said "the original text is right" (keep / accepted keep)."""
    return r.get("resolution") == "keep" or (r.get("action") == "keep" and r.get("resolution") == "accept")


def _too_short(span: str) -> bool:
    """One CJK character (or one non-word char): replacing it everywhere would hit unrelated words."""
    from .review import _LATIN_TOKEN
    return len(span) < 2 and not _LATIN_TOKEN.match(span)


def is_card(r) -> bool:
    return isinstance(r, dict) and r.get("kind") == WORD_KIND and isinstance(r.get("span"), str)


def card_id(span: str, action: str, suggestion: str) -> str:
    return hash8(f"word|{span}|{action}|{suggestion}")


# ---------------------------------------------------------------- checklist display (word pairs)
PAIR_MAX = 12          # a word pair side is at most this many chars (longer: cut with …)
PAIR_WHOLE = 4         # span and suggestion both this short: show them whole (already a word)
DELETE_TO = "（刪除）"
_WORDISH = re.compile(r"\w")


def _clip(s: str, n: int = PAIR_MAX) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def word_pair(span: str, suggestion: str = "", action: str = "replace") -> tuple[str, str]:
    """(from, to) shown in the checklist: the minimal differing substrings of span / suggestion
    (common prefix / suffix trimmed), widened to ≥ 2 chars per side with neighbouring word chars
    (preceding first) when the change is a single char; ≤ PAIR_MAX chars. delete -> (span, （刪除）)."""
    span, sug = str(span or ""), str(suggestion or "")
    if action == "delete":
        return _clip(span), DELETE_TO
    if action != "replace" or not sug:
        return _clip(span), _clip(span)
    if len(span) <= PAIR_WHOLE and len(sug) <= PAIR_WHOLE:
        return span, sug
    n = min(len(span), len(sug))
    p = 0
    while p < n and span[p] == sug[p]:
        p += 1
    s = 0
    while s < n - p and span[-1 - s] == sug[-1 - s]:
        s += 1
    i, ja, jb = p, len(span) - s, len(sug) - s
    while ja - i < 2 or jb - i < 2:
        if i > 0 and _WORDISH.match(span[i - 1]):
            i -= 1
        elif s > 0 and _WORDISH.match(span[ja]):
            ja, jb, s = ja + 1, jb + 1, s - 1
        else:
            break
    return _clip(span[i:ja]), _clip(sug[i:jb])


def set_pair(r: dict) -> dict:
    """Store card.from / card.to (the checklist's word pair)."""
    r["from"], r["to"] = word_pair(r.get("span"), r.get("suggestion"), r.get("action"))
    return r


def row_id(frm: str, to: str) -> str:
    return "row-" + hash8(f"row|{frm}|{to}")


def card_count(r: dict) -> int:
    return sum(int(t.get("count") or 0) for t in r.get("targets") or [] if isinstance(t, dict))


def review_rows(items) -> list[dict]:
    """Checklist rows (derived view): pending replace / delete cards with the same (from, to) form one
    row {id, from, to, action, cards: [card ids], count (summed), confidence (lowest)}, list order."""
    rows: dict[tuple, dict] = {}
    for r in items or []:
        if not (is_card(r) and r.get("state") == "pending" and r.get("action") in ("replace", "delete")):
            continue
        frm, to = word_pair(r["span"], r.get("suggestion"), r.get("action"))
        row = rows.get((frm, to))
        if row is None:
            row = rows[(frm, to)] = {"id": row_id(frm, to), "from": frm, "to": to, "action": r["action"],
                                     "cards": [], "count": 0, "confidence": r.get("confidence") or "low"}
        row["cards"].append(r["id"])
        row["count"] += card_count(r)
        if _RANK.get(r.get("confidence"), 1) < _RANK.get(row["confidence"], 1):
            row["confidence"] = r.get("confidence") or "low"
    return list(rows.values())


def auto_keep(r: dict) -> None:
    """An AI "keep" card ("not an error") is settled by the AI itself: never pending / shown / counted."""
    r.update(state="confirmed", resolvedBy="ai", resolution="keep", keepReason=r.get("reason") or "",
             decidedAt=r.get("decidedAt") or now_ms())


def migrate_cards(m: dict) -> bool:
    """Open AI keep cards -> auto-confirmed; cards without from / to get them. True if m changed."""
    review = m.get("review") if isinstance(m.get("review"), dict) else None
    changed = False
    for r in (review or {}).get("items") or []:
        if not is_card(r):
            continue
        if r.get("action") == "keep" and r.get("state") in OPEN_STATES:
            auto_keep(r)
            changed = True
        if r.get("from") is None or r.get("to") is None:
            set_pair(r)
            changed = True
    return changed


# ---------------------------------------------------------------- span matching
def _matches(text: str, span: str, guard: str = "") -> list[tuple[int, int]]:
    from .review import _matches as m  # Latin edges get word boundaries; never inside `guard`
    return m(text, span, guard)


def _map_strings(x, fn, key=None):
    """Apply fn(str) -> (str, n) to every visible string leaf of an entry; -> (new, total n)."""
    if isinstance(x, str):
        return fn(x)
    if isinstance(x, list):
        out, n = [], 0
        for v in x:
            v2, k = _map_strings(v, fn)
            out.append(v2)
            n += k
        return out, n
    if isinstance(x, dict):
        out, n = {}, 0
        for k, v in x.items():
            if k in SKIP_KEYS:
                out[k] = v
                continue
            v2, c = _map_strings(v, fn, k)
            out[k] = v2
            n += c
        return out, n
    return x, 0


def _counter(span: str, guard: str):
    return lambda s: (s, len(_matches(s, span, guard)))


def _replacer(span: str, new: str, guard: str):
    def fn(s):
        spans = _matches(s, span, guard)
        for a, b in reversed(spans):
            s = s[:a] + new + s[b:]
        return s, len(spans)
    return fn


_DUP_PUNCT = re.compile(r"([，、；。：,;])(?:\s*[，、；,;])+")


def _tidy(s: str) -> str:
    s = _DUP_PUNCT.sub(r"\1", s)
    s = re.sub(r"[，、；,;]\s*([。！？）」])", r"\1", s)
    s = re.sub(r"（\s*）|「\s*」", "", s)
    s = re.sub(r"^\s*[，、；,;。]\s*", "", s)
    return s.strip()


def _deleter(span: str):
    def fn(s):
        spans = _matches(s, span, "")
        if not spans:
            return s, 0
        for a, b in reversed(spans):
            s = s[:a] + s[b:]
        return _tidy(s), len(spans)
    return fn


def _entry_lists(m: dict):
    """(section, list) for every summary / notes list (same order as items.iter_items)."""
    summ = m.get("summary") if isinstance(m.get("summary"), dict) else {}
    for k, lst in summ.items():
        if isinstance(lst, list):
            yield f"summary.{k}", lst
    notes = m.get("notes") if isinstance(m.get("notes"), dict) else {}
    for k in NOTES_LIST_KEYS:
        if isinstance(notes.get(k), list):
            yield f"notes.{k}", notes[k]


def _entry_ids(m: dict) -> dict:
    """{(section, index): itemId} (ids exactly as iter_items / ensure_ids give them)."""
    out, idx = {}, Counter()
    for section, _raw, item_id, _t, _s in iter_items(m):
        out[(section, idx[section])] = item_id
        idx[section] += 1
    return out


def targets(m: dict, span: str, action: str, suggestion: str = "") -> list[dict]:
    """[{where, itemId?, count}]: transcript (not for delete) + every entry containing span."""
    guard = suggestion if action == "replace" else ""
    out = []
    if action != "delete":
        n = sum(len(_matches(str(s.get("text") or ""), span, guard))
                for s in m.get("transcript") or [] if isinstance(s, dict))
        if n:
            out.append({"where": "transcript", "count": n})
    ids = _entry_ids(m)
    for section, lst in _entry_lists(m):
        for i, e in enumerate(lst):
            _, n = _map_strings(e, _counter(span, guard))
            if n:
                out.append({"where": section, "itemId": ids.get((section, i)), "count": n})
    return out


def _snip(text: str, a: int, b: int) -> str:
    side = max(4, (CTX_MAX - (b - a) - 2) // 2)
    pre = ("…" if a > side else "") + text[max(0, a - side):a]
    post = text[b:b + side] + ("…" if len(text) - b > side else "")
    return f"{pre}【{text[a:b]}】{post}".replace("\n", " ")


def _entry_text_of(m: dict, section: str, item_id: str) -> tuple[str, float | None]:
    for sec, _raw, iid, text, src in iter_items(m):
        if sec == section and iid == item_id:
            ts = [s["t"] for s in src if isinstance(s.get("t"), (int, float))]
            return text, (min(ts) if ts else None)
    return "", None


def context_and_t(m: dict, span: str, tg: list[dict]) -> tuple[str, float | None]:
    """Context (≤ ~40 chars, span in 【】): the first entry target, else the first transcript hit."""
    t = None
    for s in m.get("transcript") or []:
        if isinstance(s, dict):
            hit = _matches(str(s.get("text") or ""), span)
            if hit:
                t = parse_time(s.get("t"))
                tx_ctx = _snip(str(s.get("text")), *hit[0])
                break
    else:
        tx_ctx = ""
    for x in tg:
        if x["where"] != "transcript":
            text, t0 = _entry_text_of(m, x["where"], x.get("itemId"))
            hit = _matches(text, span)
            if hit:
                return _snip(text, *hit[0]), (t if t is not None else t0)
    return tx_ctx, t


# ---------------------------------------------------------------- cards
def make_card(m: dict, span: str, action: str, suggestion: str, reason: str, confidence: str,
              from_flags: list[str], sources: list[str], origin: str = "ai", tg: list | None = None) -> dict:
    tg = targets(m, span, action, suggestion) if tg is None else tg
    ctx, t = context_and_t(m, span, tg)
    first_item = next((x for x in tg if x["where"] != "transcript"), None)
    c = {"id": card_id(span, action, suggestion), "kind": WORD_KIND, "origin": origin,
         "span": span, "action": action, "suggestion": suggestion, "reason": reason,
         "confidence": confidence, "targets": tg, "context": ctx, "sources": sources,
         "fromFlags": list(from_flags), "state": "pending",
         # legacy fields: an old client shows text + detail and never crashes on a card
         "section": first_item["where"] if first_item else "transcript", "text": ctx or span, "detail": reason}
    if first_item and first_item.get("itemId"):
        c["itemId"] = first_item["itemId"]
    if isinstance(t, (int, float)):
        c["t"] = float(t)
    return set_pair(c)


_RANK = {"high": 3, "medium": 2, "low": 1}


def normalize_cards(raw, m: dict, flags: dict) -> list[dict]:
    """AI output -> cards. span must exist in its targets (else dropped); a replace without a usable
    suggestion becomes keep (low); a delete whose span is only in the transcript becomes keep (low);
    same (span, action, suggestion) -> one card (flags merged, best reason kept)."""
    out: dict[tuple, dict] = {}
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        span = str(x.get("span") or "").strip()
        action = x.get("action")
        if not span or action not in CARD_ACTIONS:
            continue
        sug = str(x.get("suggestion") or "").strip()
        conf = x.get("confidence") if x.get("confidence") in CONFS else "low"
        reason = re.sub(r"\s+", " ", str(x.get("reason") or "")).strip()[:REASON_MAX]
        ff = []
        for f in x.get("fromFlags") or []:
            if isinstance(f, str) and f in flags and f not in ff:
                ff.append(f)
        if action == "replace" and (not sug or sug == span or _too_short(span)):
            action, conf = "keep", "low"
        if action != "replace":
            sug = ""
        tg = targets(m, span, action, sug)
        if not tg and action == "delete":
            tg = targets(m, span, "keep")
            if tg:
                action, conf = "keep", "low"
        if not tg:
            continue
        key = (span, action, sug)
        if key in out:
            c = out[key]
            c["fromFlags"] += [f for f in ff if f not in c["fromFlags"]]
            if _RANK[conf] > _RANK[c["confidence"]]:
                c["confidence"], c["reason"] = conf, reason or c["reason"]
            elif not c["reason"]:
                c["reason"] = reason
            continue
        out[key] = {"span": span, "action": action, "suggestion": sug, "reason": reason,
                    "confidence": conf, "fromFlags": ff, "tg": tg}
    cards = []
    for c in out.values():
        kinds = sorted({flags[f].get("kind") for f in c["fromFlags"] if flags[f].get("kind")})
        card = make_card(m, c["span"], c["action"], c["suggestion"], c["reason"], c["confidence"],
                         c["fromFlags"], kinds or ["ai"], tg=c["tg"])
        card["detail"] = card["reason"]
        cards.append(card)
    return cards


def leftover_cards(m: dict) -> list[dict]:
    """Applied corrections whose `wrong` still occurs in the transcript -> replace cards."""
    from .review import count_term
    out = []
    corr = m.get("corrections") if isinstance(m.get("corrections"), dict) else {}
    for c in corr.get("items") or []:
        if not isinstance(c, dict) or c.get("state") != "applied":
            continue
        wrong, right = str(c.get("wrong") or ""), str(c.get("right") or "")
        if not wrong or not right or wrong == right:
            continue
        n, ts = count_term(m.get("transcript"), wrong, right)
        if not n:
            continue
        done = c.get("count") if isinstance(c.get("count"), int) else 0
        at = f"，例如 [{_hms(ts[0])}]" if ts else ""
        reason = f"已自動改 {done} 處「{wrong}」→「{right}」，另有 {n} 處未改{at}"
        conf = c.get("confidence") if c.get("confidence") in CONFS else "medium"
        out.append(make_card(m, wrong, "replace", right, reason, conf, [], ["term"], origin="leftover"))
    return out


# ---------------------------------------------------------------- review list maintenance
def _flag_key(r: dict):
    return (r.get("kind"), r.get("section"), r.get("itemId")) if r.get("itemId") else None


def finalize_review(m: dict, items: list[dict], prev: list[dict] | None = None) -> list[dict]:
    """After a (pure) verify: re-mark flags merged into cards (by id, or kind/section/itemId),
    add leftover cards, refresh targets of pending cards (no target left -> stale / leftover gone),
    un-merge flags whose card disappeared. Handled cards are never resurrected."""
    items = [r for r in items if isinstance(r, dict)]
    prev = [r for r in prev or [] if isinstance(r, dict)]
    merged_id, merged_key = {}, {}
    for r in prev + items:
        if not is_card(r) and r.get("state") == MERGED and r.get("mergedInto"):
            merged_id[r["id"]] = r["mergedInto"]
            k = _flag_key(r)
            if k:
                merged_key[k] = r["mergedInto"]
    cards = {r["id"]: r for r in items if is_card(r)}
    handled = [r for r in cards.values() if r.get("state") not in OPEN_STATES]
    handled_sa = {(r["span"], r["action"]) for r in handled}
    kept = {r["span"] for r in handled if _kept_span(r)}  # the user said the original is right
    # leftovers (recomputed from the corrections every time)
    fresh = {c["id"]: c for c in leftover_cards(m)}
    out = []
    for r in items:
        if is_card(r) and r.get("origin") == "leftover" and r.get("state") in OPEN_STATES:
            if r["id"] in fresh:
                out.append({**fresh.pop(r["id"]), "state": "pending"})
            continue  # the leftover is gone (fixed elsewhere)
        out.append(r)
    for c in fresh.values():
        if c["id"] not in cards and (c["span"], c["action"]) not in handled_sa and c["span"] not in kept:
            out.append(c)
    cards = {r["id"]: r for r in out if is_card(r)}
    for r in out:
        if is_card(r):
            set_pair(r)
            if r.get("action") == "keep" and r.get("state") in OPEN_STATES:
                auto_keep(r)  # "not an error": the AI settles it, it never reaches the checklist
                continue
            if r.get("state") in OPEN_STATES and r.get("origin") != "leftover":
                tg = targets(m, r["span"], r["action"], r.get("suggestion") or "")
                if tg:
                    ctx, t = context_and_t(m, r["span"], tg)
                    r.update(targets=tg, context=ctx, text=ctx or r["span"], state="pending")
                    if t is not None:
                        r["t"] = float(t)
                else:
                    r.update(targets=[], state=STALE)
            continue
        into = merged_id.get(r.get("id"))
        if into is None and _flag_key(r):
            into = merged_key.get(_flag_key(r))
        if r.get("state") == MERGED and r.get("mergedInto") not in cards:
            r["state"] = "pending"
            r.pop("mergedInto", None)
        elif r.get("state") == "pending" and into in cards:
            r["state"], r["mergedInto"] = MERGED, into
    return out


def apply_ai_cards(m: dict, raw) -> list[dict]:
    """Replace the open (pending / stale) AI cards of meeting.review with the adjudicate output.

    Program flags named in fromFlags become "merged" into their card. A card equal (id, or same
    span + action) to one the user already handled is not added again: its flags merge into the
    handled card. Returns the new cards."""
    return _fold_cards(m, raw, replace_open=True)


def add_ai_cards(m: dict, raw) -> list[dict]:
    """Add adjudicate output to meeting.review WITHOUT dropping the open cards (加強校對). Same rules as
    apply_ai_cards; in addition a card about a span the user kept (「原文正確」) is never added."""
    return _fold_cards(m, raw, replace_open=False)


def _fold_cards(m: dict, raw, replace_open: bool) -> list[dict]:
    review = m.get("review") if isinstance(m.get("review"), dict) else {"items": []}
    items = [r for r in review.get("items") or [] if isinstance(r, dict)]
    if replace_open:
        items = [r for r in items if not (is_card(r) and r.get("origin") != "leftover" and r.get("state") in OPEN_STATES)]
    keep_ids = {r["id"] for r in items if is_card(r)}
    for r in items:  # flags merged into a dropped card are open again
        if not is_card(r) and r.get("state") == MERGED and r.get("mergedInto") not in keep_ids:
            r["state"] = "pending"
            r.pop("mergedInto", None)
    flags = {r["id"]: r for r in items if not is_card(r) and r.get("kind") in FLAG_KINDS and r.get("id")}
    handled = [r for r in items if is_card(r) and r.get("state") not in OPEN_STATES]
    by_id = {r["id"]: r for r in items if is_card(r)}
    by_sa = {(r["span"], r["action"]): r for r in handled}
    for r in handled:  # "keep the original" also answers a later keep card for the same span
        if _kept_span(r):
            by_sa.setdefault((r["span"], "keep"), r)
    new = []
    kept = {r["span"]: r for r in handled if _kept_span(r) and r.get("resolvedBy") != "ai"}
    for c in normalize_cards(raw, m, flags):
        target = by_id.get(c["id"]) or by_sa.get((c["span"], c["action"]))
        if target is None and not replace_open and c["span"] in kept:
            target = kept[c["span"]]  # the user said the original is right: answer the flags with it
        if target is None:
            new.append(c)
            by_id[c["id"]] = target = c
        for f in c["fromFlags"]:
            if flags[f].get("state") == "pending":
                flags[f]["state"], flags[f]["mergedInto"] = MERGED, target["id"]
    items = finalize_review(m, items + new)
    m["review"] = {**review, "items": items, "at": now_ms()}
    return new


# ---------------------------------------------------------------- card actions
def replace_in_entries(m: dict, span: str, new: str, *, quotes: bool = False) -> int:
    """span -> new in every summary / notes entry (all visible text fields; src quotes too when
    quotes=True, i.e. the transcript was changed the same way). Returns entries changed."""
    changed = 0
    for _section, lst in _entry_lists(m):
        for i, e in enumerate(lst):
            e2, n = _map_strings(e, _replacer(span, new, new))
            if quotes and isinstance(e2, dict) and isinstance(e2.get("src"), list):
                e2["src"] = [{**s, "quote": _replacer(span, new, new)(s["quote"])[0]}
                             if isinstance(s, dict) and isinstance(s.get("quote"), str) else s for s in e2["src"]]
            if n:
                lst[i] = e2
                changed += 1
            elif quotes:
                lst[i] = e2
    return changed


def delete_in_entries(m: dict, span: str) -> int:
    changed = 0
    for _section, lst in _entry_lists(m):
        for i, e in enumerate(lst):
            e2, n = _map_strings(e, _deleter(span))
            if n:
                lst[i] = e2
                changed += 1
    return changed


# person-name guard for 「全部照建議」: surnames incl. the common-character ones verify drops
_NAME_SURNAMES = set(
    "王李張劉陳楊黃趙吳周徐孫朱胡郭何林羅高梁鄭謝宋唐許韓馮鄧曹彭曾蕭蔡潘杜戴夏鍾汪"
    "田任姜范方石姚譚廖鄒熊陸郝孔崔康毛邱秦史顧侯邵孟龍萬段雷錢湯尹黎易常武喬賀賴龔"
    "文盧簡游柯魏翁施詹余葉洪呂蘇沈莊江涂連童傅歐卓溫邢藍嚴董薛")


def looks_like_name(s: str) -> bool:
    s = str(s or "").strip()
    return 2 <= len(s) <= 4 and all("一" <= c <= "鿿" for c in s) and s[0] in _NAME_SURNAMES


def accept_high_eligible(r) -> bool:
    """Pending card, high confidence, no program 'name' flag behind it, span / suggestion not name-like."""
    return (is_card(r) and r.get("state") == "pending" and r.get("confidence") == "high"
            and "name" not in (r.get("sources") or [])
            and not looks_like_name(r.get("span")) and not looks_like_name(r.get("suggestion")))


def card_action(m: dict, r: dict, action: str, text: str | None = None) -> dict:
    """accept / keep / edit (legacy confirm = accept, delete = keep) on card r. Mutates m and r;
    returns applied {transcript: n, items: m}."""
    from . import review as R
    if action == "confirm":
        action = "accept"
    elif action == "delete":
        action = "keep"
    span = r["span"]
    if action == "edit":
        act, sug = "replace", text or ""
    elif action == "accept":
        act, sug = r.get("action"), r.get("suggestion") or ""
    else:
        act, sug = "keep", ""
    n_tx = n_items = 0
    if act == "replace" and sug and sug != span:
        n_tx, _ = R.count_term(m.get("transcript"), span, sug)
        if n_tx:
            R._apply_term(m, span, sug)  # all occurrences, raw kept, recorded in corrections
        n_items = replace_in_entries(m, span, sug, quotes=bool(n_tx))
    elif act == "delete":
        n_items = delete_in_entries(m, span)
    else:
        corr = m.get("corrections") if isinstance(m.get("corrections"), dict) else {}
        if any(isinstance(c, dict) and c.get("wrong") == span and c.get("state") == "pending"
               for c in corr.get("items") or []):
            R._reject_term(m, span)  # the original text is right: the pending correction is rejected
    if n_tx or n_items:
        m["translations"] = {}
    applied = {"transcript": n_tx, "items": n_items}
    r.update(state="edited" if action == "edit" else "confirmed", resolution=action,
             applied=applied, decidedAt=now_ms())
    if action == "edit":
        r["editedText"] = text
    return applied


# ---------------------------------------------------------------- adjudicate input
def _lines_near(transcript, anchors: list[float], window: float = CONTEXT_S) -> list[str]:
    out = []
    for s in transcript or []:
        if not isinstance(s, dict):
            continue
        t = parse_time(s.get("t"))
        if t is not None and any(abs(t - a) <= window for a in anchors):
            out.append(f"[{_hms(t)}] {s.get('speaker') or '說話者'}: {s.get('text') or ''}")
    return out


def _name_tokens(text: str, loose: bool = False) -> list[str]:
    """3-char person-name-like tokens (same rules as verify.near_miss_names) + title-anchored names.
    loose=True: also tokens not standing like a name (黃任宗以…); the caller keeps those only when
    the transcript has them AND a one-character variant (a likely ASR misspelling pair)."""
    s = _nfkc(text)
    out = []
    for i, ch in enumerate(s):
        if ch not in _SURNAMES:
            continue
        tok = s[i:i + 3]
        if len(tok) < 3 or not all(_CJK.match(c) for c in tok):
            continue
        if tok[:2] in _SURNAME_WORDS or set(tok) & _NAME_STOP or tok[2] in _NAME_TAIL_BAD:
            continue
        after, before = s[i + 3:], s[:i]
        if not loose and not (not after or not _CJK.match(after[0]) or after[0] in _NAME_FOLLOW
                              or any(after.startswith(t) for t in _TITLES) or any(before.endswith(t) for t in _TITLES)):
            continue
        if tok not in out:
            out.append(tok)
    for shown, _forms in _person_candidates(text):
        if shown not in out:
            out.append(shown)
    return out


def name_list(m: dict) -> list[str]:
    """「人名與異寫清單」 lines: names in the entries / title-anchored names in the transcript, their
    transcript count and first time, and transcript spellings differing in one character."""
    tx = TranscriptIndex(m.get("transcript"), m.get("durationSec"))
    names: list[str] = []
    strict: set[str] = set()
    for _sec, _raw, _iid, text, _src in iter_items(m):
        strict.update(_name_tokens(text))
        names += [n for n in _name_tokens(text, loose=True) if n not in names]
    for s in m.get("transcript") or []:
        if isinstance(s, dict):
            got = [n for n, _ in _person_candidates(str(s.get("text") or ""))]
            strict.update(got)
            names += [n for n in got if n not in names]
    lines = []
    for name in names:
        if len(lines) >= MAX_NAMES:
            break
        n = norm(name)
        cnt = tx.full.count(n)
        first = tx.full.find(n)
        line = f"- {name}：逐字稿 {cnt} 次" + (f"（首見 {_hms(tx.time_at(first))}）" if first >= 0 else "")
        if len(n) == 3:
            pat = re.compile(re.escape(n[0]) + "(?:" + re.escape(n[1]) + "[一-鿿]|[一-鿿]" + re.escape(n[2]) + ")")
            var = Counter()
            pos: dict[str, int] = {}
            for mm in pat.finditer(tx.full):
                v = mm.group()
                if v == n or v[2] in _NAME_TAIL_BAD or v[:2] in _SURNAME_WORDS or set(v) & _NAME_STOP:
                    continue
                var[v] += 1
                pos.setdefault(v, mm.start())
            if var:
                line += "；相近寫法：" + "、".join(f"{v} {k} 次（{_hms(tx.time_at(pos[v]))}）"
                                              for v, k in var.most_common(MAX_VARIANTS))
            elif name not in strict:
                continue
            if name not in strict and not cnt:
                continue
        elif name not in strict:
            continue
        lines.append(line)
    return lines


def _corrections_lines(m: dict) -> list[str]:
    corr = m.get("corrections") if isinstance(m.get("corrections"), dict) else {}
    out = []
    for c in corr.get("items") or []:
        if isinstance(c, dict) and c.get("wrong") and c.get("right") and c.get("state") in ("applied", "pending"):
            st = "已自動套用" if c["state"] == "applied" else "待確認"
            out.append(f"- 「{c['wrong']}」→「{c['right']}」（{st}，{c.get('count') or 0} 處）")
    return out


def _flag_line(r: dict) -> str:
    sug = f"（程式建議：{r['suggestion']}）" if r.get("suggestion") else ""
    return f"[F:{r['id']}] {r.get('kind')}：{r.get('detail') or ''}{sug}"


def _pending_flags(m: dict, flag_ids=None) -> list[dict]:
    review = m.get("review") if isinstance(m.get("review"), dict) else {}
    return [r for r in review.get("items") or [] if isinstance(r, dict) and not is_card(r)
            and r.get("kind") in FLAG_KINDS and r.get("state") == "pending" and r.get("id")
            and (flag_ids is None or r["id"] in flag_ids)]


def pending_flag_ids(m: dict, flag_ids=None) -> set[str]:
    """Ids of the pending program flags an adjudicate run is asked about."""
    return {r["id"] for r in _pending_flags(m, flag_ids)}


def adjudicate_blocks(m: dict, flag_ids=None, entries: bool = True) -> tuple[str, list[str]]:
    """(header, blocks) of the ADJUDICATE user message; blocks are chunked by the caller.
    flag_ids: only these pending flags (None = all); entries=False: no summary / notes entry blocks
    (flags attached to an entry are then sent on their own)."""
    flags = _pending_flags(m, flag_ids)
    by_item: dict[tuple, list] = {}
    loose = []
    for r in flags:
        if entries and r.get("itemId") and str(r.get("section") or "").startswith(("summary.", "notes.")):
            by_item.setdefault((r["section"], r["itemId"]), []).append(r)
        else:
            loose.append(r)
    head = ["## 人名與異寫清單（程式從條目與逐字稿整理）", *(name_list(m) or ["（無）"]), "",
            "## 名詞校正紀錄", *(_corrections_lines(m) or ["（無）"]), ""]
    blocks = []
    tr = m.get("transcript")
    for r in loose:
        b = [_flag_line(r)]
        if r.get("kind") == "term":
            from .review import _matches as mt
            wrong, n = str(r.get("text") or ""), 0
            for s in tr or []:
                if not isinstance(s, dict):
                    continue
                text = str(s.get("text") or "")
                for a, e in mt(text, wrong, str(r.get("suggestion") or "")):
                    b.append(f"  [{_hms(parse_time(s.get('t')) or 0)}] …{text[max(0, a - 20):e + 20]}…")
                    n += 1
                    if n >= 4:
                        break
                if n >= 4:
                    break
        else:
            b.append(f"  原句：「{r.get('text') or ''}」")
            if isinstance(r.get("t"), (int, float)):
                b += ["  前後逐字稿："] + ["  " + x for x in _lines_near(tr, [r["t"]])]
        blocks.append("\n".join(b))
    for section, _raw, iid, text, src in iter_items(m) if entries else ():
        if not str(text).strip():
            continue
        b = [f"### [I:{iid}] {section}", f"條目：{text}"]
        quotes = [f"[{_hms(s['t'])}]「{s.get('quote') or ''}」" if isinstance(s.get("t"), (int, float))
                  else f"「{s.get('quote') or ''}」" for s in src if s.get("quote")]
        b.append("原句：" + ("；".join(quotes) if quotes else "（無）"))
        fl = by_item.pop((section, iid), [])
        if fl:
            b += ["程式旗標："] + ["  " + _flag_line(r) for r in fl]
        anchors = [s["t"] for s in src if isinstance(s.get("t"), (int, float))]
        near = _lines_near(tr, anchors) if anchors else []
        if near:
            b += ["前後逐字稿："] + near
        blocks.append("\n".join(b))
    for fl in by_item.values():  # flags whose entry is gone: still answer them
        blocks += [_flag_line(r) for r in fl]
    return "\n".join(head), blocks


def adjudicate_messages(m: dict, budget: int, flag_ids=None, entries: bool = True) -> list[str]:
    head, blocks = adjudicate_blocks(m, flag_ids, entries)
    if not blocks:
        return []
    out, cur, size = [], [], len(head)
    for b in blocks:
        if cur and size + len(b) + 2 > budget:
            out.append(cur)
            cur, size = [], len(head)
        cur.append(b)
        size += len(b) + 2
    if cur:
        out.append(cur)
    return [head + "\n\n## 程式旗標與條目\n\n" + "\n\n".join(c) for c in out]
