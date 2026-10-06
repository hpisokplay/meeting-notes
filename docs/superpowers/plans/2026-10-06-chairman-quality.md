# Chairman-Quality Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Make every AI output grounded, verified and reviewable: domain glossary at ASR time, auto term-correction before summarising, lecture/meeting modes, every item carries source quotes, program-side verification feeding a 待確認清單 that gates export; Opus by default.

**Spec:** `docs/superpowers/specs/2026-10-06-chairman-quality-design.md` (binding). Earlier system spec: `docs/superpowers/specs/2026-10-05-nas-migration-design.md`. Audit that motivated this: the 會計師第一場 audit (61 defects in 203 items).

## Global Constraints

- Legacy meetings must keep rendering: summary/notes items may be plain strings (legacy) OR objects (new). Every consumer accepts both.
- Never hard-delete; raw transcript kept in `transcriptRaw` before any automatic correction; restorable.
- All user-visible text Traditional Chinese (Taiwan).
- Default `settings.claudeModel` becomes `"opus"` (existing saved settings keep their value unless still default "sonnet" from the old default — migrate "sonnet" → "opus" once, flag `settings.modelMigrated=true`).
- Tests: server `server\.venv\Scripts\python -m pytest server/tests -q`; worker `python -m pytest worker -q`; web `npx vitest run`. All must stay green.

## Data contract (shared by all tasks — exact)

```
Item (new format, used in summary sections and notes list items):
  { id: "<8 hex>", text: str, src: [ {t: float_seconds, quote: str} ], group?: str }
  legacy: plain str → treat as {id: hash(text), text, src: []}

meeting.mode: "lecture" | "meeting"   (absent on legacy → "meeting")

meeting.summary (mode "meeting"): { actionItems: Item[], mainPoints: Item[], qa: Item[] }
meeting.summary (mode "lecture"): { conclusions: Item[], advice: Item[], risks: Item[], cases: Item[], mainPoints: Item[], qa: Item[] }
  qa Item.text format "問：…\n答：…" (same convention as legacy splitQA)
  lecture qa = only questions actually asked by someone in the recording

meeting.transcriptRaw: same shape as transcript; written once, before the first automatic correction.

meeting.corrections: { items: [ {wrong, right, confidence: "high"|"medium"|"low", count, ts: [float…], state: "applied"|"pending"|"rejected"} ], at }

meeting.review: { items: [ ReviewItem ], at }
  ReviewItem: { id: "<8 hex>", kind: "term"|"quote"|"number"|"name"|"calc"|"slip",
                section: "summary.<key>" | "notes.<key>" | "transcript",
                itemId?: str, text: str (what is shown), detail: str (why flagged, 繁中),
                suggestion?: str, t?: float, state: "pending"|"confirmed"|"edited"|"deleted" }
  pendingCount = items with state "pending"

settings.glossary: str (one term per line; defaults to DEFAULT_GLOSSARY in server)
job payload for transcribe: { glossary: [str…] }   (worker reads job.payload.glossary)
```

API additions:
- `POST /api/meetings/{id}/review/{rid}` body `{action: "confirm"|"edit"|"delete", text?: str}` → updated meeting. edit replaces the target item's text (by itemId) or, for kind "term", applies `right=text` to the transcript; delete removes the target item (moves it into `alt["deleted:<section>"]` list); confirm just marks state.
- `POST /api/meetings/{id}/ai` new action `"reprocess"` → runs correction → summary (mode-aware) → notes (only if meeting already has notes) → verify. Also runs automatically after transcription (replaces the old summary-only pipeline step).
- `POST /api/meetings/{id}/mode` body `{mode}` → sets mode, enqueues reprocess.
- `POST /api/meetings/{id}/restore-raw` → transcript = transcriptRaw, corrections states reset to pending, enqueue reprocess.
- `GET/PUT /api/settings` gains `glossary` (string).

---

### Task A: Worker — glossary, speaker smoothing, OpenCC fixes (worker/ only)

