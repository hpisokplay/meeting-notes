"""Unrecognised stretches of the recording (word-review spec, 「未辨識段落」).

meeting.gaps = [{start, end}] (seconds): stretches longer than GAP_S with no transcript —
before the first segment, between consecutive segments and after the last one (vs durationSec).
Segments usually carry only their start t, so a segment's end is estimated as
t + max(2, len(text) / 5) (about 5 characters per second of speech) unless it has a numeric `end`.
"""
from __future__ import annotations

from .items import parse_time

GAP_S = 20.0
MAX_LISTED = 10
CHARS_PER_S = 5.0


def _end(seg: dict, t: float) -> float:
    e = seg.get("end")
    if isinstance(e, (int, float)) and not isinstance(e, bool) and e >= t:
        return float(e)
    return t + max(2.0, len(str(seg.get("text") or "")) / CHARS_PER_S)


def compute_gaps(transcript, duration=None) -> list[dict]:
    segs = []
    for s in transcript if isinstance(transcript, list) else []:
        if isinstance(s, dict):
            t = parse_time(s.get("t"))
            if t is not None:
                segs.append((t, _end(s, t)))
    if not segs:
        return []
    segs.sort()
    out = []
    if segs[0][0] > GAP_S:
        out.append({"start": 0.0, "end": round(segs[0][0], 2)})
    end = segs[0][1]
    for t, e in segs[1:]:
        if t - end > GAP_S:
            out.append({"start": round(end, 2), "end": round(t, 2)})
        end = max(end, e)
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration - end > GAP_S:
        out.append({"start": round(end, 2), "end": round(float(duration), 2)})
    return out


def _hms(t: float) -> str:
    s = int(t)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def gaps_line(gaps) -> str:
    """「⚠️ 約 X 分鐘未辨識：HH:MM:SS–HH:MM:SS、…」 (at most MAX_LISTED stretches), or ""."""
    gs = [g for g in gaps if isinstance(g, dict) and isinstance(g.get("start"), (int, float))
          and isinstance(g.get("end"), (int, float))] if isinstance(gaps, list) else []
    if not gs:
        return ""
    total = sum(max(0.0, g["end"] - g["start"]) for g in gs)
    mins = f"{total / 60:.1f}".rstrip("0").rstrip(".")
    listed = "、".join(f"{_hms(g['start'])}–{_hms(g['end'])}" for g in gs[:MAX_LISTED])
    more = f"…等共 {len(gs)} 段" if len(gs) > MAX_LISTED else ""
    return f"⚠️ 約 {mins} 分鐘未辨識：{listed}{more}"


def meeting_gaps(m: dict) -> list[dict]:
    g = m.get("gaps")
    if isinstance(g, list):
        return g
    return compute_gaps(m.get("transcript"), m.get("durationSec"))
