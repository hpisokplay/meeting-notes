"""Pure functions: word/speaker alignment, segment building, cleanup. No GPU, no network."""
import re

PROMPT = "以下是繁體中文的會議。"

SENTENCE_END =tuple("。！？!?；;，,…")

HALLUCINATIONS = ("Amara", "字幕志願者", "字幕由", "中文字幕", "請不吝點贊",
                  "訂閱", "謝謝觀看", "謝謝收看")


def align_words(words, turns):
    """words: [(start,end,text)], turns: [{"s","e","spk"}].
    Speaker by max overlap, else nearest turn. Returns [(start,end,text,speaker)]."""
    out = []
    for a, b, w in words:
        best, best_o = None, 0.0
        for t in turns:
            o = min(b, t["e"]) - max(a, t["s"])
            if o > best_o:
                best, best_o = t["spk"], o
        if best is None:
            if turns:
                best = min(turns, key=lambda t: min(abs(t["s"] - b), abs(t["e"] - a)))["spk"]
            else:
                best = "SPEAKER_00"
        out.append((a, b, w, best))
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


def filter_hallucinations(segs, prompt):
    """Drop known Whisper hallucinations + prompt echoes, collapse >=3 identical repeats."""
    pn = _norm(prompt)
    prefix = prompt[:6] if len(prompt) >= 6 else None
    kept = []
    for s in segs:
        txt = s["text"]
        if any(h in txt for h in HALLUCINATIONS):
            continue
        n = _norm(txt)
        if not n:
            continue
        if prompt and prompt in txt:
            continue
        if prefix and prefix in txt:
            continue
        if len(n) >= 4 and n in pn:  # echo of (part of) the prompt
            continue
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


def offset_parts(parts, durations):
    """Shift each part's segment times by the summed duration of the preceding parts."""
    out = []
    base = 0.0
    for segs, dur in zip(parts, durations):
        for s in segs:
            out.append({**s, "t": round(s["t"] + base, 3)})
        base += dur
    return out
