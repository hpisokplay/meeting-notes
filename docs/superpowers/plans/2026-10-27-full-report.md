# 完整整理（引言＋大標題＋子標題）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 AI 動作「完整整理」（引言＋大標題＋子標題，走 Claude API、失敗退回 Max），並讓 Word／PDF／伺服器匯出照新結構排版。

**Architecture:** 結果存成兩個平的條目清單 `summary.reportIntro`、`summary.reportPoints`（`group`＝大標題，`text`＝「子標題：說明」），所以既有的依據驗證、人名替換、編輯、點回原句全部沿用。新模組 `server/anthropic_api.py` 負責 API 呼叫；`server/ai.py` 新增 `report` 動作，先試 API，任何失敗整次改用 Max 重跑。前端三處匯出（`js/docx.js`、`js/export.js`、`server/render.py`）看到 `reportPoints` 就改用新順序。

**Tech Stack:** Python 3 / FastAPI / pytest（`server/tests`）；Vanilla JS ES Modules / vitest（`tests`）；官方 `anthropic` Python 套件。

**Spec:** `docs/superpowers/specs/2026-10-27-full-report-design.md`（含「實作調整」一節）

## Global Constraints

- 只有 `report` 動作走 API；其他 AI 動作一律走 Max，不改。
- API 模型：`claude-opus-5-5`，寫成單一常數 `REPORT_API_MODEL`（`server/anthropic_api.py`）。
- 金鑰檔：`/run/meetings/anthropic.key`（可用環境變數 `MEETINGS_ANTHROPIC_KEY_FILE` 覆寫，測試用）。檔案不存在或為空＝API 未啟用，直接走 Max，不報錯。金鑰不得出現在 log、錯誤訊息、git。
- `server/ai.py` 的 `child_env()` 繼續濾掉 `ANTHROPIC*`，不得改。
- 一次完整整理只走一條路；API 任何一段失敗，整次用 Max 重跑；`reportMeta.via` 只能是 `"api"` 或 `"max"`。
- 分段合併的第二次呼叫只可重排與合併分組，不可改寫文字，保留全部 `src`。
- 有完整整理時匯出順序：標題日期 → 引言 → 編號大標題＋圓點子標題（粗體小標＋全形冒號＋說明）→ Action Items（i. ii.）→ Q&A（i. ii.）→（選用）逐字稿；不輸出「會議重點」。
- 沒有 `reportPoints` 的會議，畫面與匯出必須與改版前逐字相同。
- 中文一律繁體（台灣用語）。不 merge main、不 push、不部署 NAS。

## Review Focus

- 子標題沒有冒號（AI 漏寫）：匯出整段當說明，不加粗體小標，不丟字。
- AI 回傳空的 `reportPoints`（只有引言）：視為「沒有完整整理」，匯出走舊格式。
- API 回 200 但被截斷（`stop_reason == "max_tokens"`）或不符 schema：視為失敗，退回 Max。
- 金鑰檔有前後空白／換行／BOM：要 strip；任何例外訊息不含金鑰。
- 講座模式的會議有完整整理：沒有 `actionItems` 時 Action Items 區塊整個省略（不印「（無）」），Q&A 同理。
- 標題或說明含 `& < >`：Word 檔不可損毀（沿用 `esc`）。

---

### Task 1: 提示詞、schema、正規化（純函式）

**Files:**
- Modify: `server/prompts.py`（檔尾新增）
- Create: `server/report.py`
- Test: `server/tests/test_report.py`

