"""Program-side verification (pure, no LLM) -> 待確認清單 ReviewItems.

For every summary / notes entry:
  quote  : each src.quote must be found in the transcript (punctuation/space-insensitive,
           difflib ratio >= 0.8 on candidate windows);
  number : every number in the text (arabic, 中文數字 incl. 萬/億/千/百/十, %, 年) must appear,
           numerically equivalent, in the transcript within ±60 s of the entry's sources
           (anywhere when the entry has no source);
  name   : glossary terms, corrections.right, 「…」 terms, Latin words (>= 2 letters) and
           title-anchored person names (許崑源會計師 / 署長宋秀玲) must occur in the transcript;
  calc   : a×b=c / a%×b%=c% / a*b=c are recomputed.
Pending corrections (medium/low confidence) become kind "term" items.

ReviewItem: {id, kind, section, itemId?, text, detail, suggestion?, t?, state}
ids are stable (hash of kind+section+itemId+text) so states survive re-verification.
"""
from __future__ import annotations

import bisect
import difflib
import re
import unicodedata
from collections import Counter

from .items import hash8, item_text, iter_items, parse_time

QUOTE_MIN_RATIO = 0.8
NUMBER_WINDOW_SEC = 60.0
VERIFY_KINDS = ("quote", "number", "name", "calc", "term")  # recomputed on every run
KEEP_STATES = ("confirmed", "edited", "deleted")

# ---------------------------------------------------------------- text normalisation
_VARIANTS = str.maketrans({"臺": "台", "两": "兩", "万": "萬", "亿": "億", "〇": "零"})
_NONWORD = re.compile(r"[^\w%]|_", re.UNICODE)


def norm(s: str) -> str:
    """NFKC, 臺→台, lower-case, drop whitespace and punctuation (keeps CJK, letters, digits, %)."""
    s = unicodedata.normalize("NFKC", str(s or "")).translate(_VARIANTS).lower()
    return _NONWORD.sub("", s)


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).translate(_VARIANTS)


# ---------------------------------------------------------------- numbers
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SMALL_MAG = {"十": 10, "百": 100, "千": 1000}
_BIG_MAG = {"萬": 1e4, "億": 1e8, "兆": 1e12}
_RUN = re.compile(r"[0-9.,零一二兩三四五六七八九十百千萬億兆]+")
_TOK = re.compile(r"\d+(?:\.\d+)?|[零一二兩三四五六七八九]|[十百千萬億兆]")
_STOP_RUNS = {"一一"}


def _parse_tokens(toks: list[str]) -> float | None:
    """Standard Chinese/arabic mixed numeral: 五千萬, 5000萬, 一億兩千萬, 3.5億, 十二, 一零八."""
    if not toks:
        return None
    if all(t in _CN_DIGIT for t in toks):  # digit-by-digit reading: 一零八 -> 108
        return float("".join(str(_CN_DIGIT[t]) for t in toks))
    total = section = 0.0
    num = None
    for t in toks:
        if t in _CN_DIGIT:
            num = float(_CN_DIGIT[t])
        elif t in _SMALL_MAG:
            section += (num if num is not None else 1.0) * _SMALL_MAG[t]
            num = None
        elif t in _BIG_MAG:
            group = section + (num or 0.0)
            if t == "萬":
                total += (group or 1.0) * 1e4
            else:  # 億 / 兆 scale everything before them
                total = (total + group or 1.0) * _BIG_MAG[t]
            section, num = 0.0, None
        else:
            num = float(t)
    return total + section + (num or 0.0)


