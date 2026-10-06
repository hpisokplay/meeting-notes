"""Pure functions: word/speaker alignment, segment building, cleanup. No GPU, no network."""
import bisect
import logging
import re

PROMPT = "以下是繁體中文的會議。"

SENTENCE_END = tuple("。！？!?；;，,…")


def align_words(words, turns):
    """words: [(start,end,text)], turns: [{"s","e","spk"}].
    Speaker by max overlap, else nearest turn. Returns [(start,end,text,speaker)].
    O((W+T) log T): turns sorted by start, bisect + prefix-max-end lookback."""
    ts = sorted(turns, key=lambda t: (t["s"], t["e"]))
    starts = [t["s"] for t in ts]
    pm = []  # prefix max of end, and which turn index holds it
    pmi = []
    best_e, best_i = float("-inf"), -1
    for i, t in enumerate(ts):
        if t["e"] > best_e:
            best_e, best_i = t["e"], i
        pm.append(best_e)
        pmi.append(best_i)
    out = []
    for a, b, w in words:
        spk = None
        if ts:
            idx = bisect.bisect_left(starts, b) - 1  # last turn starting before word end
            best_o = 0.0
            i = idx
            lo = max(-1, idx - 256)  # cap: pathological spanning turns must not make this O(T)
            while i > lo and pm[i] > a:
                o = min(b, ts[i]["e"]) - max(a, ts[i]["s"])
                if o > best_o:
                    spk, best_o = ts[i]["spk"], o
                i -= 1
            if spk is None:  # no overlap: nearest turn
                prev_d = (a - pm[idx]) if idx >= 0 else float("inf")
                nxt_d = (starts[idx + 1] - b) if idx + 1 < len(ts) else float("inf")
                spk = ts[pmi[idx]]["spk"] if prev_d <= nxt_d else ts[idx + 1]["spk"]
        else:
            spk = "SPEAKER_00"
        out.append((a, b, w, spk))
    return out


def build_segments(aligned, gap=2.0, max_chars=60):
    """Merge consecutive words of the same speaker (gap < `gap` s) into lines; once a
    line holds >= max_chars, start a new one at the next sentence punctuation."""
    segs = []
    cur = None
    last_end = 0.0
    split_next = False
    for a, b, w, spk in aligned:
        if (cur is None or cur["speaker"] != spk or a - last_end >= gap or split_next
                or len(cur["text"]) >= max_chars * 3):
            cur = {"speaker": spk, "text": "", "t": a}
            segs.append(cur)
        cur["text"] += w
        last_end = b
        split_next = len(cur["text"]) >= max_chars and w.rstrip().endswith(SENTENCE_END)
    for s in segs:
        s["text"] = s["text"].strip()
    return [s for s in segs if s["text"]]


def _norm(text):
    return re.sub(r"[\s。，、！？!?,.;；:：「」『』\"'…\-]", "", text)


_P = r"[^，。！？!?,.]"
HALLU_RES = [re.compile(p, re.I) for p in (
    r"(中文)?字幕由.*?(提供|製作)",
    r"字幕志願者" + _P + "*",
    r"Amara(\s*\.\s*org)?(\s*社群(提供)?)?",
    r"請不吝點贊" + _P + "*",
    r"謝謝\s*(觀看|收看)(本期)?(節目|影片)?",
    r"訂閱" + _P + r"{0,10}(頻道|按讚)" + _P + "*",
    r"中文字幕",
    r"字幕由",
)]
SHORT_LIMIT = 20


def _strip_hallu(text):
    hit = False
    for rx in HALLU_RES:
        text, n = rx.subn("", text)
        hit = hit or n > 0
    return text, hit


def _strip_prompt(text, prompt):
    if len(prompt) < 6:
        return text
    for k in range(len(prompt), 5, -1):
        if prompt[:k] in text:
            return text.replace(prompt[:k], "")
    return text


