# DD會議紀錄 NAS 版：設計規格

日期：2026-10-05
狀態：使用者授權整夜自主實作（「中間我都同意」）。本檔記錄所有預設決定，早上一起審。

## 1. 目標

把 DD會議紀錄（原本是 GitHub Pages 上的手機網頁，直接呼叫 Gemini／Groq，資料同步到 GitHub）搬到家裡的 Synology NAS：

1. **不用 Gemini／Groq 金鑰。** 語音轉文字用使用者電腦的顯示卡（faster-whisper large-v3 ＋ pyannote 分說話者）；所有文字處理（摘要、加強、學習筆記、問答、掃詞、翻譯）用 Claude Max（NAS 上的 `claude -p`）。
2. **資料存 NAS**，不再寫 GitHub。舊資料（`hpisokplay/meeting-notes-data` 的 `meetings.json`：26 場、10 個分類）全部匯入，一筆不刪。GitHub 上的舊 repo 與舊網頁原封不動保留。
3. **網頁與手機都能開**：`https://ddys.tail95e8ec.ts.net:8443/`（Tailscale 內網，443 已被定錨使用）。PWA 可加到主畫面。
4. **大檔案**：單檔 ≥ 1000 MB、長度 ≥ 8 小時。上傳分塊、可續傳；轉錄在電腦顯示卡上跑。
5. **現有功能全部保留**（清單見 §7）。
6. **Telegram**：沿用現有 @ysdd_claude_bot（tgbridge），加一個 `meetings-db` 技能：問會議內容、要檔案（Word／Markdown／逐字稿／錄音）、傳錄音進來轉。轉好時主動通知。

## 2. 實測依據（spike，2026-10-05）

| 項目 | 結果 |
|---|---|
| 電腦 RTX 3060 Ti，large-v3 float16 batch 8 | 69 分鐘 110 秒（37x）；60 分鐘 59 秒（61x） |
| pyannote community-1，GPU | 34x；單人講課正確判為 1 人；會議分出 4 人 |
| NAS J4125（無 AVX），small int8 | 1.5x，錯字明顯多，無分說話者 |
| sherpa-onnx 分說話者 | 淘汰（單人講課切成 12～220 人） |
| `claude -p --json-schema` | 可用，回傳 `structured_output`；Sonnet 5.5 context 1M |

已知 Whisper 毛病：安靜段落會產生「中文字幕由 Amara.org 社群提供」之類的幻覺句，以及回聲提示語。必須過濾。

## 3. 架構

```
手機／電腦瀏覽器 ──HTTPS(Tailscale serve :8443)──► NAS 容器 meetings（FastAPI :8778）
                                                   ├─ 靜態前端（PWA）
                                                   ├─ REST API、分塊上傳
                                                   ├─ 工作佇列（jobs/）
                                                   ├─ AI 工作執行緒 → claude -p（Max 訂閱）
                                                   ├─ NAS 備援轉錄（CPU small，預設等 20 分鐘沒人領才啟動）
                                                   └─ TG 通知（Bot API，共用 tgbridge token）
電腦 GPU 轉錄器（Windows 開機自動跑）──HTTPS 輪詢──► /api/worker/*
tgbridge 機器人（既有容器）── 讀寫 /projects/meetings/ 檔案 ──► 同一份資料
```

### 3.1 NAS 上的路徑

| 用途 | NAS 路徑 | 容器內 |
|---|---|---|
| 程式（前端＋後端，掛載，免重建） | `/volume1/docker/meetings/app` | `/app` |
| 資料主本 | `/volume1/projects/meetings` | `/data`（tgbridge 看到 `/projects/meetings`） |
| 備份 | `/volume1/docker/backups/meetings` | `/backup` |
| Claude 設定 | `/volume1/docker/meetings/claude-config` | `/claude-home/.claude` |
| Claude Max token（唯讀） | `/volume1/docker/worker/home/.claude-token` | `/claude-home/.claude-token` |
| TG token／allowed_ids（唯讀） | `/volume1/docker/tgbridge/{token,allowed_ids}` | `/run/tg/*` |
| 轉錄器金鑰（唯讀） | `/volume1/docker/meetings/worker.key` | `/run/meetings/worker.key` |