**Interfaces:**
- Produces（`server/prompts.py`）：`REPORT_SCHEMA`（`{intro: ITEM_SCHEMA, points: [{heading:str, title:str, text:str, src:[SRC]}]}`，required intro/points）、`REPORT_PROMPT: str`（含 `GROUNDING_RULES`、`LANG_RULE`、`IN_USER`）、`REPORT_MERGE_SCHEMA`、`REPORT_MERGE_PROMPT: str`。
- Produces（`server/report.py`）：
  - `normalize_report(raw: dict) -> dict`：回傳 `{"reportIntro": [Item], "reportPoints": [Item]}`；每個 point 轉成 `text = f"{title}：{text}"`（title 為空則只留 text）、`group = heading`（heading 空白則用「其他」）；丟掉 text 空白者；用 `ensure_ids`、`normalize_src`。
  - `merge_reports(parts: list[dict]) -> dict`：多段（每段是 `normalize_report` 的結果）合併：引言取第一段；`reportPoints` 依 `group` 第一次出現順序串接，同文字去重並合併 `src`（最多 `MAX_SRC`）。純程式，不呼叫 AI。
  - `has_report(summary) -> bool`：`summary.reportPoints` 至少 1 條。

- [ ] **Step 1: Write failing tests** in `server/tests/test_report.py`：`test_normalize_joins_title_and_text`（輸入 title「生意與訂單」text「元澄可提供…」→ `text == "生意與訂單：元澄可提供…"`、`group == "會議討論內容"`）；`test_normalize_no_title_keeps_text_only`；`test_normalize_blank_heading_becomes_other`；`test_normalize_drops_empty_text`；`test_merge_keeps_first_intro_and_group_order`；`test_merge_dedupes_and_merges_src`；`test_has_report_false_when_only_intro`。
- [ ] **Step 2: Run** `cd server && python -m pytest tests/test_report.py -q` — Expected: FAIL（模組不存在）。
- [ ] **Step 3: Implement** `server/report.py` 與 `server/prompts.py` 新增項。`REPORT_PROMPT` 照 spec「AI 提示詞」一節：引言 2–4 句、大標題 2–6 個、每個大標題 2–6 個子標題、title 4–14 字名詞短語、不套固定範本、待辦與 Q&A 不寫、說明保留數字日期條件但書。`REPORT_MERGE_PROMPT`：輸入多段草稿，只輸出重排後的「條目序號清單與分組名」，不輸出新文字（程式依序號取回原文字，避免改寫）。
- [ ] **Step 4: Run** 同上 — Expected: PASS。
- [ ] **Step 5: Commit** `feat(report): 完整整理的提示詞、schema 與正規化`

### Task 2: Claude API 客戶端

**Files:**
- Create: `server/anthropic_api.py`
- Modify: `server/requirements.txt`、`deploy/nas/requirements.txt`（加 `anthropic`，釘版本）
- Test: `server/tests/test_anthropic_api.py`

**Interfaces:**
- Produces：
  - `REPORT_API_MODEL = "claude-opus-5-5"`
  - `class ApiError(Exception)`
  - `def read_key() -> str | None`：讀 `os.environ.get("MEETINGS_ANTHROPIC_KEY_FILE", "/run/meetings/anthropic.key")`；strip 空白、換行、BOM；不存在或空回 `None`。
  - `def api_enabled() -> bool`：`read_key() is not None`。
  - `def call_api(system: str, user: str, schema: dict, *, client=None, timeout: float = 600) -> dict`：以強制 tool-use（`tool_choice` 指定單一工具，`input_schema=schema`）取得結構化輸出，用串流（`client.messages.stream(...)` → `get_final_message()`），`max_tokens=32000`。`stop_reason == "max_tokens"`、沒有 tool_use 區塊、或結果不是 dict → `raise ApiError`。任何 SDK 例外包成 `ApiError`，訊息只含例外類別名與 HTTP 狀態，**不含**金鑰或請求內容。`client` 參數供測試注入。