def filter_hallucinations(segs, prompt):
    """Remove Whisper hallucinations/prompt echoes without eating real content;
    collapse >=3 identical consecutive repeats."""
    kept = []
    for s in segs:
        txt = s["text"]
        short = len(_norm(txt)) <= SHORT_LIMIT
        stripped, hit = _strip_hallu(txt)
        stripped = _strip_prompt(stripped, prompt)
        if not _norm(stripped):
            continue  # whole segment was hallucination / echo
        if hit and short:
            continue  # short segment containing a hallucination marker
        if stripped != txt:
            stripped = stripped.strip(" 	，,、")
            s = {**s, "text": stripped}
        kept.append(s)
    out = []
    i = 0
    while i < len(kept):
        j = i
        while j + 1 < len(kept) and kept[j + 1]["text"] == kept[i]["text"]:
            j += 1
        if j - i + 1 >= 3:
            out.append(kept[i])
        else:
            out.extend(kept[i:j + 1])
        i = j + 1
    return out


HOTWORD_BUDGET = 60  # CJK chars + ASCII words; faster-whisper truncates hotwords at ~223 tokens
_log = logging.getLogger("worker")
_WORD_UNITS = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_+]*|[^\sA-Za-z0-9]")


def _units(term):
    return len(_WORD_UNITS.findall(term))


def build_hotwords(glossary):
    """Glossary list -> faster-whisper `hotwords` string (None when empty).
    Terms are kept in order until the size budget is reached; the rest are dropped (warned)."""
    terms = [t.strip() for t in (glossary or []) if isinstance(t, str) and t.strip()]
    kept, used = [], 0
    for t in terms:
        u = _units(t)
        if used + u > HOTWORD_BUDGET:
            break
        kept.append(t)
        used += u
    if len(kept) < len(terms):
        _log.warning("glossary too long for hotwords: dropped %d of %d terms", len(terms) - len(kept), len(terms))
    return " ".join(kept) or None


_NUM_BEFORE = "一二兩三四五六七八九十幾每這那半"
_TW_FIX = re.compile(r"(?<![" + _NUM_BEFORE + r"])隻(?=有|是|要|能|好)")


def fix_traditional(text):
    """Post-fix s2twp over-conversion of 只 -> 隻, unless preceded by a numeral/quantifier
    (「一隻」「兩隻好狗」「三隻有」 untouched)."""
    return _TW_FIX.sub("只", text)


def _join(a, b):
    if a and b and a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum():
        return a + " " + b
    return a + b


def smooth_speakers(segs, max_sec=3.0, max_chars=8):
    """A run of one other speaker sandwiched between the same speaker A on both sides,
    shorter than max_sec AND <= max_chars, is reassigned to A; adjacent same-speaker
    segments are then re-merged. Segments need speaker/text/t (start seconds).

    Trade-off: a real interjection of <= max_chars lasting < max_sec between two segments of
    the same speaker is absorbed too. Accepted, because diarization flicker in lectures is
    far more common than genuine one-word interjections."""
    segs = [dict(s) for s in segs]
    n = len(segs)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and segs[j + 1]["speaker"] == segs[i]["speaker"]:
            j += 1
        if (i > 0 and j + 1 < n and segs[i - 1]["speaker"] == segs[j + 1]["speaker"]
                and segs[i - 1]["speaker"] != segs[i]["speaker"]):
            dur = segs[j + 1]["t"] - segs[i]["t"]
            chars = sum(len(s["text"].strip()) for s in segs[i:j + 1])
            if dur < max_sec and chars <= max_chars:
                for k in range(i, j + 1):
                    segs[k]["speaker"] = segs[i - 1]["speaker"]
        i = j + 1
    out = []
    for s in segs:
        if out and out[-1]["speaker"] == s["speaker"]:
            out[-1]["text"] = _join(out[-1]["text"], s["text"])
        else:
            out.append(s)
    return out


def name_speakers(segs):
    """SPEAKER_xx -> 說話者 N by first appearance."""
    names = {}
    out = []
    for s in segs:
        spk = s["speaker"]
        if spk not in names:
            names[spk] = "說話者 %d" % (len(names) + 1)
        out.append({**s, "speaker": names[spk]})
    return out