### 3.2 資料夾格式（`/data`）

```
meetings/<id>.json        會議主檔（舊格式欄位全保留，見 §4）
meetings/<id>.md          給機器人讀的純文字版（標題、日期、摘要、逐字稿含說話者）— 每次存檔重產
exports/<id>.docx         Word 匯出 — 每次存檔重產（給 TG 傳檔）
audio/<id>/<n>-<原檔名>    原始錄音（多檔依序）
groups.json               分類 [{id,name,color}]
index.json                會議索引 [{id,title,createdAt,group,durationSec,status}]（給機器人）
trash/<id>.json           刪除的會議先移到這裡（保留，不真的刪）
uploads/<uploadId>/       分塊上傳暫存（完成後搬到 audio/）
inbox/                    丟錄音進來 → 自動建立會議並排轉錄（TG 用）
jobs/{queue,running,done,failed}/<jobId>.json
settings.json             伺服器設定（Claude 型號、備援開關、等待分鐘、TG 通知開關）
.restart                  存在時伺服器自行結束，由 Docker 重啟（更新程式用）
```

寫檔一律「寫 .tmp → os.replace」原子寫入。同一場會議的寫入用一把鎖（程序內 threading.Lock per id）。

## 4. 會議資料格式

沿用舊格式（舊資料直接可用）：

```
id, title, createdAt, updatedAt, editedAt, group,
transcript[{speaker, text, t}]      t = 秒（舊資料可能是 "MM:SS" 字串或 fi 欄位，原樣保留）
summary{actionItems[], mainPoints[], qa[]}
notes{outline, concepts, tables, figures, quiz}
alt{...}  terms{items[], scannedAt}  chat[{q,a,at}]  translations{zh,en,ja}
durationSec, audioMode, audioParts
```

新增欄位：

```
status: "uploading" | "queued" | "transcribing" | "summarizing" | "done" | "failed"
progress: {stage, pct, msg, engine}      engine: "gpu" | "nas-cpu"
audio: [{file, name, size, durationSec}] 對應 audio/<id>/
speakers: {"說話者 1": "Jeffrey"}         說話者改名對照（舊的整份改名功能照舊直接改 transcript）
error: string
```

## 5. API（全部 JSON，除上傳與音檔）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/status` | 版本、轉錄器上線狀態（最後一次輪詢時間）、佇列數、Claude 型號、磁碟剩餘 |
| GET | `/api/meetings` | 列表（不含逐字稿；含 status/progress） |
| GET | `/api/meetings/{id}` | 完整會議 |
| PUT | `/api/meetings/{id}` | 整份存檔（前端編輯用；伺服器補 updatedAt） |
| DELETE | `/api/meetings/{id}` | 移到 trash/ |
| GET/PUT | `/api/groups` | 分類 |
| POST | `/api/uploads` | `{name,size,mime}` → `{uploadId, chunkSize}`（chunk 8 MB） |
| GET | `/api/uploads/{uid}` | 已收到哪些塊（續傳用） |
| PUT | `/api/uploads/{uid}/{index}` | 上傳一塊（原始位元組） |
| POST | `/api/meetings` | `{title, group, uploads:[uid...]}` → 建立會議、搬音檔、排轉錄 → 會議 |
| GET | `/api/meetings/{id}/audio/{n}` | 播放／下載錄音（支援 Range） |
| POST | `/api/meetings/{id}/retranscribe` | 重新轉錄（有錄音的會議） |
| POST | `/api/meetings/{id}/ai` | `{action, ...}` → `{jobId}`；結果由伺服器寫回會議 |
| GET | `/api/jobs/{jobId}` | 工作狀態 `{state, progress, error}` |
| POST | `/api/jobs/{jobId}/cancel` | 取消 |
| GET | `/api/meetings/{id}/export.docx` | Word |
| GET | `/api/export` | 全部資料備份（舊備份格式） |
| POST | `/api/import` | 匯入舊備份格式（合併，不刪任何現有資料） |
| GET/PUT | `/api/settings` | 伺服器設定 |
| GET | `/api/worker/next` | 轉錄器領工作（Header `X-Worker-Key`）；同時當心跳 |
| POST | `/api/worker/jobs/{jobId}/progress` | 回報進度 |
| POST | `/api/worker/jobs/{jobId}/result` | 交回 `{segments, durationSec, engine}` |
| POST | `/api/worker/jobs/{jobId}/fail` | 回報失敗 |