def parse_number(piece: str) -> list[float]:
    """All plausible values of one numeral run (several for ranges / colloquial forms)."""
    toks = _TOK.findall(piece)
    if not toks:
        return []
    alts = []
    # range: 三四千億 -> 3000億 / 4000億 ; 五六十 -> 50 / 60
    if (len(toks) >= 3 and toks[0] in _CN_DIGIT and toks[1] in _CN_DIGIT and toks[0] != "零"
            and toks[1] != "零" and toks[0] != toks[1] and (toks[2] in _SMALL_MAG or toks[2] in _BIG_MAG)):
        for d in (toks[0], toks[1]):
            v = _parse_tokens([d] + toks[2:])
            if v is not None:
                alts.append(v)
        return alts
    v = _parse_tokens(toks)
    if v is not None:
        alts.append(v)
    # colloquial trailing digit: 三萬五 = 35000, 兩千五 = 2500, 一百五 = 150
    if len(toks) >= 2 and toks[-1] in _CN_DIGIT and toks[-1] != "零":
        prev = toks[-2]
        scale = {"萬": 1000, "千": 100, "百": 10, "億": 1e7}.get(prev)
        if scale:
            base = _parse_tokens(toks[:-1])
            if base is not None:
                alts.append(base + _CN_DIGIT[toks[-1]] * scale)
    return alts


def _pieces(run: str):
    """Split a raw run on commas that are not thousands separators; yield (piece, offset)."""
    run2 = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "\0", run)  # mark thousands commas
    off = 0
    for part in run2.split(","):
        clean = part.replace("\0", "")
        lead = len(clean) - len(clean.lstrip("."))
        piece = clean.strip(".")
        if piece:
            yield piece, off + lead
        off += len(part) + 1


def extract_numbers(text: str, strict: bool = False) -> list[dict]:
    """Numbers in text as [{raw, values:set[float], percent:bool}].

    strict=True (the item side) skips things that are not claims worth checking: model numbers
    glued to Latin letters (B200, 400G, 5G), runs starting with 萬/億/千/百 (千萬不要, 萬一),
    and plain values <= 10 that are not percentages (一般, 兩個, 第三).
    """
    s = _nfkc(text)
    out = []
    for m in _RUN.finditer(s):
        for piece, off in _pieces(m.group()):
            start = m.start() + off
            end = start + len(piece)
            if not any(c.isdigit() or c in _CN_DIGIT or c in _SMALL_MAG for c in piece):
                continue
            vals = parse_number(piece)
            if not vals:
                continue
            before, after = s[max(0, start - 3):start], s[end:end + 2]
            percent = after[:1] == "%" or before.endswith("百分之")
            values = set(vals)
            if after[:1] == "成" and all(v <= 10 for v in vals):
                nxt = s[end + 1:end + 2]
                tail = _CN_DIGIT.get(nxt, int(nxt) if nxt.isdigit() else None)
                values |= {v * 10 + (tail or 0) for v in vals}  # 八成 = 80%, 九成五 = 95%
                percent = True
            if after[:1] == "年":
                for v in vals:
                    if 1 <= v <= 200:
                        values.add(v + 1911)
                    elif 1912 <= v <= 2200:
                        values.add(v - 1911)
            if strict:
                latin = bool(re.match(r"[A-Za-z]", after[:1])) or bool(re.search(r"[A-Za-z]$", before))
                if latin or piece[0] in "萬億兆千百" or piece in _STOP_RUNS:
                    continue
                concat = len(piece) > 1 and all(c in _CN_DIGIT for c in piece)
                if concat and after[:1] not in ("年", "%"):  # 二三代, 一一 are not numbers
                    continue
                if not percent and max(vals) <= 10:
                    continue
            out.append({"raw": piece + ("%" if after[:1] == "%" else ""), "values": values, "percent": percent})
    return out


def _num_match(a: set, b: set) -> bool:
    for x in a:
        for y in b:
            if abs(x - y) <= 1e-6 * max(1.0, abs(x), abs(y)):
                return True
    return False


