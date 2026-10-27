"""Render a meeting to Markdown / Word. Tolerates legacy transcript timestamps."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .gaps import gaps_line, meeting_gaps
from .items import item_text
from .report import has_report

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


def _roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
                 (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out += r
            n -= v
    return out


def _report_outline(m: dict):
    """-> (intro, [(heading, [(title, text)])]); same rules as JS reportOutline."""
    s = m.get("summary") if isinstance(m.get("summary"), dict) else {}
    intro_arr = s.get("reportIntro") if isinstance(s.get("reportIntro"), list) else []
    intro = item_text(intro_arr[0]) if intro_arr else ""
    points = s.get("reportPoints") if isinstance(s.get("reportPoints"), list) else []
    has_group = any(isinstance(x, dict) and x.get("group") for x in points)
    order: list[str] = []
    by: dict[str, list] = {}
    for x in points:
        g = ((x.get("group") if isinstance(x, dict) else "") or "其他") if has_group else ""
        full = item_text(x)
        i = full.find("：")
        sub = ("", full) if i < 0 else (full[:i], full[i + 1:])
        if g not in by:
            by[g] = []
            order.append(g)
        by[g].append(sub)
    return intro, [(g, by[g]) for g in order]


_REPORT_LECTURE = _LECTURE[:4]  # 核心結論／講者建議／風險提醒／案例: kept after the report body for lectures


def _report_lecture(m: dict):
    """Lecture meeting with a report: [(heading, [texts])] for the four lecture sections (empty ones dropped)."""
    if m.get("mode") != "lecture":
        return []
    s = m.get("summary") if isinstance(m.get("summary"), dict) else {}
    secs = [(title, [item_text(x) for x in s.get(k) or [] if item_text(x).strip()]) for k, title in _REPORT_LECTURE]
    return [(t, xs) for t, xs in secs if xs]


def _report_blocks(m: dict):
    """Printable pieces: (intro, [(numbered heading or '', [(title, text)])], action texts, [(q, a)])."""
    intro, secs = _report_outline(m)
    out, n = [], 0
    for h, subs in secs:
        h = h.strip()
        subs = [(t.strip(), x.strip()) for t, x in subs]
        subs = [(t, x) for t, x in subs if t or x]
        if h:
            n += 1
        out.append((f"{n}. {h}" if h else "", subs))
    s = m.get("summary") if isinstance(m.get("summary"), dict) else {}
    actions = [item_text(x) for x in s.get("actionItems") or [] if item_text(x).strip()]
    qa = [_split_qa(x) for x in s.get("qa") or []]
    return intro.strip(), out, actions, [(q, a) for q, a in qa if q or a]


def _markdown_report(m: dict, out: list) -> list:
    intro, secs, actions, qa = _report_blocks(m)
    if intro:
        out += [intro, ""]
    for h, subs in secs:
        if h:
            out += [f"## {h}"]
        out += [f"- **{t}：**{x}" if t else f"- {x}" for t, x in subs]
        out += [""]
    for title, xs in _report_lecture(m):
        out += [f"## {title}", *[f"{i}. {x}" for i, x in enumerate(xs, 1)], ""]
    if actions:
        out += ["## Action Items", *[f"{_roman(i)}. {x}" for i, x in enumerate(actions, 1)], ""]
    if qa:
        out += ["## Q&A"]
        for i, (q, a) in enumerate(qa, 1):
            out += [f"{_roman(i)}. **Q：{q}**", f"   A：{a}", ""]
    return out


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
    if has_report(m.get("summary")):
        out = _markdown_report(m, out)
        out += ["## 逐字稿", *_lines(m), ""]
        return "\n".join(out)
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
    if has_report(m.get("summary")):
        intro, rsecs, actions, rqa = _report_blocks(m)
        if intro:
            d.add_paragraph(_x(intro))
        for h, subs in rsecs:
            if h:
                d.add_heading(_x(h), 1)
            for t, x in subs:
                p = d.add_paragraph(style="List Bullet")
                if t:
                    p.add_run(_x(f"{t}：")).bold = True
                p.add_run(_x(x))
        for title, xs in _report_lecture(m):
            d.add_heading(title, 1)
            for i, x in enumerate(xs, 1):
                d.add_paragraph(_x(f"{i}. {x}"))
        if actions:
            d.add_heading("Action Items", 1)
            for i, x in enumerate(actions, 1):
                d.add_paragraph(_x(f"{_roman(i)}. {x}"))
        if rqa:
            d.add_heading("Q&A", 1)
            for i, (q, a) in enumerate(rqa, 1):
                d.add_paragraph().add_run(_x(f"{_roman(i)}. Q：{q}")).bold = True
                d.add_paragraph(_x(f"A：{a}"))
    else:
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
