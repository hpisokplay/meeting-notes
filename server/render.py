"""Render a meeting to Markdown / Word. Tolerates legacy transcript timestamps."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


def _item_text(x) -> str:
    if isinstance(x, str):
        return x
    return x.get("text", "") if isinstance(x, dict) else str(x)


def _sections(m: dict):
    s = m.get("summary") or {}
    todo = [_item_text(x) for x in s.get("actionItems") or []]
    pts = [_item_text(x) for x in s.get("mainPoints") or []]
    qa = [(q.get("q", ""), q.get("a", "")) for q in s.get("qa") or [] if isinstance(q, dict)]
    return todo, pts, qa


def _lines(m: dict):
    for seg in m.get("transcript") or []:
        if not isinstance(seg, dict):
            continue
        t = _hms(seg.get("t"))
        yield (f"[{t}] " if t else "") + f"{seg.get('speaker') or ''}: {seg.get('text') or ''}"


def meeting_to_markdown(m: dict) -> str:
    todo, pts, qa = _sections(m)
    out = [f"# {m.get('title') or '未命名會議'}", "", f"日期：{_date(m)}（台北）",
           f"分類：{_group_name(m)}", f"長度：{_fmt_dur(m.get('durationSec'))}", ""]
    out += ["## 待辦", *[f"- {x}" for x in todo], ""]
    out += ["## 重點", *[f"- {x}" for x in pts], ""]
    out += ["## Q&A"]
    for q, a in qa:
        out += [f"**Q：{q}**", f"A：{a}", ""]
    out += ["", "## 逐字稿", *_lines(m), ""]
    return "\n".join(out)


def meeting_to_docx(m: dict, path: Path) -> None:
    from docx import Document

    todo, pts, qa = _sections(m)
    d = Document()
    d.add_heading(m.get("title") or "未命名會議", 0)
    d.add_paragraph(f"日期：{_date(m)}（台北）")
    d.add_paragraph(f"分類：{_group_name(m)}")
    d.add_paragraph(f"長度：{_fmt_dur(m.get('durationSec'))}")
    d.add_heading("待辦", 1)
    for x in todo:
        d.add_paragraph(x, style="List Bullet")
    d.add_heading("重點", 1)
    for x in pts:
        d.add_paragraph(x, style="List Bullet")
    d.add_heading("Q&A", 1)
    for q, a in qa:
        d.add_paragraph().add_run(f"Q：{q}").bold = True
        d.add_paragraph(f"A：{a}")
    d.add_heading("逐字稿", 1)
    for line in _lines(m):
        d.add_paragraph(line)
    d.save(str(path))