# ---------------------------------------------------------------- transcript index
class TranscriptIndex:
    """Normalised transcript with offset -> segment -> time mapping and a bigram index."""

    def __init__(self, transcript, duration: float | None = None):
        self.starts: list[float] = []  # segment start times
        self.offsets: list[int] = []  # char offset of each segment in self.full
        self.seg_numbers: list[list[set]] = []
        parts, off, last_t = [], 0, 0.0
        for seg in transcript if isinstance(transcript, list) else []:
            if not isinstance(seg, dict):
                continue
            t = parse_time(seg.get("t"))
            t = last_t if t is None else t
            last_t = t
            txt = str(seg.get("text") or "")
            n = norm(txt)
            self.starts.append(t)
            self.offsets.append(off)
            self.seg_numbers.append([x["values"] for x in extract_numbers(txt)])
            parts.append(n)
            off += len(n)
        self.full = "".join(parts)
        end = self.starts[-1] + 60.0 if self.starts else 0.0
        if isinstance(duration, (int, float)) and duration > end - 60.0:
            end = max(end - 60.0, float(duration))
        self.ends = self.starts[1:] + [end]
        self._bigrams: dict[str, list[int]] | None = None

    def __contains__(self, term: str) -> bool:
        n = norm(term)
        return bool(n) and n in self.full

    def time_at(self, offset: int) -> float | None:
        if not self.offsets:
            return None
        i = bisect.bisect_right(self.offsets, offset) - 1
        return self.starts[max(0, i)]

    def _index(self) -> dict[str, list[int]]:
        if self._bigrams is None:
            idx: dict[str, list[int]] = {}
            f = self.full
            for i in range(len(f) - 1):
                idx.setdefault(f[i:i + 2], []).append(i)
            self._bigrams = idx
        return self._bigrams

    def locate(self, quote: str) -> tuple[float, float | None, str]:
        """Best fuzzy match of quote: (ratio 0..1, time of the match, matched text)."""
        q = norm(quote)
        if not q:
            return 0.0, None, ""
        pos = self.full.find(q)
        if pos >= 0:
            return 1.0, self.time_at(pos), q
        L = len(q)
        if L < 4:
            return 0.0, None, ""
        idx = self._index()
        votes: Counter = Counter()
        for k in range(L - 1):
            for p in idx.get(q[k:k + 2], ()):
                votes[p - k] += 1
        best = (0.0, None, "")
        sm = difflib.SequenceMatcher(None, autojunk=False)
        sm.set_seq2(q)
        for start, _ in votes.most_common(12):
            for d in (-2, -1, 0, 1, 2):
                for e in (-2, -1, 0, 1, 2):
                    a = max(0, start + d)
                    w = self.full[a:a + L + e]
                    if not w:
                        continue
                    sm.set_seq1(w)
                    if sm.real_quick_ratio() <= best[0] or sm.quick_ratio() <= best[0]:
                        continue
                    r = sm.ratio()
                    if r > best[0]:
                        best = (r, self.time_at(a), w)
        return best

    def numbers_near(self, anchors: list[float], window: float = NUMBER_WINDOW_SEC) -> list[set]:
        if not anchors:
            return [v for seg in self.seg_numbers for v in seg]
        out = []
        for s, e, nums in zip(self.starts, self.ends, self.seg_numbers):
            if any(s <= a + window and e >= a - window for a in anchors):
                out.extend(nums)
        return out


