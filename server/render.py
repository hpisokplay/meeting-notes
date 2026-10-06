"""Render a meeting to Markdown / Word. Tolerates legacy transcript timestamps."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .gaps import gaps_line, meeting_gaps
from .items import item_text

_BAD_XML = re.compile('[^' + chr(9) + chr(10) + chr(13) + chr(0x20) + '-' + chr(0xD7FF) + chr(0xE000) + '-' + chr(0xFFFD) + chr(0x10000) + '-' + chr(0x10FFFF) + ']')


def _x(text) -> str:
    """Strip characters that are invalid in XML 1.0 (python-docx would raise)."""
    return _BAD_XML.sub("", str(text))


TAIPEI = ZoneInfo("Asia/Taipei")


def _date(m: dict) -> str:
    ts = m.get("createdAt")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return ""
    return datetime.fromtimestamp(ts / 1000, TAIPEI).strftime("%Y-%m-%d %H:%M")


def _fmt_dur(sec) -> str:
    if isinstance(sec, bool) or not isinstance(sec, (int, float)) or sec <= 0:
        return ""
    sec = int(sec)
    h, r = divmod(sec, 3600)
    mi, s = divmod(r, 60)
    return f"{h} 小時 {mi} 分" if h else f"{mi} 分 {s} 秒"


def _hms(t) -> str:
    """Numeric seconds -> HH:MM:SS; anything else (legacy 'MM:SS', absent) -> ''."""
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return ""
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _group_name(m: dict) -> str:
    g = m.get("group")
    return g if isinstance(g, str) and g else ""


_LECTURE = (("conclusions", "核心結論"), ("advice", "講者建議"), ("risks", "風險提醒"),
            ("cases", "案例"), ("mainPoints", "重點"))
_MEETING = (("actionItems", "待辦"), ("mainPoints", "重點"))


def _split_qa(x) -> tuple[str, str]:
    """Legacy {q, a} dict, Item / string "問：…<newline>答：…" (or "問：… 答：…") -> (q, a)."""
    if isinstance(x, dict) and ("q" in x or "a" in x) and not isinstance(x.get("text"), str):
        return str(x.get("q") or ""), str(x.get("a") or "")
    t = item_text(x).strip()
    m = re.match(r"^(?:問|Q)[：:]\s*(.*?)\s*(?:答|A)[：:]\s*(.*)$", t, re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return t, ""


def _sections(m: dict):
    """-> ([(heading, [texts])], [(q, a)]) for the meeting's mode (legacy / absent = meeting)."""
    s = m.get("summary") if isinstance(m.get("summary"), dict) else {}
    keys = _LECTURE if m.get("mode") == "lecture" else _MEETING
    secs = [(title, [item_text(x) for x in s.get(k) or [] if item_text(x).strip()]) for k, title in keys]
    qa = [_split_qa(x) for x in s.get("qa") or []]
    return secs, [(q, a) for q, a in qa if q or a]


def _lines(m: dict):
    for seg in m.get("transcript") or []:
        if not isinstance(seg, dict):
            continue
        t = _hms(seg.get("t"))
        yield (f"[{t}] " if t else "") + f"{seg.get('speaker') or ''}: {seg.get('text') or ''}"


def meeting_to_markdown(m: dict) -> str:
    secs, qa = _sections(m)
    out = [f"# {m.get('title') or '未命名會議'}", "", f"日期：{_date(m)}（台北）",
           f"分類：{_group_name(m)}", f"長度：{_fmt_dur(m.get('durationSec'))}", ""]
    gl = gaps_line(meeting_gaps(m))
    if gl:
        out += [f"> {gl}", ""]
    warns = [w for w in m.get("warnings") or [] if isinstance(w, str) and w]
    if warns:
        out += [f"> ⚠️ {w}" for w in warns] + [""]
    for title, xs in secs:
        out += [f"## {title}", *[f"- {x}" for x in xs], ""]
    out += ["## Q&A"]
    for q, a in qa:
        out += [f"**Q：{q}**", f"A：{a}", ""]
    out += ["", "## 逐字稿", *_lines(m), ""]
    return "\n".join(out)


def meeting_to_docx(m: dict, path: Path) -> None:
    from docx import Document

    secs, qa = _sections(m)
    d = Document()
    d.add_heading(_x(m.get("title") or "未命名會議"), 0)
    d.add_paragraph(f"日期：{_date(m)}（台北）")
    d.add_paragraph(_x(f"分類：{_group_name(m)}"))
    d.add_paragraph(f"長度：{_fmt_dur(m.get('durationSec'))}")
    gl = gaps_line(meeting_gaps(m))
    if gl:
        d.add_paragraph(_x(gl))
    for w in m.get("warnings") or []:
        if isinstance(w, str) and w:
            d.add_paragraph(_x(f"⚠️ {w}"))
    for title, xs in secs:
        d.add_heading(title, 1)
        for x in xs:
            d.add_paragraph(_x(x), style="List Bullet")
    d.add_heading("Q&A", 1)
    for q, a in qa:
        d.add_paragraph().add_run(_x(f"Q：{q}")).bold = True
        d.add_paragraph(_x(f"A：{a}"))
    d.add_heading("逐字稿", 1)
    for line in _lines(m):
        d.add_paragraph(_x(line))
    d.save(str(path))