- [ ] **Step 1: Write failing tests**：`test_read_key_strips_whitespace_and_bom`、`test_read_key_missing_file_is_none`、`test_read_key_empty_is_none`、`test_call_api_returns_tool_input`（假 client）、`test_call_api_truncated_raises`（`stop_reason="max_tokens"`）、`test_call_api_no_tool_block_raises`、`test_call_api_wraps_sdk_error_without_leaking_key`（例外訊息含假金鑰 `sk-test-123`，斷言 `"sk-test-123" not in str(ApiError)`）。
- [ ] **Step 2: Run** `cd server && python -m pytest tests/test_anthropic_api.py -q` — Expected: FAIL。
- [ ] **Step 3: Implement** `server/anthropic_api.py`；`anthropic` 在函式內才 import（沒裝套件時 `api_enabled()` 與其他功能不受影響）。
- [ ] **Step 4: Run** 同上 — Expected: PASS。
- [ ] **Step 5: Commit** `feat(report): Claude API 客戶端（金鑰檔、串流、失敗不洩漏金鑰）`

### Task 3: `report` 動作（API 優先、退回 Max）

**Files:**
- Modify: `server/ai.py`（`ACTIONS`、`LABELS`、`HANDLERS`、`Ctx.__init__`、`handle_ai_job`、新增 `_report`、`_generate_report`）
- Test: `server/tests/test_report_action.py`；擴充 `server/tests/fake_claude.py`

**Interfaces:**
- Consumes：Task 1 `normalize_report`／`merge_reports`／`has_report`；Task 2 `api_enabled`、`call_api`、`ApiError`、`REPORT_API_MODEL`。
- Produces：
  - `Ctx.__init__(..., api_call=None)`：`api_call(system, user, schema) -> dict | None`；`handle_ai_job` 在 `api_enabled()` 時傳入（呼叫前後都檢查 `abort()`），否則 `None`。
  - `def _generate_report(ctx: Ctx, call) -> dict`：依 `chunk_lines(ctx.lines, ctx.budget)` 分段，每段 `call(P.REPORT_PROMPT, text, P.REPORT_SCHEMA)`；多段時再呼叫 `P.REPORT_MERGE_PROMPT`，程式依序號取回原文字；回傳 `merge_reports` 結果。
  - `def _report(ctx: Ctx, payload) -> Callable`：先用 `ctx.api_call` 跑 `_generate_report`；`ApiError` 或任何非 `ClaudeAborted` 的例外 → log 一行警告（不含金鑰）→ 整次用 `ctx.call` 重跑，`via="max"`。回傳的 `apply(m)`：`_backup(m, "summary", ("reportIntro", "reportPoints"))`；寫入 `m["summary"]["reportIntro"]`、`["reportPoints"]`；`m["reportMeta"] = {"via", "model", "at", "fp": _fingerprint(m["transcript"]), "stale": False}`；若 `m["summary"]` 沒有 `actionItems`／`qa`，用 Max（`ctx.call`）以 `_grounded_summary` 補；最後 `_reverify(m, ctx)`。`reportPoints` 為空 → `raise ClaudeError("完整整理沒有產生內容")`。
  - `LABELS["report"] = "完整整理"`；`ACTIONS`／`HANDLERS` 加入 `report`。

- [ ] **Step 1: Extend fake_claude** 讓 `REPORT_PROMPT` 回傳含引言與 2 個大標題的固定結果（src 引用第一行逐字稿，沿用現有 SUMMARY 的做法）；`REPORT_MERGE_PROMPT` 回傳序號清單。
- [ ] **Step 2: Write failing tests**（用 `handle_ai_job` + 假 Claude + 假 `api_call`，仿 `test_ai.py` 的 `sj`／`mk`）：`test_report_via_api_writes_items_and_meta`（`via == "api"`）；`test_report_api_error_falls_back_to_max`（`via == "max"`、無錯誤）；`test_report_api_truncated_falls_back`；`test_report_no_key_uses_max`；`test_report_empty_points_fails`；`test_report_does_not_touch_main_points`；`test_report_backup_restorable`；`test_report_abort_does_not_fall_back`（取消時不得再跑 Max）；`test_report_multi_chunk_merge_keeps_text`（把 `MAX_CHARS` 調小）。
- [ ] **Step 3: Run** `cd server && python -m pytest tests/test_report_action.py -q` — Expected: FAIL。
- [ ] **Step 4: Implement** 上述介面。API 與 Max 兩邊都失敗時，錯誤訊息同時列出兩個原因。`ClaudeLimitError`／`ClaudeTransientError` 照舊向上拋（沿用既有重試）。
- [ ] **Step 5: Run** 同上，再跑 `cd server && python -m pytest -q` — Expected: 全部 PASS，數字貼進回報。
- [ ] **Step 6: Commit** `feat(report): report 動作，API 優先、失敗退回 Max`