# ---------------------------------------------------------------- names
_SURNAMES = set(
    "王李張劉陳楊黃趙吳周徐孫朱胡郭何林羅高梁鄭謝宋唐許韓馮鄧曹彭曾蕭蔡潘杜戴夏鍾汪"
    "田任姜范方石姚譚廖鄒熊陸郝孔崔康毛邱秦史顧侯邵孟龍萬段雷錢湯尹黎易常武喬賀賴龔"
    "文盧簡游柯魏翁施詹余葉洪呂蘇沈莊江涂連童傅歐卓溫邢藍嚴董薛"
)
_SURNAMES -= set("文方常石田任萬龍易武史")  # too common as ordinary characters
_TITLES = (
    "會計師", "律師", "建築師", "醫師", "教授", "博士", "老師", "先生", "小姐", "女士", "太太",
    "董事長", "副董事長", "總經理", "副總經理", "副總", "執行長", "財務長", "總裁", "創辦人", "董事", "經理", "協理",
    "署長", "部長", "次長", "局長", "司長", "處長", "院長", "市長", "縣長", "主委", "委員", "立委", "議員",
    "總統", "副總統", "主席", "法官", "檢察官", "主任", "科長", "組長", "秘書長",
)
_TITLE_ALT = "|".join(sorted(map(re.escape, _TITLES), key=len, reverse=True))
_NAME_STOP = set("的是在和與及為由之等各說表指認提也則曾已將會對向並或而但就都還要讓把被從於這那其該此本某")
_NAME_AFTER = re.compile(rf"([一-鿿]{{2,3}})(?={_TITLE_ALT})")
_NAME_BEFORE = re.compile(rf"(?:{_TITLE_ALT})([一-鿿]{{2,3}})")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]*[A-Za-z0-9]|[A-Za-z]{2,}")
_LATIN_IGNORE = {"dri", "tbd", "qa", "q", "a", "vs", "or", "of", "and", "the", "to", "in", "on", "for",
                 "by", "with", "is", "etc", "ok", "eg", "ie"}
_QUOTED = re.compile(r"「([^「」]{2,40})」")


def _person_candidates(text: str) -> list[tuple[str, list[str]]]:
    """[(shown name, accepted spellings)] for title-anchored person names."""
    out = []
    s = _nfkc(text)
    for m in _NAME_AFTER.finditer(s):  # 許崑源會計師: the 2–3 chars right before the title
        cand = m.group(1)
        # try the longest tail that starts with a surname and has no function characters
        for k in (3, 2):
            tail = cand[-k:] if len(cand) >= k else ""
            if tail and tail[0] in _SURNAMES and not (set(tail) & _NAME_STOP):
                out.append((tail, [tail]))
                break
    for m in _NAME_BEFORE.finditer(s):  # 署長宋秀玲: the greedy 3 chars may include a verb
        cand = m.group(1)
        if cand[0] not in _SURNAMES:
            continue
        forms = [f for f in (cand[:3], cand[:2]) if len(f) >= 2 and not (set(f) & _NAME_STOP)]
        if forms:
            out.append((forms[0], forms))
    return out


def _name_problems(text: str, tx: TranscriptIndex, glossary: list[str], rights: list[str]) -> list[str]:
    missing: list[str] = []
    nt = norm(text)

    def miss(term: str):
        if term not in missing:
            missing.append(term)

    for term in list(glossary) + list(rights):
        n = norm(term)
        if len(n) >= 2 and n in nt and n not in tx.full:
            miss(term)
    for q in _QUOTED.findall(_nfkc(text)):
        n = norm(q)
        if len(n) < 2 or n in tx.full:
            continue
        if len(n) >= 8 and tx.locate(q)[0] >= QUOTE_MIN_RATIO:
            continue
        miss(q)
    for w in _LATIN.findall(_nfkc(text)):
        if w.lower() in _LATIN_IGNORE or len(w) < 2:
            continue
        if w.lower() not in tx.full:
            miss(w)
    for shown, forms in _person_candidates(text):
        if not any(norm(f) in tx.full for f in forms):
            miss(shown)
    return missing


# ---------------------------------------------------------------- calc
_CNUM = r"\d+(?:\.\d+)?(?:[萬億兆千百])?%?"
_MUL = r"\s*(?:[×xX*＊]|乘以|乘上)\s*"
_EQ = r"\s*(?:[=＝≈]|等於)\s*(?:約|大約)?\s*"
_CALC = re.compile(rf"((?:{_CNUM}{_MUL})+{_CNUM}){_EQ}({_CNUM})")
_UNIT = {"萬": 1e4, "億": 1e8, "兆": 1e12, "千": 1e3, "百": 1e2}


def _calc_value(tok: str) -> tuple[float, str]:
    pct = tok.endswith("%")
    t = tok.rstrip("%")
    unit = t[-1] if t and t[-1] in _UNIT else ""
    v = float(t[:-1] if unit else t) * (_UNIT[unit] if unit else 1.0)
    return (v / 100.0 if pct else v), ("%" if pct else unit)