- `job.payload.glossary` (list) → faster-whisper `hotwords=" ".join(glossary[:200])` (check faster-whisper 1.2.1 transcribe signature supports `hotwords` in BatchedInferencePipeline; if not, append top terms to `initial_prompt` keeping it ≤ 200 chars) and keep the neutral prompt.
- `smooth_speakers(segments_or_words, max_sec=3.0, max_chars=8)`: a run of a different speaker sandwiched between the same speaker A on both sides, shorter than max_sec AND ≤ max_chars → reassigned to A; then re-merge adjacent same-speaker segments. Apply before naming speakers.
- OpenCC post-fix table (exact substring replacements after s2twp): 「隻有」→「只有」, 「隻是」→「只是」, 「隻要」→「只要」, 「隻能」→「只能」, 「隻好」→「只好」, 「一隻」 untouched. Unit-tested.
- Tests: hotwords/initial_prompt wiring with fake model; smoothing cases (sandwiched short run merged; long run kept; edge at start/end kept); OpenCC fixes.

### Task B: Server — prompts with sources, modes, verification (pure modules)

Files: `server/prompts.py` (new prompts), `server/verify.py` (new, pure), `server/items.py` (new: ids, legacy normalisation), tests.
- `items.normalize_item(x) -> Item` (str → Item with id = first 8 hex of sha1(text), src []); `items.ensure_ids(list)`.
- New prompt set (Opus-grade, strict grounding rules from spec §三, verbatim rules list): SUMMARY_MEETING, SUMMARY_LECTURE, MODE_DETECT (returns lecture|meeting + reason), CORRECTION (returns [{wrong,right,confidence,reason,ts}] — only ASR mishearings, never stylistic edits; include glossary as reference), NOTES (existing notes prompt + src per outline/concept/figure/quiz item), SLIPS (find speaker slips/contradictions: returns [{t, quote, issue, suggestion}]). JSON schemas require `src` arrays with t and quote for every item. Rules to include verbatim (Traditional Chinese): 只能寫逐字稿裡有的內容；不可補人名全名、職稱、機構、數字；講者的但書、警告（不要、小心、注意、不建議）必須保留在同一條；講者口誤或前後矛盾照實寫「講者原話為…，依上下文疑為…」；數字照原文單位，不可自行換算；每條附 1–3 個 src（t 為秒、quote 為逐字稿原句 6–40 字）。Lecture: mainPoints ≤ 25 grouped by `group`; meeting: ≤ 15.
- `verify.verify_meeting(meeting) -> list[ReviewItem]` (pure, no LLM): for every Item in summary/notes: quote fuzzy-match (normalise punctuation/space; ≥ 0.8 ratio via difflib on sliding windows) → else kind "quote"; numbers in text (arabic, 中文數字 incl. 萬/億/千/百/十, %, 年) must appear (numerically equivalent) in transcript text within ±60 s of any src t (if no src: anywhere) → else "number"; names: tokens from glossary + corrections.right + quoted 「…」 terms + Latin words ≥ 2 chars must occur in transcript → else "name"; calc: patterns `a%×b%=c%`, `a×b=c`, `a*b=c` verified → else "calc". Stable ReviewItem ids = hash(kind+section+itemId+text). Merge with previous review: keep states of ids already confirmed/edited/deleted.
- Tests with a small synthetic transcript covering each check incl. 中文數字 「五千萬」==50000000, 「一億」, 「28%」, 「民國108年」; legacy string items accepted.

### Task C: Server — pipeline, endpoints, settings (after Task B)

