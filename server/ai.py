"""Claude Max AI actions: `claude -p` runner, action implementations, write-back, AI thread, API.

One AI job runs at a time (AIThread claims kind "ai" as owner "nas-ai"). Results are written
back into the meeting by the server (the frontend just polls the job and refetches).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Callable

from fastapi import APIRouter, Body, HTTPException

from . import prompts as P
from .storage import BadId, Store

log = logging.getLogger(__name__)

AI_OWNER = "nas-ai"
TIMEOUT_S = 30 * 60
LIMIT_RETRY_S = 30 * 60
MAX_LIMIT_RETRIES = 6
LIMIT_MSG = "Claude 用量已達上限，30 分鐘後自動重試"
TRANSIENT_RETRY_S = 5 * 60
TRANSIENT_MSG = "Claude 暫時連不上，5 分鐘後自動重試"
# Both are matched ONLY against stderr, the `result` text and the subtype / api_error_status
# fields (never the whole stdout JSON: "duration_ms":14293 etc. must not look like an error).
LIMIT_RE = re.compile(r"usage limit|rate limit|\b429\b|limit reached|額度已用完|額度不足|用量已達上限", re.I)
TRANSIENT_RE = re.compile(
    r"api error:?\s*5\d\d\b|\b5\d\d\b[^\n]{0,40}(?:error|internal|unavailable|gateway)"
    r"|internal server error|service unavailable|bad gateway|gateway timeout"
    r"|connection error|econnreset|etimedout|econnrefused|fetch failed|socket hang up"
    r"|overloaded|\b529\b", re.I)
KEEP_ENV = {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"}

MAX_CHARS = 600_000              # whole-transcript budget per call (spec §9; sonnet/opus 1M ctx)
HAIKU_MAX_CHARS = 150_000        # haiku: smaller context window
TERMS_BATCH_CHARS = 60_000       # terms: batched scan for recall (legacy: 240 lines)
ENHANCE_BATCH_CHARS = 60_000     # enhance: batched scan for completeness (legacy: 240 lines)
NOTES_ENHANCE_BATCH_CHARS = 20_000  # notes_enhance (legacy: 80 lines)
TRANSLATE_BATCH_CHARS = 12_000   # translated output must fit the output-token limit
ASK_CHUNK_CHARS = 50_000
ABORT_CHECK_S = 1.0              # poll interval while claude runs
HEARTBEAT_S = 30.0               # job heartbeat while claude runs (requeue_stale is 10 min)

ACTIONS = {"summary", "enhance", "notes", "notes_enhance", "ask", "terms", "translate"}
LABELS = {"summary": "摘要", "notes": "學習筆記", "ask": "回答問題", "terms": "挑專有名詞",
          "translate": "翻譯"}


class ClaudeError(Exception):
    """Claude call failed (permanent for the job)."""


class ClaudeLimitError(ClaudeError):
    """Usage / rate limit: retry later."""


class ClaudeTransientError(ClaudeError):
    """API 5xx / overloaded / network trouble: retry in 5 minutes."""


class ClaudeAborted(ClaudeError):
    """The job was cancelled while Claude was running."""


# ---------------------------------------------------------------- runner
def child_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if k.upper() in KEEP_ENV or not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _drain(proc: subprocess.Popen) -> None:
    """Reap a killed process without ever blocking forever on a stuck pipe."""
    try:
        proc.communicate(timeout=10)
    except Exception:
        pass


def _parse_json_text(s: str):
    s = (s or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    if m:
        s = m.group(1)
    return json.loads(s)


def run_claude(system: str, user: str, schema: dict, model: str, cmd: list[str], *,
               timeout: float = TIMEOUT_S, abort: Callable[[], bool] | None = None) -> dict:
    """Run `claude -p` once; transcript/payload on stdin. Returns the structured output dict."""
    exe = shutil.which(cmd[0]) or cmd[0]
    args = [exe, *cmd[1:], "-p", "--model", model, "--tools", "", "--output-format", "json",
            "--no-session-persistence", "--max-turns", "3",
            "--json-schema", json.dumps(schema, ensure_ascii=False), "--system-prompt", system]
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=child_env(), **kw)
    except OSError as e:
        raise ClaudeError(f"無法啟動 Claude：{e}")
    deadline = time.monotonic() + timeout
    data_in = user.encode("utf-8")
    first = True
    while True:
        try:
            out, err = proc.communicate(input=data_in if first else None, timeout=ABORT_CHECK_S)
            break
        except subprocess.TimeoutExpired:
            first = False
            if abort is not None and abort():
                _kill_tree(proc)
                _drain(proc)
                raise ClaudeAborted("已取消")
            if time.monotonic() > deadline:
                _kill_tree(proc)
                _drain(proc)
                raise ClaudeError(f"Claude 逾時（超過 {int(timeout // 60) or 1} 分鐘）")
    stdout = out.decode("utf-8", errors="replace")
    stderr = err.decode("utf-8", errors="replace")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        data = None
    failed = proc.returncode != 0 or data is None or bool(data.get("is_error"))
    if failed:
        parts = [stderr]
        if data is not None:
            parts += [data.get("result"), data.get("subtype"), data.get("api_error_status")]
        else:
            parts.append(stdout)  # not JSON at all: the raw text is the error message
        text = " ".join(str(x) for x in parts
                        if x not in (None, "") and not isinstance(x, (dict, list)))
        if LIMIT_RE.search(text):
            raise ClaudeLimitError(LIMIT_MSG)
        status = (data or {}).get("api_error_status")
        if TRANSIENT_RE.search(text) or (isinstance(status, int) and 500 <= status <= 599):
            raise ClaudeTransientError(TRANSIENT_MSG)
        snippet = re.sub(r"\s+", " ", text).strip()[:300] or f"結束代碼 {proc.returncode}"
        raise ClaudeError(f"Claude 執行失敗：{snippet}")
    so = data.get("structured_output")
    if isinstance(so, dict):
        return so
    try:
        r = _parse_json_text(data.get("result") or "")
    except (json.JSONDecodeError, TypeError):
        r = None
    if not isinstance(r, dict):
        raise ClaudeError("Claude 回傳結果無法解析，請重試")
    return r


# ---------------------------------------------------------------- transcript helpers
def _hms(t) -> str | None:
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return None
    s = max(0, int(t))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcript_lines(segs) -> list[str]:
    out = []
    for s in segs or []:
        if not isinstance(s, dict):
            continue
        line = f"{s.get('speaker') or '說話者'}: {s.get('text') or ''}"
        ts = _hms(s.get("t"))
        out.append(f"[{ts}] {line}" if ts else line)
    return out


def serialize_transcript(segs) -> str:
    return "\n".join(transcript_lines(segs))


def chunk_lines(lines: list[str], budget: int) -> list[str]:
    """Join lines into chunks of at most `budget` chars (a single longer line is its own chunk)."""
    chunks, cur, size = [], [], 0
    for ln in lines:
        add = len(ln) + (1 if cur else 0)
        if cur and size + add > budget:
            chunks.append("\n".join(cur))
            cur, size = [], 0
            add = len(ln)
        cur.append(ln)
        size += add
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def _keywords(q: str) -> set[str]:
    q = (q or "").lower()
    words = set(re.findall(r"[a-z0-9]{2,}", q))
    cjk = re.findall(r"[぀-ヿ㐀-鿿]+", q)
    for run in cjk:
        words.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return words


def pick_relevant(lines: list[str], question: str, budget: int = MAX_CHARS,
                  chunk: int = ASK_CHUNK_CHARS) -> str:
    """Whole text when it fits; else the chunks with most keyword overlap, in original order."""
    whole = "\n".join(lines)
    if len(whole) <= budget:
        return whole
    chunks = chunk_lines(lines, chunk)
    kws = _keywords(question)
    scored = sorted(range(len(chunks)),
                    key=lambda i: (-sum(chunks[i].lower().count(k) for k in kws), i))
    picked, size = [], 0
    for i in scored:
        if size + len(chunks[i]) > budget:
            continue
        picked.append(i)
        size += len(chunks[i]) + 1
    return "\n".join(chunks[i] for i in sorted(picked))


# ---------------------------------------------------------------- pure ports of gemini.js helpers
def assemble_polished(items, all_: list[str], cap: int, honor_drop: bool) -> dict:
    n = len(all_)
    covered: set[int] = set()
    outs, dropped = [], 0
    for it in items or []:
        it = it if isinstance(it, dict) else {}
        is_drop = honor_drop and it.get("drop") is True
        text = it["text"].strip() if isinstance(it.get("text"), str) else ""
        if not is_drop and not text:
            continue
        src = []
        for x in it.get("src") if isinstance(it.get("src"), list) else []:
            try:
                x = int(float(x))
            except (TypeError, ValueError):
                continue
            if 1 <= x <= n and x not in covered and x not in src:
                src.append(x)
        if not src:
            continue
        if is_drop:
            covered.update(src)
            dropped += len(src)
            continue
        if len(src) > cap:
            for x in src:
                covered.add(x)
                outs.append((x, all_[x - 1]))
            continue
        covered.update(src)
        outs.append((min(src), text))
    for x in range(1, n + 1):
        if x not in covered:
            outs.append((x, all_[x - 1]))
    outs.sort(key=lambda o: o[0])
    return {"list": [t for _, t in outs], "dropped": dropped}


def merge_term_groups(items: list[dict], groups) -> list[dict]:
    by_term = {x["t"]: x for x in items}
    taken_by: dict[str, str] = {}
    alts_of: dict[str, list[str]] = {}
    for g in groups or []:
        g = g if isinstance(g, dict) else {}
        terms = []
        for t in g.get("terms") if isinstance(g.get("terms"), list) else []:
            t = str(t or "").strip()
            if t in by_term and t not in taken_by and t not in terms:
                terms.append(t)
        if len(terms) < 2:
            continue
        head = next(x["t"] for x in items if x["t"] in terms)
        rest = [t for t in terms if t != head]
        for t in rest:
            taken_by[t] = head
        taken_by[head] = head
        alts_of[head] = rest
        best = str(g.get("best") or "").strip()
        if best:
            by_term[head]["fix"] = best
    return [{**x, "alts": alts_of.get(x["t"], [])} for x in items
            if x["t"] not in taken_by or taken_by[x["t"]] == x["t"]]


def _s(x) -> str:
    return x.strip() if isinstance(x, str) else ""


def _a(x) -> list:
    return x if isinstance(x, list) else []


ANCHOR_PREFIX_RE = re.compile(r"^\[\d\d:\d\d:\d\d\]\s*[^:：]+[:：]\s*")


def _clean_anchor(x) -> str:
    """Anchors must be plain transcript text; prompt lines carry `[HH:MM:SS] 說話者: `."""
    return ANCHOR_PREFIX_RE.sub("", _s(x)).strip()


def normalize_notes(r) -> dict:
    o = r if isinstance(r, dict) else {}
    d = lambda x: x if isinstance(x, dict) else {}  # noqa: E731
    tables = []
    for t in _a(o.get("tables")):
        t = d(t)
        headers = [h for h in map(_s, _a(t.get("headers"))) if h]
        rows = []
        for row in _a(t.get("rows")):
            row = [_s(c) for c in row] if isinstance(row, list) else [c.strip() for c in _s(row).split("|")]
            if any(row):
                rows.append(row)
        if headers and rows:
            tables.append({"title": _s(t.get("title")), "headers": headers, "rows": rows})
    figures = []
    for f in _a(o.get("figures")):
        if isinstance(f, str):
            t = f.strip()
            m = re.match(r"^([^：:]{1,24})[：:]\s*(.+)$", t)
            f2 = {"group": "", "label": m.group(2).strip(), "value": m.group(1).strip()} if m \
                else {"group": "", "label": t, "value": ""}
        else:
            f = d(f)
            f2 = {"group": _s(f.get("group")), "label": _s(f.get("label")), "value": _s(f.get("value"))}
        if f2["label"]:
            figures.append(f2)
    return {
        "outline": [x for x in ({"title": _s(d(x).get("title")), "anchor": _clean_anchor(d(x).get("anchor")),
                                 "points": [p for p in map(_s, _a(d(x).get("points"))) if p]}
                                for x in _a(o.get("outline"))) if x["title"]],
        "concepts": [x for x in ({"term": _s(d(x).get("term")), "plain": _s(d(x).get("plain")),
                                  "why": _s(d(x).get("why"))} for x in _a(o.get("concepts"))) if x["term"]],
        "tables": tables,
        "figures": figures,
        "quiz": [x for x in ({"q": _s(d(x).get("q")), "a": _s(d(x).get("a"))}
                             for x in _a(o.get("quiz"))) if x["q"]],
    }


def dedupe_notes_items(section: str, all_: list) -> list:
    if section == "outline":
        out = []
        for o in all_:
            if out and out[-1]["title"] == o["title"]:
                out[-1]["points"] += [p for p in o.get("points") or [] if p not in out[-1]["points"]]
                continue
            out.append({**o, "points": list(o.get("points") or [])})
        return out
    key = {"concepts": "term", "tables": "title", "quiz": "q", "figures": "label"}[section]
    seen, out = set(), []
    for x in all_:
        k = (x or {}).get(key) or ""
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def _dedupe(xs):
    seen, out = set(), []
    for x in xs:
        k = json.dumps(x, ensure_ascii=False, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


# ---------------------------------------------------------------- actions
class Ctx:
    """What an action needs: the meeting, a Claude caller and a progress reporter."""

    def __init__(self, meeting: dict, call, report, model: str = "sonnet"):
        self.m = meeting
        self.budget = HAIKU_MAX_CHARS if "haiku" in (model or "").lower() else MAX_CHARS
        self.call = call  # call(system, user, schema) -> dict
        self.report = report  # report(pct, msg)
        self.lines = transcript_lines(meeting.get("transcript"))


def _summary(ctx: Ctx, payload):
    chunks = chunk_lines(ctx.lines, ctx.budget)
    acc = {"actionItems": [], "mainPoints": [], "qa": []}
    for i, text in enumerate(chunks):
        ctx.report(5 + 90 * i // len(chunks), "Claude 摘要中…" if len(chunks) == 1
                   else f"Claude 摘要中…（第 {i + 1}/{len(chunks)} 段）")
        r = ctx.call(P.SUMMARY_PROMPT, text, P.SUMMARY_SCHEMA)
        for k in acc:
            acc[k] += [x for x in _a(r.get(k)) if isinstance(x, str) and x.strip()]
    summary = {k: _dedupe(v) for k, v in acc.items()}

    def apply(m):
        old = m.get("summary") if isinstance(m.get("summary"), dict) else {}
        if any(_a(old.get(k)) for k in acc):
            alt = m.setdefault("alt", {})
            for k in acc:
                alt[f"summary:{k}"] = list(_a(old.get(k)))
        m["summary"] = summary
        m["translations"] = {}
        m["error"] = None  # any successful summary clears an old (e.g. failed-summary) error
    return apply


def _enhance(ctx: Ctx, payload):
    section = payload["section"]
    meta = P.SECTION_META[section]
    batches = chunk_lines(ctx.lines, ENHANCE_BATCH_CHARS)
    all_: list[str] = []
    for i, text in enumerate(batches):
        ctx.report(5 + 70 * i // len(batches), f"Claude 加強{meta['label']}中…（{i + 1}/{len(batches)}）")
        r = ctx.call(P.enhance_prompt(meta), text, P.ITEMS_SCHEMA)
        all_ += [x.strip() for x in _a(r.get("items")) if isinstance(x, str) and x.strip()]
    items = all_
    if all_:
        ctx.report(80, f"Claude 整理潤飾{meta['label']}中…")
        r = ctx.call(P.polish_prompt(meta, len(all_)),
                     "\n".join(f"{i + 1}. {s}" for i, s in enumerate(all_)), P.POLISH_SCHEMA)
        if not isinstance(r.get("items"), list):
            raise ClaudeError(f"整理{meta['label']}後結果為空，請重試")
        cap = meta.get("cap") or P.MERGE_CAP
        built = assemble_polished(r["items"], all_, cap, bool(meta.get("allowDrop")))
        if built["dropped"] > max(2, len(all_) * 0.4) or not built["list"]:
            built = assemble_polished(r["items"], all_, cap, False)
        items = built["list"]

    def apply(m):
        box = m["summary"] if isinstance(m.get("summary"), dict) else {}
        m.setdefault("alt", {})[f"summary:{section}"] = list(_a(box.get(section)))
        box[section] = items
        m["summary"] = box
        m["translations"] = {}
    return apply


def _notes(ctx: Ctx, payload):
    chunks = chunk_lines(ctx.lines, ctx.budget)
    parts = []
    for i, text in enumerate(chunks):
        ctx.report(5 + 90 * i // len(chunks), "Claude 整理學習筆記中…" if len(chunks) == 1
                   else f"Claude 整理學習筆記中…（第 {i + 1}/{len(chunks)} 段）")
        parts.append(normalize_notes(ctx.call(P.NOTES_PROMPT, text, P.NOTES_SCHEMA)))
    notes = {k: dedupe_notes_items(k, [x for p in parts for x in p[k]]) for k in P.NOTES_KEYS}

    def apply(m):
        old = m.get("notes") if isinstance(m.get("notes"), dict) else {}
        if any(_a(old.get(k)) for k in P.NOTES_KEYS):
            alt = m.setdefault("alt", {})
            for k in P.NOTES_KEYS:
                alt[f"notes:{k}"] = list(_a(old.get(k)))
        m["notes"] = {**notes, "generatedAt": int(time.time() * 1000)}
        m["translations"] = {}
    return apply


def _notes_enhance(ctx: Ctx, payload):
    section = payload["section"]
    meta = P.NOTES_SECTIONS[section]
    batches = chunk_lines(ctx.lines, NOTES_ENHANCE_BATCH_CHARS)
    all_ = []
    for i, text in enumerate(batches):
        ctx.report(5 + 90 * i // len(batches), f"Claude 加強{meta['label']}中…（{i + 1}/{len(batches)}）")
        r = ctx.call(P.notes_enhance_prompt(section), text, P.notes_section_schema(section))
        all_ += normalize_notes({section: r.get(section)})[section]
    items = dedupe_notes_items(section, all_)

    def apply(m):
        box = m["notes"] if isinstance(m.get("notes"), dict) else {k: [] for k in P.NOTES_KEYS}
        m.setdefault("alt", {})[f"notes:{section}"] = list(_a(box.get(section)))
        box[section] = items
        m["notes"] = box
        m["translations"] = {}
    return apply


def _ask(ctx: Ctx, payload):
    q = payload["question"].strip()
    ctx.report(10, "Claude 思考回答中…")
    text = pick_relevant(ctx.lines, q, budget=ctx.budget)
    r = ctx.call(P.ASK_PROMPT, P.ask_user_message(ctx.m.get("summary"), text, q), P.ANSWER_SCHEMA)
    a = _s(r.get("answer"))
    if not a:
        raise ClaudeError("未取得回答，請重試。")

    def apply(m):
        chat = m["chat"] if isinstance(m.get("chat"), list) else []
        chat.append({"q": q, "a": a, "at": int(time.time() * 1000)})
        m["chat"] = chat
    return apply


def _terms(ctx: Ctx, payload):
    # batched like enhance: one pass over a long transcript misses terms (recall)
    chunks = chunk_lines(ctx.lines, min(TERMS_BATCH_CHARS, ctx.budget))
    found: dict[str, dict] = {}  # dedupe by t; the first batch's category wins
    for i, text in enumerate(chunks):
        ctx.report(5 + 70 * i // len(chunks), f"Claude 挑出專有名詞中…（{i + 1}/{len(chunks)}）")
        r = ctx.call(P.TERMS_PROMPT, text, P.TERMS_SCHEMA)
        for it in _a(r.get("items")):
            it = it if isinstance(it, dict) else {}
            t = _s(it.get("term"))
            if t and t not in found:
                found[t] = {"t": t, "cat": _s(it.get("category")) or "term", "fix": _s(it.get("fix"))}
    items = list(found.values())
    if len(items) >= 2:
        ctx.report(80, "Claude 比對同一名稱的不同寫法…")
        try:
            g = ctx.call(P.GROUP_PROMPT, "\n".join(f"- {x['t']}" for x in items), P.GROUP_SCHEMA)
            items = merge_term_groups(items, g.get("groups"))
        except (ClaudeLimitError, ClaudeAborted):
            raise
        except ClaudeError:
            items = [{**x, "alts": []} for x in items]  # grouping is optional (legacy behaviour)
    else:
        items = [{**x, "alts": []} for x in items]

    def apply(m):
        old = m.get("terms") if isinstance(m.get("terms"), dict) else {}
        kept = [x for x in _a(old.get("items")) if isinstance(x, dict) and x.get("t")
                and (x.get("applied") or x.get("draft"))]
        seen = {x["t"] for x in kept}
        merged = list(kept)
        for x in items:
            if x["t"] not in seen:
                merged.append(x)
                seen.add(x["t"])
        m["terms"] = {"scannedAt": int(time.time() * 1000), "items": merged}
    return apply


def _fingerprint(segs) -> str:
    return json.dumps([[s.get("speaker"), s.get("text")] for s in segs or [] if isinstance(s, dict)],
                      ensure_ascii=False)


def _translate(ctx: Ctx, payload):
    lang = payload["lang"]
    system = P.translate_prompt(lang)
    m0 = ctx.m
    fp = _fingerprint(m0.get("transcript"))
    s = m0.get("summary") if isinstance(m0.get("summary"), dict) else {}
    ctx.report(5, "Claude 翻譯摘要中…")
    src_sum = {"actionItems": _a(s.get("actionItems")), "mainPoints": _a(s.get("mainPoints") or s.get("keyPoints")),
               "qa": _a(s.get("qa"))}
    r = ctx.call(system, json.dumps(src_sum, ensure_ascii=False), P.SUMMARY_SCHEMA)
    out = {"summary": {k: _a(r.get(k)) for k in ("actionItems", "mainPoints", "qa")}}
    notes = m0.get("notes") if isinstance(m0.get("notes"), dict) else None
    if notes and any(_a(notes.get(k)) for k in P.NOTES_KEYS):
        ctx.report(10, "Claude 翻譯學習筆記中…")
        src = {k: _a(notes.get(k)) for k in P.NOTES_KEYS}
        out["notes"] = normalize_notes(ctx.call(system, json.dumps(src, ensure_ascii=False), P.NOTES_SCHEMA))
    segs = [x for x in _a(m0.get("transcript")) if isinstance(x, dict)]
    batches, cur, size = [], [], 0
    for x in segs:
        n = len(str(x.get("text") or "")) + len(str(x.get("speaker") or "")) + 30
        if cur and size + n > TRANSLATE_BATCH_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(x)
        size += n
    if cur:
        batches.append(cur)
    tr_segs = []
    for i, b in enumerate(batches):
        ctx.report(15 + 80 * i // len(batches), f"Claude 翻譯逐字稿中…（{i + 1}/{len(batches)}）")
        src = {"segments": [{"speaker": str(x.get("speaker") or ""), "text": str(x.get("text") or "")}
                            for x in b]}
        for _attempt in range(2):  # a different count would shift timestamps: retry once
            got = [x for x in _a(ctx.call(system, json.dumps(src, ensure_ascii=False),
                                          P.TR_SEG_SCHEMA).get("segments")) if isinstance(x, dict)]
            if len(got) == len(b):
                break
        for j, orig in enumerate(b):  # pad (keep the original) / trim to exactly the batch length
            seg = dict(orig)
            if j < len(got):
                g = got[j]
                seg["speaker"] = _s(g.get("speaker")) or seg.get("speaker") or ""
                if isinstance(g.get("text"), str):
                    seg["text"] = g["text"]
            tr_segs.append(seg)
    out["transcript"] = tr_segs

    def apply(m):
        if _fingerprint(m.get("transcript")) != fp:
            raise ClaudeError("原文已變更，請重新翻譯")
        tr = m["translations"] if isinstance(m.get("translations"), dict) else {}
        tr[lang] = out
        m["translations"] = tr
    return apply


HANDLERS = {"summary": _summary, "enhance": _enhance, "notes": _notes, "notes_enhance": _notes_enhance,
            "ask": _ask, "terms": _terms, "translate": _translate}


# ---------------------------------------------------------------- job handling
def _is_pipeline_summary(m: dict, jid: str) -> bool:
    return m.get("aiJobId") == jid and m.get("status") == "summarizing"


def on_ai_job_terminal(store: Store, job: dict | None, state: str) -> None:
    """JobQueue terminal listener: a cancelled / permanently failed pipeline summary must not
    leave the meeting stuck in "summarizing" (the transcript is usable)."""
    if not job or job.get("kind") != "ai" or not job.get("meeting"):
        return
    jid = job.get("id")

    def apply(m):
        if not _is_pipeline_summary(m, jid):
            return
        m["status"] = "done"
        m["progress"] = {"stage": "done", "pct": 100, "msg": ""}
        if state == "failed":
            m["error"] = job.get("error") or "摘要失敗"
    try:
        m = store.get(job["meeting"])
        if m and _is_pipeline_summary(m, jid):
            store.update(job["meeting"], apply)
    except (KeyError, BadId):
        pass
    except Exception:
        log.exception("ai terminal listener failed for %s", jid)


def handle_ai_job(store: Store, job: dict, cmd: list[str], jobs=None, *, model: str | None = None) -> None:
    """Run one claimed (running) ai job to completion: Claude call(s), write-back, finish/fail."""
    if jobs is None:
        from .jobs import JobQueue
        jobs = JobQueue(store)
    jid, mid = job["id"], job.get("meeting")
    payload = job.get("payload") or {}
    action = payload.get("action")
    model = model or store.get_settings().get("claudeModel") or "sonnet"
    state = {"pct": 0, "msg": "", "beat": time.monotonic(), "check": 0.0, "gone": False}
    m = store.get(mid) if mid else None
    pipeline = bool(m) and _is_pipeline_summary(m, jid)

    def report(pct, msg):
        state.update(pct=pct, msg=msg)
        if jobs.progress(jid, "claude", pct, msg) is None:
            state["gone"] = True
        state["beat"] = time.monotonic()
        if pipeline:
            def ap(mm):
                if _is_pipeline_summary(mm, jid):
                    mm["progress"] = {"stage": "claude", "pct": pct, "msg": msg,
                                      "engine": (mm.get("progress") or {}).get("engine", "")}
            try:
                store.update(mid, ap)
            except (KeyError, BadId):
                state["gone"] = True

    def abort():
        now = time.monotonic()
        if state["gone"]:
            return True
        if now - state["beat"] >= HEARTBEAT_S:
            state["beat"] = now
            if jobs.progress(jid, "claude", state["pct"], state["msg"]) is None:
                state["gone"] = True
        elif now - state["check"] >= ABORT_CHECK_S:
            state["check"] = now
            j = jobs.get(jid)
            if j is None or j.get("state") != "running":
                state["gone"] = True
        return state["gone"]

    def call(system, user, schema):
        if abort():
            raise ClaudeAborted("已取消")
        return run_claude(system, user, schema, model, cmd, abort=abort)

    try:
        if m is None:
            raise ClaudeError("找不到這場會議")
        handler = HANDLERS.get(action)
        if handler is None:
            raise ClaudeError(f"未知的 AI 動作：{action}")
        report(0, f"Claude {LABELS.get(action, '處理')}中…" if action in LABELS else "Claude 處理中…")
        apply = handler(Ctx(m, call, report, model), payload)
        if abort():
            raise ClaudeAborted("已取消")

        def write(mm):
            # runs under the meeting lock: a cancel that landed after the Claude call returned
            # must still win (raising here aborts store.update before anything is saved)
            j = jobs.get(jid)
            if j is None or j.get("state") != "running":
                raise ClaudeAborted("已取消")
            apply(mm)
            if action == "summary" and _is_pipeline_summary(mm, jid):
                mm["status"] = "done"
                mm["error"] = None
                mm["progress"] = {"stage": "done", "pct": 100, "msg": "",
                                  "engine": (mm.get("progress") or {}).get("engine", "")}
        try:
            store.update(mid, write)
        except KeyError:
            raise ClaudeError("找不到這場會議")
        jobs.progress(jid, "claude", 100, "完成")
        jobs.finish(jid)
    except ClaudeAborted:
        log.info("ai job %s cancelled while running", jid)
    except ClaudeLimitError as e:
        if (job.get("retries") or 0) < MAX_LIMIT_RETRIES:
            jobs.fail(jid, LIMIT_MSG, retry_at=time.time() + LIMIT_RETRY_S)
            if pipeline:
                report_limit(store, mid, jid)
        else:
            jobs.fail(jid, f"{e}（已重試 {MAX_LIMIT_RETRIES} 次仍失敗）".replace("，30 分鐘後自動重試", ""))
    except ClaudeTransientError as e:  # shares the retries counter / cap with the limit retries
        if (job.get("retries") or 0) < MAX_LIMIT_RETRIES:
            jobs.fail(jid, TRANSIENT_MSG, retry_at=time.time() + TRANSIENT_RETRY_S)
            if pipeline:
                report_limit(store, mid, jid, TRANSIENT_MSG)
        else:
            jobs.fail(jid, f"{e}（已重試 {MAX_LIMIT_RETRIES} 次仍失敗）".replace("，5 分鐘後自動重試", ""))
    except Exception as e:  # ClaudeError and anything unexpected: permanent failure
        if not isinstance(e, ClaudeError):
            log.exception("ai job %s crashed", jid)
        jobs.fail(jid, str(e) or "AI 工作失敗")


def report_limit(store: Store, mid: str, jid: str, msg: str = LIMIT_MSG) -> None:
    def ap(m):
        if _is_pipeline_summary(m, jid):
            m["progress"] = {"stage": "claude", "pct": 0, "msg": msg,
                             "engine": (m.get("progress") or {}).get("engine", "")}
    try:
        store.update(mid, ap)
    except (KeyError, BadId):
        pass


class AIThread(threading.Thread):
    """Claims "ai" jobs one at a time and runs them."""

    def __init__(self, store: Store, jobs, cmd: list[str], poll: float = 2.0):
        super().__init__(daemon=True, name="ai-worker")
        self.store, self.jobs, self.cmd, self.poll = store, jobs, cmd, poll
        self._stop_ev = threading.Event()

    def stop(self, timeout: float = 10) -> None:
        self._stop_ev.set()
        self.join(timeout)

    def run(self) -> None:
        while not self._stop_ev.is_set():
            job = None
            try:
                job = self.jobs.claim(["ai"], AI_OWNER)
            except Exception:
                log.exception("ai claim failed")
            if job is None:
                self._stop_ev.wait(self.poll)
                continue
            try:
                handle_ai_job(self.store, job, self.cmd, self.jobs)
            except Exception:
                log.exception("ai job %s failed unexpectedly", job.get("id"))


# ---------------------------------------------------------------- HTTP
def build_ai_router(store: Store, get_jobs) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.post("/meetings/{mid}/ai")
    def run_ai(mid: str, body: dict = Body(...)):
        action = body.get("action")
        if action not in ACTIONS:
            raise HTTPException(400, "未知的 AI 動作")
        payload = {"action": action}
        if action == "enhance":
            if body.get("section") not in P.SECTION_META:
                raise HTTPException(400, "未知的區塊")
            payload["section"] = body["section"]
        elif action == "notes_enhance":
            if body.get("section") not in P.NOTES_SECTIONS:
                raise HTTPException(400, "未知的區塊")
            payload["section"] = body["section"]
        elif action == "ask":
            q = body.get("question")
            if not isinstance(q, str) or not q.strip():
                raise HTTPException(400, "請輸入問題")
            payload["question"] = q.strip()
        elif action == "translate":
            if body.get("lang") not in P.LANG_LABEL:
                raise HTTPException(400, "不支援的語言")
            payload["lang"] = body["lang"]
        try:
            m = store.get(mid)
        except BadId:
            raise HTTPException(400, "會議 id 不合法")
        if m is None:
            raise HTTPException(404, "找不到這場會議")
        if not (isinstance(m.get("transcript"), list) and m["transcript"]):
            raise HTTPException(400, "這場沒有逐字稿，無法使用 AI 功能")
        return {"jobId": get_jobs().enqueue("ai", mid, payload)}

    return r