def _fmt(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def calc_problems(text: str) -> list[tuple[str, str]]:
    """[(expression as written, corrected expression)] for wrong a×b=c."""
    out = []
    s = _nfkc(text)
    for m in _CALC.finditer(s):
        lhs, rhs = m.group(1), m.group(2)
        try:
            factors = [_calc_value(f) for f in re.split(_MUL, lhs) if f]
            res, unit = _calc_value(rhs)
        except ValueError:
            continue
        prod = 1.0
        for v, _ in factors:
            prod *= v
        approx = "約" in m.group(0) or "≈" in m.group(0)
        tol = 0.02 if approx else 0.005
        if abs(prod - res) <= tol * max(abs(prod), abs(res), 1e-12):
            continue
        shown = prod * 100 if unit == "%" else prod / (_UNIT.get(unit, 1.0))
        out.append((m.group(0).strip(), f"{lhs}={_fmt(shown)}{unit}"))
    return out


# ---------------------------------------------------------------- review items
def review_item(kind: str, section: str, item_id: str | None, text: str, detail: str,
                suggestion: str | None = None, t: float | None = None) -> dict:
    """Build a ReviewItem with the stable id hash(kind+section+itemId+text)."""
    r = {"id": hash8(f"{kind}|{section}|{item_id or ''}|{text}"), "kind": kind, "section": section,
         "text": text, "detail": detail, "state": "pending"}
    if item_id:
        r["itemId"] = item_id
    if suggestion:
        r["suggestion"] = suggestion
    if isinstance(t, (int, float)):
        r["t"] = float(t)
    return r


def merge_review(prev, new_items: list[dict]) -> list[dict]:
    """New items carry over the state (and edited text) of same-id items already handled."""
    old = {}
    for r in prev if isinstance(prev, list) else []:
        if isinstance(r, dict) and r.get("id"):
            old[r["id"]] = r
    out, seen = [], set()
    for r in new_items:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        o = old.get(r["id"])
        if o and o.get("state") in KEEP_STATES:
            r = {**r, "state": o["state"]}
        out.append(r)
    return out


def pending_count(review) -> int:
    items = review.get("items") if isinstance(review, dict) else review
    return sum(1 for r in items or [] if isinstance(r, dict) and r.get("state") == "pending")


def _glossary_list(glossary) -> list[str]:
    if isinstance(glossary, str):
        glossary = glossary.splitlines()
    return [g.strip() for g in glossary or [] if isinstance(g, str) and g.strip()]


def _hms(t: float) -> str:
    s = int(t)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _is_legacy(raw) -> bool:
    """Legacy entries never had sources: plain strings, or old notes objects without a src key."""
    return isinstance(raw, str) or (isinstance(raw, dict) and "src" not in raw)


def check_entry(section: str, item_id: str, text: str, src: list, tx: TranscriptIndex,
                glossary: list[str], rights: list[str], *, legacy: bool) -> list[dict]:
    out = []
    anchors = [s["t"] for s in src if isinstance(s.get("t"), (int, float))]
    t0 = anchors[0] if anchors else None

    # quote
    bad = []
    for s in src:
        q = s.get("quote") or ""
        if not norm(q):
            continue
        ratio, at, near = tx.locate(q)
        if ratio >= QUOTE_MIN_RATIO:
            if at is not None:
                anchors.append(at)
        else:
            bad.append((q, ratio, near))
    if bad:
        detail = "；".join(
            f"原句「{q}」在逐字稿裡找不到（最接近的相似度 {round(r * 100)}%"
            + (f"：「{n}」" if r >= 0.5 and n else "") + "）" for q, r, n in bad)
        out.append(review_item("quote", section, item_id, text, detail, t=t0))
    elif not legacy and not any(norm(s.get("quote") or "") for s in src):
        out.append(review_item("quote", section, item_id, text,
                               "這一條沒有附逐字稿原句，無法確認出處。", t=t0))
    if t0 is None and anchors:
        t0 = anchors[0]

    # number
    near = tx.numbers_near(anchors)
    missing = []
    for n in extract_numbers(text, strict=True):
        if not any(_num_match(n["values"], v) for v in near) and n["raw"] not in missing:
            missing.append(n["raw"])
    if missing:
        where = f"對應段落（原句前後 {int(NUMBER_WINDOW_SEC)} 秒）的逐字稿" if anchors else "逐字稿"
        out.append(review_item("number", section, item_id, text,
                               f"數字「{'、'.join(missing)}」在{where}裡找不到，請對照錄音確認。", t=t0))

    # name
    names = _name_problems(text, tx, glossary, rights)
    if names:
        out.append(review_item("name", section, item_id, text,
                               f"名稱「{'、'.join(names)}」在逐字稿裡沒有出現，可能是 AI 自行補上或改寫。", t=t0))

    # calc
    calcs = calc_problems(text)
    if calcs:
        detail = "；".join(f"算式「{e}」驗算不符，應為「{fix}」" for e, fix in calcs)
        out.append(review_item("calc", section, item_id, text, detail,
                               suggestion=text.replace(calcs[0][0], calcs[0][1]) if len(calcs) == 1 else None,
                               t=t0))
    return out


def term_items(corrections) -> list[dict]:
    """Pending (medium/low) corrections -> kind "term" items: text = wrong, suggestion = right."""
    out = []
    items = corrections.get("items") if isinstance(corrections, dict) else None
    for c in items or []:
        if not isinstance(c, dict) or c.get("state") != "pending":
            continue
        wrong, right = str(c.get("wrong") or ""), str(c.get("right") or "")
        if not wrong or not right:
            continue
        conf = {"high": "高", "medium": "中", "low": "低"}.get(c.get("confidence"), "未知")
        ts = [t for t in (parse_time(x) for x in c.get("ts") or []) if t is not None]
        cnt = c.get("count")
        detail = (f"語音辨識可能聽錯：「{wrong}」→「{right}」（信心：{conf}"
                  + (f"，出現 {cnt} 次" if isinstance(cnt, int) else "")
                  + (f"，例如 {_hms(ts[0])}" if ts else "") + "）。確認或改正後會套用到逐字稿。")
        out.append(review_item("term", "transcript", None, wrong, detail, suggestion=right,
                               t=ts[0] if ts else None))
    return out


def verify_meeting(meeting: dict, glossary=None, extra: list[dict] | None = None) -> list[dict]:
    """All ReviewItems for a meeting, merged with meeting.review (handled states kept).

    glossary: list or newline string (settings.glossary). extra: items produced elsewhere
    (e.g. kind "slip" from the SLIPS prompt). Kinds this module computes are always recomputed;
    previous items of other kinds are kept unless `extra` brings that kind again.
    """
    tx = TranscriptIndex(meeting.get("transcript"), meeting.get("durationSec"))
    gl = _glossary_list(glossary)
    corr = meeting.get("corrections") if isinstance(meeting.get("corrections"), dict) else {}
    rights = [str(c.get("right")) for c in corr.get("items") or [] if isinstance(c, dict) and c.get("right")]

    new: list[dict] = []
    for section, raw, item_id, text, src in iter_items(meeting):
        if not str(text).strip():
            continue
        new.extend(check_entry(section, item_id, text, src, tx, gl, rights, legacy=_is_legacy(raw)))
    new.extend(term_items(corr))

    extra = [r for r in extra or [] if isinstance(r, dict) and r.get("id")]
    extra_kinds = {r.get("kind") for r in extra}
    prev_review = meeting.get("review") if isinstance(meeting.get("review"), dict) else {}
    prev = prev_review.get("items") or []
    carried = [r for r in prev if isinstance(r, dict) and r.get("kind") not in VERIFY_KINDS
               and r.get("kind") not in extra_kinds]
    return merge_review(prev, new + extra) + [r for r in carried
                                               if r.get("id") not in {x["id"] for x in new + extra}]