### Task 4: 與既有流程接上（重新整理、已過期、還原）

**Files:**
- Modify: `server/ai.py`（`_reprocess` 尾端）、`server/review.py`／`server/jobs.py` 中「換掉整份逐字稿」的寫入點（用 `grep -n 'm\["transcript"\] =' server/*.py` 找：新轉錄結果、`restore_raw`）
- Test: `server/tests/test_report_action.py`（追加）

**Interfaces:**
- Consumes：Task 3 `_generate_report`／`_report` 的 apply。
- Produces：`reportMeta.stale == True` 在逐字稿被整份換掉時；`reprocess` 結束時也產生 `report`。

- [ ] **Step 1: Write failing tests**：`test_reprocess_also_builds_report`；`test_new_transcript_marks_report_stale`；`test_name_correction_replaces_in_report_items`（確認人名訂正後 `reportIntro`／`reportPoints` 文字一併替換——預期既有機制已涵蓋，這支測試鎖住它）。
- [ ] **Step 2: Run** — Expected: 前兩支 FAIL，第三支若 PASS 代表既有機制已涵蓋。
- [ ] **Step 3: Implement**：`_reprocess` 在摘要之後呼叫 `_report`（沿用同一個 `ctx`，進度步驟新增 `STEP_REPORT = ("report", 60, "整理完整報告中")`）；`report` 失敗在 reprocess 內用 `_optional` 包住，只記 warning，不讓整個重新整理失敗。換逐字稿的寫入點加 `if isinstance(m.get("reportMeta"), dict): m["reportMeta"]["stale"] = True`。
- [ ] **Step 4: Run** `cd server && python -m pytest -q` — Expected: 全部 PASS。
- [ ] **Step 5: Commit** `feat(report): 重新整理帶完整整理；換逐字稿標已過期`

### Task 5: 前端共用工具 `reportOutline`

**Files:**
- Modify: `js/items.js`
- Test: `tests/items.test.js`

**Interfaces:**
- Produces（`js/items.js`）：
  - `hasReport(meeting: object): boolean`：`summary.reportPoints` 至少 1 條。
  - `reportOutline(summary: object): { intro: string, sections: { heading: string, subs: { title: string, text: string, item: object }[] }[] }`：依 `group` 第一次出現順序分組（用現有 `groupItems`）；`text` 以**第一個全形冒號「：」**切成 `title`／`text`，沒有冒號則 `title === ''`、`text` 為整段；`intro` 取 `reportIntro[0]` 文字，沒有則 `''`。
  - `ALL_SUMMARY_KEYS` 加入 `'reportIntro'`、`'reportPoints'`（人名訂正要全部套用）。

- [ ] **Step 1: Write failing tests**：`reportOutline splits title at first full-width colon`、`...without colon gives empty title`、`...keeps group first-seen order`、`hasReport false for legacy meeting`、`hasReport false when only intro`。
- [ ] **Step 2: Run** `npx vitest run tests/items.test.js` — Expected: FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** 同上 — Expected: PASS。
- [ ] **Step 5: Commit** `feat(report): 前端 reportOutline／hasReport`

### Task 6: Word 匯出（`js/docx.js`）

**Files:**
- Modify: `js/docx.js`（`documentXml` 內新增 `reportBody(meeting, want)` 分支）
- Test: `tests/docx.test.js`