Files: `server/ai.py`, `server/jobs.py` (pipeline hook only), `server/app.py`/new router `server/review.py`, `server/storage.py` (settings defaults), `server/uploads.py` (glossary into transcribe payload), tests.
- DEFAULT_GLOSSARY in server (≈60 terms: tax/wealth/finance/semiconductor/optics — e.g. 股利, 股利所得, 複委託, ETF, BVI, 英屬維京群島, 開曼, CRS, 共同申報準則, 最低稅負, 基本所得額, 贈與稅, 遺產稅, 房地合一, 實質課稅, 納保法, 境外資金匯回專法, 亞洲資產管理中心, 受控外國公司, CFC, 信託, 保險, 輝達, 台積電, 美股, 台股, 400G, 800G, 1.6T, CPO, NPO, Pluggable, Turnkey, Pilot line, 無塵室 …).
- Transcribe job payload includes glossary list (settings.glossary lines or default).
- Pipeline after transcript result: enqueue ai `reprocess` (instead of `summary`). reprocess steps with job progress messages (「名詞校正中」「判斷講座／會議」「整理摘要中」「整理筆記中」「驗證中」): (1) save transcriptRaw if absent; (2) CORRECTION → high → apply to transcript (whole-word substring replace, count), medium/low → pending; (3) MODE_DETECT if meeting.mode absent; (4) summary by mode (Opus), normalise items + ids; (5) notes if meeting.notes exists (regenerate with src) — new meetings: notes stay on-demand as before; (6) SLIPS → review items kind "slip"; (7) verify → review items; meeting.review = merged; status "done". Old summary/notes backed up into alt (flat keys) before overwrite. Translations cleared.
- Existing actions enhance/notes_enhance/ask/terms/translate keep working; enhance outputs Items with src and re-runs verify afterwards.
- Endpoints per contract (review action, mode, restore-raw, reprocess). Review edit/delete operate by itemId on Item lists; for legacy string items use normalize_item ids.
- Settings: glossary default, claudeModel default opus + one-time migration.
- Tests: pipeline order with fake claude (mode lecture → lecture sections; corrections applied/pending; transcriptRaw kept); review endpoints (confirm/edit/delete incl. term edit applying to transcript); restore-raw; settings migration; job payload glossary.

### Task D: Frontend (js/, tests/ only; parallel with B/C against the contract)

- Render Items (string or object) everywhere summary/notes are shown; show a small time chip per src (click → seek audio / jump transcript like existing data-jump); copy/export use text only.
- Lecture mode sections & labels: 核心結論 / 講者建議 / 風險提醒 / 案例 / 重點（依 group 分組標題）/ Q&A. Meeting mode unchanged labels. Mode switch control on detail page (講座／會議) → POST mode, then wait job & reload.
- 「⚠️ 待確認清單」card placed directly under the summary/notes mode toggle (above enhance buttons): lists pending review items (kind badge, text, detail, suggestion, time chip) with ✅ 確認 / ✏️ 改正 (inline edit) / 🗑 刪除 → POST review action → reload. Shows count; collapses when 0 with 「全部確認完成」.
- Export gate: 分享 / PDF / Word disabled while pendingCount > 0, tooltip/alert 「還有 N 項待確認，處理完才能匯出」.
- Corrections: in the 專有名詞訂正 card show auto-applied corrections list (wrong → right, count) with 「還原原始逐字稿」 button (POST restore-raw, confirm dialog).
- Settings: 「轉錄詞庫」 textarea (one per line) saved via settings.glossary; Claude model select default label shows opus.
- Buttons: 「🔁 重新整理（不重新轉錄）」 → ai reprocess.
- Progress page: show reprocess sub-stage messages.
- docx/pdf export render Item.text (and lecture sections) correctly; legacy unaffected.
- Bump APP_VERSION v102 / sw cache meeting-app-v102.
- Tests: Items render from string and object; lecture sections; review card actions call API; export disabled with pending > 0; settings glossary save; docx contains lecture section titles.

### Task E: Deploy + rerun + re-audit (controller)

- Merge, run all suites, update_app.sh, copy worker code to C:\Users\User\MeetingWorker\app (restart scheduled task).
- Re-run the 會計師第一場 meeting: retranscribe (audio exists) → automatic reprocess.
- Re-audit with the same auditor brief; compare defect count (target: 0 fabrications, 0 unflagged wrong numbers; every remaining doubt appears in 待確認清單).