AI `action`：

| action | 參數 | 寫回 |
|---|---|---|
| `summary` | — | `summary`（舊版存進 alt 由前端既有邏輯處理；伺服器重做時備份舊版到 `alt.summary`） |
| `enhance` | `section`: actionItems/mainPoints/qa | 先備份到 `alt`，再寫新版 |
| `notes` | — | `notes` |
| `notes_enhance` | `section`: outline/concepts/tables/figures/quiz | 先備份到 `alt`，再寫新版 |
| `ask` | `question` | 追加到 `chat` |
| `terms` | — | `terms.items`、`terms.scannedAt` |
| `translate` | `lang`: zh/en/ja | `translations[lang]` |

AI 工作在伺服器單一執行緒排隊執行（Claude 一次一件）。提示詞從舊 `js/gemini.js` 移植，語意不變，JSON Schema 不變。

## 6. 轉錄流程

1. 前端分塊上傳（8 MB／塊，失敗自動重試、可續傳；上傳時保持螢幕不暗）。
2. `POST /api/meetings` 建立會議：`status=queued`，產生 `transcribe` 工作。
3. 電腦轉錄器每 10 秒輪詢 `/api/worker/next`，領到就：
   1. 下載錄音（支援續傳）→ ffmpeg 轉 16 kHz 單聲道 WAV（多檔依序，各自記錄起點）
   2. faster-whisper large-v3（GPU，batch 8，word timestamps，VAD）
   3. pyannote community-1（GPU）分說話者
   4. 逐字對齊說話者 → 合併成句（同一人且間隔 < 2 秒併成一段，每段 ≤ 約 60 字遇標點切）
   5. 過濾幻覺句（字幕志工、Amara、謝謝收看、提示語回聲、重複句）
   6. 簡轉繁（OpenCC s2twp）
   7. 說話者依出場順序命名「說話者 1、2…」
   8. 交回 `segments`
4. 伺服器收到 → `status=summarizing` → 自動排 `summary` AI 工作 → `done` → TG 通知（若開）。
5. 備援：`transcribe` 工作排隊超過 `fallbackMinutes`（預設 20）沒人領，且備援開啟 → NAS 用 CPU small 模型轉（無分說話者，全部標「說話者」），進度標示 `nas-cpu`。之後可按「重新轉錄」改用電腦轉。
6. 轉錄器失聯（領了工作但 10 分鐘沒回報）→ 工作退回佇列。

## 7. 功能保留清單（對照舊版）

保留：多檔上傳依檔名排序、進度頁（耗時、目前階段）、停止／取消並刪除、會議列表、搜尋（標題／逐字稿／摘要）、分類（新增、改名、刪除、顏色、未分類）、詳細頁（改標題、原文/中文/English/日本語、分享、PDF、Word、匯出段落選擇）、會議摘要／學習筆記兩模式、加強（3＋5 個）、換版／合併、編輯模式刪項目、複製、收合、點項目跳到出處、說話者色塊、整份改說話者名、逐句編輯、時間戳、掃詞（自動挑、分組、草稿、重掃、手動新增、套用全部）、問這場會議、刪除會議、匯出／匯入備份、強制更新、加到主畫面提示、開機畫面。