**Interfaces:**
- Consumes：Task 5 `hasReport`、`reportOutline`。
- Produces：`documentXml` 在 `hasReport(meeting)` 時依 Global Constraints 的順序輸出；內部 helper `roman(n: number): string`（1→`i`、4→`iv`、9→`ix`、12→`xii`）。
  - 大標題段落：粗體 13pt，文字 `「${i + 1}. ${heading}」`，`keepNext`。
  - 子標題：`<w:ind w:left="720" w:hanging="360"/>`，開頭 `•　`，粗體小標＋「：」＋說明；`title === ''` 時只輸出說明。
  - Action Items／Q&A：標題用 `heading()`；條目前綴 `i.　`，縮排同上；Q&A 沿用現有「問：／答：」兩行並 `keepLines`；清單為空則整個區塊省略。
  - 受 `normalizeSections`：`actionItems`、`qa`、`transcript` 勾選仍生效；`mainPoints` 在有完整整理時不輸出。

- [ ] **Step 1: Write failing tests**（解 zip 後檢查 `word/document.xml`，沿用 `tests/docx.test.js` 現有取 XML 的方式）：`report: intro comes right after the date`、`report: headings numbered and bold`、`report: sub title bold followed by colon`、`report: action items use roman numerals`、`report: no 會議重點 heading when report exists`、`report: empty action items omits the block`、`report: escapes & < > in headings`、`report: lecture meeting without actionItems prints no 待辦 block`、`legacy meeting output unchanged`（用現有舊會議 fixture，斷言仍有「📌 會議重點 Main Point」）。
- [ ] **Step 2: Run** `npx vitest run tests/docx.test.js` — Expected: 新測試 FAIL，舊測試 PASS。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** `npx vitest run` — Expected: 全部 PASS，貼出 passed 數。
- [ ] **Step 5: Commit** `feat(report): Word 匯出新結構`

### Task 7: PDF／HTML 匯出（`js/export.js`）

**Files:**
- Modify: `js/export.js`（`meetingToHtmlBody`，加 `reportHtml`）；`css` 區 `STYLE` 加 `.rp-h`、`.rp-sub`
- Test: `tests/export.test.js`

**Interfaces:**
- Consumes：Task 5。
- Produces：`meetingToHtmlBody` 在 `hasReport` 時輸出：`<p class="intro">`、每個大標題 `<h2>${n}. …</h2>`、其下 `<ul>` 內 `<li><strong>小標：</strong>說明</li>`、Action Items 與 Q&A 用 `<ol type="i">`；其餘規則同 Task 6。所有文字經 `esc`。

- [ ] **Step 1: Write failing tests**：`html: report order intro, headings, action items, qa`、`html: sub title bold`、`html: no 會議重點 with report`、`html: legacy unchanged`、`html: escapes special chars`。
- [ ] **Step 2: Run** `npx vitest run tests/export.test.js` — Expected: FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** `npx vitest run` — Expected: 全部 PASS。
- [ ] **Step 5: Commit** `feat(report): PDF／HTML 匯出新結構`

### Task 8: 伺服器匯出（`server/render.py`）

**Files:**
- Modify: `server/render.py`（`meeting_to_markdown`、`meeting_to_docx`；新增 `_report_outline(m) -> tuple[str, list[tuple[str, list[tuple[str, str]]]]]`）
- Test: `server/tests/test_render_report.py`

**Interfaces:**
- Produces：`_report_outline(m)` 與 JS `reportOutline` 同規則（第一個全形冒號切分、group 第一次出現順序）。Markdown：引言一段、`## N. 大標題`、`- **小標：**說明`、`## Action Items`（`i.` 清單）、`## Q&A`。docx：用 `python-docx`，大標題 `add_heading(…, 1)`，子標題 `List Bullet` 並把小標設粗體。無 `reportPoints` 時輸出與改版前逐字相同。