移除（因為不再需要）：Gemini／Groq 金鑰設定、型號鎖定、金鑰用量顯示、GitHub 同步設定、瀏覽器內切音檔。

新增：錄音存 NAS、播放器（點時間戳跳到該處）、重新轉錄、NAS／轉錄器狀態、Claude 型號選擇（預設 sonnet）、TG 通知開關。

## 8. 電腦轉錄器（worker/）

- Python，獨立 venv：`%LOCALAPPDATA%\MeetingWorker\venv`；模型：faster-whisper large-v3（HF 快取）、pyannote community-1 放 `%LOCALAPPDATA%\MeetingWorker\models\pyannote-c1`（本機檔案，不需要 HF 權杖）。
- 設定檔 `%LOCALAPPDATA%\MeetingWorker\config.json`：`{server, key}`。
- 開機自動跑：Windows 工作排程器「登入時」，pythonw 隱藏視窗；日誌 `worker.log`（滾動）。
- 一次一件；GPU 記憶體：whisper 用完釋放再跑 pyannote。

## 9. Claude 呼叫

`claude -p --model <型號> --tools "" --output-format json --no-session-persistence --json-schema <schema> --system-prompt <提示> --max-turns 3`，逐字稿從 stdin 進。取 `structured_output`。

- 逾時：每件 30 分鐘。
- 用量上限／暫時錯誤：工作標記「等待重試」，30 分鐘後自動重試，最多 6 次；訊息照實顯示給使用者。
- 逐字稿超過 600k 字元才分段（Sonnet 1M context；8 小時約 15 萬字，通常不需要）。

## 10. Telegram

- 技能 `bot-skill/meetings-db/SKILL.md` → 安裝到 `/volume1/docker/worker/home/.claude/skills/meetings-db/`（安裝前備份）。
- 問答：讀 `index.json` 找會議，讀 `meetings/<id>.md` 回答；跨會議搜尋用 grep。
- 要檔案：`cp exports/<id>.docx` 或 `meetings/<id>.md` 到 `/projects/_outbox/`；錄音 ≤ 45 MB 才傳，否則給網址。
- 傳錄音進來：機器人存到 `/projects/meetings/inbox/`（≤ 20 MB，Telegram 下載上限），伺服器自動建會議排轉錄。
- 通知：伺服器用 Bot API 直接傳「《標題》轉好了」＋網址。

## 11. 部署

- `deploy/nas/`：`Dockerfile`（node:22-bookworm-slim ＋ python3 ＋ ffmpeg ＋ claude-code@2.1.280 釘版本）、`compose.yaml`、`claude-wrapper.sh`（同定錨）、`requirements.txt`、`install.sh`（一次性，需 sudo：建資料夾、`docker compose up -d --build`、`tailscale serve --bg --https=8443 http://127.0.0.1:8778`）、`update_app.sh`（tar over ssh ＋ `.restart`）。
- ssh 使用者不能跑 docker → **安裝那一步要使用者本人輸入 sudo 密碼**（一行指令）。其餘（傳檔、匯入資料、裝技能）我用 ssh 做。

## 12. 測試

- 後端：pytest（API、分塊上傳續傳、工作佇列、AI 寫回、匯入合併不刪資料、Claude 呼叫以假程式替代）。
- 轉錄器：pytest（對齊、合併、幻覺過濾、多檔起點偏移）。
- 前端：vitest（保留仍適用的舊測試，新增 api.js 測試）。
- 端到端：電腦本機跑伺服器＋真轉錄器＋真 Claude，用「元澄建廠二次討論」跑完整流程；再做一個 8 小時合成檔測長度。
- NAS：安裝後在 NAS 上重跑同一流程。

## 13. 不做（這次）

- App 內直接錄音（舊版也沒有；iOS 背景長時間錄音不可靠）。
- 多使用者／登入（Tailscale 內網即是門禁）。