- [ ] **Step 1: Write failing tests**：`md report order`、`md sub title bold`、`md legacy unchanged`、`docx report opens and has heading + bold run`（用 `python-docx` 讀回檔案）、`outline no colon gives empty title`。
- [ ] **Step 2: Run** `cd server && python -m pytest tests/test_render_report.py -q` — Expected: FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** `cd server && python -m pytest -q` — Expected: 全部 PASS。
- [ ] **Step 5: Commit** `feat(report): 伺服器 Markdown／docx 匯出新結構`

### Task 9: 畫面（按鈕、顯示、標籤）

**Files:**
- Modify: `js/app.js`（AI 處理列加按鈕、`meetingCard` 前面加完整整理區、`SEC_SRC` 加 `ri`／`rp`、`arrMap`／`texts` 加入）、`js/items.js`（新增 `REPORT_SECTIONS` 常數）、`index.html`／`css/styles.css`（僅必要的小樣式）
- Test: `tests/app.dom.test.js`

**Interfaces:**
- Consumes：Task 3 動作名 `report`（`runServerAI(id, 'report', '完整整理中…', 'summary')`，仿 `app.js:1746`）；Task 5 `hasReport`、`reportOutline`。
- Produces：AI 處理第二列加「📄 完整整理」按鈕（有逐字稿才顯示）；`hasReport` 時，摘要卡最上方顯示「📄 完整整理」區：引言、大標題（粗體）、子標題（粗體小標＋冒號）；每條仍可點回逐字稿（沿用 `data-jump`／`src-chip`）；區塊標題旁小標籤「由 API 產生」／「由 Max 產生」；`reportMeta.stale` 時顯示提示「逐字稿已更換，請重按完整整理」。原有「會議重點」等區塊保留（資料不消失）。編輯模式可編輯引言與子標題（`SEC_SRC.ri = ['summary','reportIntro']`、`rp = ['summary','reportPoints']`）。

- [ ] **Step 1: Write failing tests**（`app.dom.test.js` 仿既有寫法）：`detail shows 完整整理 button when transcript exists`、`detail renders intro and grouped sub titles when report exists`、`detail shows via label`、`detail shows stale hint`、`detail legacy meeting has no 完整整理 section`。
- [ ] **Step 2: Run** `npx vitest run tests/app.dom.test.js` — Expected: FAIL。
- [ ] **Step 3: Implement**。
- [ ] **Step 4: Run** `npx vitest run` — Expected: 全部 PASS。
- [ ] **Step 5: Commit** `feat(report): 詳情頁完整整理按鈕與顯示`

### Task 10: 部署設定與文件（不部署）

**Files:**
- Modify: `deploy/nas/compose.yaml`（加唯讀掛載 `/volume1/docker/meetings/anthropic.key:/run/meetings/anthropic.key:ro`）、`deploy/nas/README.md`、`README.md`（加一段：金鑰放哪、沒金鑰會怎樣、費用提醒）

- [ ] **Step 1: Edit** 三個檔案；README 明寫「金鑰請自己放進 NAS，不要貼到對話或 git」。
- [ ] **Step 2: Verify** `git diff --stat` 只含上述三檔；`grep -rn "sk-ant" .` 無結果。
- [ ] **Step 3: Commit** `docs(report): 部署設定與金鑰說明`

### Task 11: 驗收（CEO 規則）

- [ ] **Step 1:** 跑 `cd server && python -m pytest -q` 與 `npx vitest run`，貼實際數字。
- [ ] **Step 2:** 派 code-reviewer 審整個分支（重點：金鑰洩漏、退回路徑、匯出舊格式不變）。
- [ ] **Step 3:** 派 test-automator 依 spec「完成標準」六條逐條驗收，每條 ✅／❌ 附證據；❌ 退回原實作者，最多 3 輪。
- [ ] **Step 4:** 用 LibreOffice 或 Word 實際開啟一份匯出的 `.docx`（內含 `& < >` 的標題），截圖確認不報損毀。
- [ ] **Step 5:** 向使用者回報；部署 NAS 前先問使用者。
