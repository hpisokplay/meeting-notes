# 會議記錄 App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一個掛在 GitHub Pages 的手機網頁 App（PWA），上傳會議錄音後透過使用者的 Gemini API 金鑰產生繁體中文逐字稿與結構化摘要，並將記錄存於手機本機 IndexedDB。

**Architecture:** 純前端靜態網站，ES Modules，無建置步驟（瀏覽器直接載入相同 .js）。核心邏輯模組（設定、儲存、Gemini 客戶端）以 Vitest 做 TDD；UI／PWA／部署以手動端到端驗證。運算全外包給 Gemini Files API + generateContent。

**Tech Stack:** HTML5 + CSS + Vanilla JS (ES Modules)、IndexedDB、localStorage、Service Worker/PWA、Google Gemini API；開發期測試用 Node + Vitest + fake-indexeddb + jsdom。

## Global Constraints

- 目標平台：iPhone iOS Safari（手機優先版面，適配 safe-area）。
- 部署產物必須是**純靜態檔案**（可直接 GitHub Pages 託管，無伺服器、無建置）。node_modules 與測試僅供開發，不進部署路徑。
- 所有使用者可見文字與 AI 輸出一律**繁體中文**（英文夾雜保留原文）。
- API 金鑰只存於使用者瀏覽器 `localStorage`，絕不寫入原始碼或提交到 git。
- **不**儲存原始音檔；每場會議只存逐字稿與摘要。
- 摘要固定三區塊：重點條列(keyPoints)、待辦事項(actionItems)、決議事項(decisions)。
- Gemini 模型：`gemini-2.5-flash`（具音訊能力）。實作 Task 4 時先以 `curl` 對照當時官方文件確認 model id 仍有效，若已更名改用當時對應的 audio-capable flash 模型。

---

### Task 1: 專案骨架與測試環境

**Files:**
- Create: `package.json`
- Create: `vitest.config.js`
- Create: `.gitignore`
- Create: `js/.gitkeep`
- Create: `tests/smoke.test.js`

**Interfaces:**
- Consumes: 無
- Produces: 可執行 `npm test`；目錄結構 `js/`（原始碼）、`tests/`（測試）。

- [ ] **Step 1: 建立 package.json**

```json
{
  "name": "meeting-transcriber",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "vitest run",
    "dev": "vitest"
  },
  "devDependencies": {
    "vitest": "^2.1.0",
    "fake-indexeddb": "^6.0.0",
    "jsdom": "^25.0.0"
  }
}
```

- [ ] **Step 2: 建立 vitest.config.js**

```js
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
  },
});
```

- [ ] **Step 3: 建立 .gitignore**

```
node_modules/
.DS_Store
*.log
```

- [ ] **Step 4: 建立佔位檔與煙霧測試**

`js/.gitkeep`：空檔案。

`tests/smoke.test.js`：
```js
import { describe, it, expect } from 'vitest';

describe('smoke', () => {
  it('runs', () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 5: 安裝依賴並執行測試**

Run: `npm install && npm test`
Expected: smoke 測試 PASS（1 passed）。

- [ ] **Step 6: Commit**

```bash
git add package.json vitest.config.js .gitignore js/.gitkeep tests/smoke.test.js
git commit -m "chore: 專案骨架與 Vitest 測試環境"
```

---

### Task 2: 設定模組（API 金鑰）

**Files:**
- Create: `js/settings.js`
- Test: `tests/settings.test.js`

**Interfaces:**
- Consumes: 無
- Produces:
  - `getApiKey(): string`（無則回空字串）
  - `setApiKey(key: string): void`（自動去除前後空白）
  - `hasApiKey(): boolean`

- [ ] **Step 1: 寫失敗測試**

`tests/settings.test.js`：
```js
import { beforeEach, describe, it, expect } from 'vitest';
import { getApiKey, setApiKey, hasApiKey } from '../js/settings.js';

beforeEach(() => localStorage.clear());

describe('settings', () => {
  it('預設沒有金鑰', () => {
    expect(getApiKey()).toBe('');
    expect(hasApiKey()).toBe(false);
  });

  it('設定後可讀回並去除空白', () => {
    setApiKey('  abc123  ');
    expect(getApiKey()).toBe('abc123');
    expect(hasApiKey()).toBe(true);
  });
});
```

- [ ] **Step 2: 執行確認失敗**

Run: `npx vitest run tests/settings.test.js`
Expected: FAIL（找不到 `../js/settings.js`）。

- [ ] **Step 3: 實作 js/settings.js**

```js
const KEY = 'gemini_api_key';

export function getApiKey() {
  return localStorage.getItem(KEY) || '';
}

export function setApiKey(key) {
  localStorage.setItem(KEY, (key || '').trim());
}

export function hasApiKey() {
  return getApiKey().length > 0;
}
```

- [ ] **Step 4: 執行確認通過**

Run: `npx vitest run tests/settings.test.js`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add js/settings.js tests/settings.test.js
git commit -m "feat: 設定模組（Gemini 金鑰存取）"
```

---

### Task 3: 儲存模組（IndexedDB 會議記錄）

**Files:**
- Create: `js/store.js`
- Test: `tests/store.test.js`

**Interfaces:**
- Consumes: 無
- Produces（Meeting 型別見下）：
  - `save(meeting: Meeting): Promise<Meeting>`
  - `get(id: string): Promise<Meeting|null>`
  - `list(): Promise<Meeting[]>`（依 createdAt 由新到舊）
  - `remove(id: string): Promise<void>`
  - `exportAll(): Promise<string>`（JSON 字串）
  - Meeting 結構：`{ id:string, title:string, createdAt:number, transcript:string, summary:{ keyPoints:string[], actionItems:string[], decisions:string[] } }`

- [ ] **Step 1: 寫失敗測試**

`tests/store.test.js`：
```js
import 'fake-indexeddb/auto';
import { describe, it, expect } from 'vitest';
import { save, get, list, remove, exportAll } from '../js/store.js';

function make(id, createdAt, title) {
  return {
    id, createdAt, title,
    transcript: '逐字稿內容',
    summary: { keyPoints: ['重點一'], actionItems: [], decisions: ['決議一'] },
  };
}

describe('store', () => {
  it('存取與清單依時間排序（新到舊）', async () => {
    await save(make('a', 1000, '第一場'));
    await save(make('b', 3000, '第二場'));
    await save(make('c', 2000, '第三場'));

    const one = await get('a');
    expect(one.title).toBe('第一場');
    expect(one.summary.keyPoints).toEqual(['重點一']);

    const all = await list();
    expect(all.map(m => m.id)).toEqual(['b', 'c', 'a']);
  });

  it('刪除', async () => {
    await save(make('x', 1, 'X'));
    await remove('x');
    expect(await get('x')).toBeNull();
  });

  it('匯出為 JSON 字串含 meetings 陣列', async () => {
    await save(make('e', 5, 'E'));
    const json = await exportAll();
    const parsed = JSON.parse(json);
    expect(Array.isArray(parsed.meetings)).toBe(true);
    expect(parsed.meetings.some(m => m.id === 'e')).toBe(true);
  });
});
```

- [ ] **Step 2: 執行確認失敗**

Run: `npx vitest run tests/store.test.js`
Expected: FAIL（找不到 `../js/store.js`）。

- [ ] **Step 3: 實作 js/store.js**

```js
const DB_NAME = 'meetings-db';
const STORE = 'meetings';
const VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function run(store, mode, fn) {
  return openDb().then(db => new Promise((resolve, reject) => {
    const os = db.transaction(store, mode).objectStore(store);
    const req = fn(os);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }));
}

export async function save(meeting) {
  await run(STORE, 'readwrite', os => os.put(meeting));
  return meeting;
}

export async function get(id) {
  const result = await run(STORE, 'readonly', os => os.get(id));
  return result || null;
}

export async function list() {
  const all = (await run(STORE, 'readonly', os => os.getAll())) || [];
  return all.sort((a, b) => b.createdAt - a.createdAt);
}

export async function remove(id) {
  await run(STORE, 'readwrite', os => os.delete(id));
}

export async function exportAll() {
  const meetings = await list();
  return JSON.stringify({ exportedAt: Date.now(), meetings }, null, 2);
}
```

- [ ] **Step 4: 執行確認通過**

Run: `npx vitest run tests/store.test.js`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add js/store.js tests/store.test.js
git commit -m "feat: 儲存模組（IndexedDB 會議記錄）"
```

---

### Task 4: Gemini 客戶端（上傳＋辨識＋摘要）

**Files:**
- Create: `js/gemini.js`
- Test: `tests/gemini.test.js`

**Interfaces:**
- Consumes: 無
- Produces:
  - `transcribeAndSummarize(file: File|Blob, apiKey: string, opts?: { onProgress?: (msg:string)=>void }): Promise<{ transcript:string, summary:{ keyPoints:string[], actionItems:string[], decisions:string[] } }>`

**先驗證（實作前執行一次，非測試步驟）：** 用終端確認 model id 與 Files API 仍有效——
`curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_KEY"` 應列出含 `gemini-2.5-flash`（或當時的 audio-capable flash 模型）。若名稱不同，更新 `js/gemini.js` 的 `MODEL` 常數。

- [ ] **Step 1: 寫失敗測試**

`tests/gemini.test.js`：
```js
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { transcribeAndSummarize } from '../js/gemini.js';

beforeEach(() => { vi.restoreAllMocks(); });

function jsonResponse(obj, headers = {}) {
  return {
    ok: true,
    status: 200,
    headers: { get: (h) => headers[h] || null },
    json: async () => obj,
    text: async () => JSON.stringify(obj),
  };
}

describe('gemini', () => {
  it('沒有金鑰時丟錯', async () => {
    await expect(transcribeAndSummarize(new Blob(['x']), '')).rejects.toThrow('金鑰');
  });

  it('happy path：上傳→ACTIVE→產生 JSON', async () => {
    const modelJson = {
      candidates: [{
        content: { parts: [{ text: JSON.stringify({
          transcript: '大家好',
          keyPoints: ['重點A'],
          actionItems: ['待辦B'],
          decisions: ['決議C'],
        }) }] },
      }],
    };
    const fetchMock = vi.fn()
      // 1) start resumable → 回傳 upload url header
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up.example/putidid' }))
      // 2) upload bytes finalize → 回傳 file ACTIVE
      .mockResolvedValueOnce(jsonResponse({ file: { uri: 'https://files/abc', name: 'files/abc', state: 'ACTIVE', mimeType: 'audio/mpeg' } }))
      // 3) generateContent → 回傳結構化 JSON
      .mockResolvedValueOnce(jsonResponse(modelJson));
    vi.stubGlobal('fetch', fetchMock);

    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mpeg' });
    file.name = 'meeting.mp3';

    const result = await transcribeAndSummarize(file, 'KEY');
    expect(result.transcript).toBe('大家好');
    expect(result.summary.keyPoints).toEqual(['重點A']);
    expect(result.summary.actionItems).toEqual(['待辦B']);
    expect(result.summary.decisions).toEqual(['決議C']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
```

- [ ] **Step 2: 執行確認失敗**

Run: `npx vitest run tests/gemini.test.js`
Expected: FAIL（找不到 `../js/gemini.js`）。

- [ ] **Step 3: 實作 js/gemini.js**

```js
const BASE = 'https://generativelanguage.googleapis.com';
const MODEL = 'gemini-2.5-flash'; // 見 Task 4「先驗證」；若已更名，改此處

const PROMPT = `你是專業的會議記錄助理。輸入是一段會議錄音。請完成：
1. 產出完整的繁體中文逐字稿；若有英文夾雜，保留英文原文。
2. 依逐字稿整理三類：重點條列(keyPoints)、待辦事項(actionItems)、決議事項(decisions)。
所有中文一律使用繁體中文（台灣用語）。某類別沒有內容時回傳空陣列。`;

const SCHEMA = {
  type: 'object',
  properties: {
    transcript: { type: 'string' },
    keyPoints: { type: 'array', items: { type: 'string' } },
    actionItems: { type: 'array', items: { type: 'string' } },
    decisions: { type: 'array', items: { type: 'string' } },
  },
  required: ['transcript', 'keyPoints', 'actionItems', 'decisions'],
};

async function uploadFile(file, apiKey, onProgress) {
  onProgress && onProgress('上傳音檔中…');
  const mime = file.type || 'audio/mpeg';
  const start = await fetch(`${BASE}/upload/v1beta/files?key=${apiKey}`, {
    method: 'POST',
    headers: {
      'X-Goog-Upload-Protocol': 'resumable',
      'X-Goog-Upload-Command': 'start',
      'X-Goog-Upload-Header-Content-Length': String(file.size),
      'X-Goog-Upload-Header-Content-Type': mime,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ file: { display_name: file.name || 'meeting-audio' } }),
  });
  if (!start.ok) throw new Error(`上傳啟動失敗 (${start.status})`);
  const uploadUrl = start.headers.get('X-Goog-Upload-URL');
  if (!uploadUrl) throw new Error('未取得上傳網址');

  const up = await fetch(uploadUrl, {
    method: 'POST',
    headers: {
      'X-Goog-Upload-Command': 'upload, finalize',
      'X-Goog-Upload-Offset': '0',
    },
    body: file,
  });
  if (!up.ok) throw new Error(`上傳失敗 (${up.status})`);
  const info = await up.json();
  return info.file; // { uri, name, state, mimeType }
}

async function waitActive(fileInfo, apiKey, onProgress) {
  let { state, uri, name, mimeType } = fileInfo;
  while (state === 'PROCESSING') {
    onProgress && onProgress('音檔處理中…');
    await new Promise(r => setTimeout(r, 2000));
    const res = await fetch(`${BASE}/v1beta/${name}?key=${apiKey}`);
    if (!res.ok) throw new Error(`檔案狀態查詢失敗 (${res.status})`);
    const f = await res.json();
    state = f.state; uri = f.uri; name = f.name; mimeType = f.mimeType || mimeType;
  }
  if (state !== 'ACTIVE') throw new Error(`音檔處理失敗 (${state})`);
  return { uri, mimeType };
}

async function generate(fileUri, mimeType, apiKey, onProgress) {
  onProgress && onProgress('辨識與摘要中…');
  const res = await fetch(`${BASE}/v1beta/models/${MODEL}:generateContent?key=${apiKey}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      contents: [{
        parts: [
          { file_data: { mime_type: mimeType, file_uri: fileUri } },
          { text: PROMPT },
        ],
      }],
      generationConfig: {
        responseMimeType: 'application/json',
        responseSchema: SCHEMA,
      },
    }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`辨識失敗 (${res.status})：${t.slice(0, 200)}`);
  }
  const data = await res.json();
  const text = data && data.candidates && data.candidates[0]
    && data.candidates[0].content && data.candidates[0].content.parts
    && data.candidates[0].content.parts[0] && data.candidates[0].content.parts[0].text;
  if (!text) throw new Error('未取得辨識結果');
  return JSON.parse(text);
}

export async function transcribeAndSummarize(file, apiKey, opts = {}) {
  const onProgress = opts.onProgress;
  if (!apiKey) throw new Error('尚未設定 API 金鑰');
  const fileInfo = await uploadFile(file, apiKey, onProgress);
  const active = await waitActive(fileInfo, apiKey, onProgress);
  const mime = active.mimeType || file.type || 'audio/mpeg';
  const result = await generate(active.uri, mime, apiKey, onProgress);
  return {
    transcript: result.transcript || '',
    summary: {
      keyPoints: result.keyPoints || [],
      actionItems: result.actionItems || [],
      decisions: result.decisions || [],
    },
  };
}
```

- [ ] **Step 4: 執行確認通過**

Run: `npx vitest run tests/gemini.test.js`
Expected: PASS（2 passed）。

- [ ] **Step 5: 全套測試回歸**

Run: `npm test`
Expected: 全部 PASS（smoke + settings + store + gemini）。

- [ ] **Step 6: Commit**

```bash
git add js/gemini.js tests/gemini.test.js
git commit -m "feat: Gemini 客戶端（上傳＋辨識＋結構化摘要）"
```

---

### Task 5: UI 與頁面（清單／新增／詳情／設定／匯出）

**Files:**
- Create: `index.html`
- Create: `css/styles.css`
- Create: `js/app.js`
- Create: `js/format.js`（純函式，可測）
- Test: `tests/format.test.js`

**Interfaces:**
- Consumes: `settings.js`、`store.js`、`gemini.js`、`format.js`
- Produces: hash 路由 SPA（`#/`、`#/new`、`#/m/<id>`、`#/settings`）
  - `js/format.js`：`formatDate(ts:number): string`、`defaultTitle(fileName:string, ts:number): string`

- [ ] **Step 1: 寫 format.js 失敗測試**

`tests/format.test.js`：
```js
import { describe, it, expect } from 'vitest';
import { formatDate, defaultTitle } from '../js/format.js';

describe('format', () => {
  it('formatDate 回傳 YYYY-MM-DD HH:mm', () => {
    const ts = new Date('2026-07-18T09:05:00').getTime();
    expect(formatDate(ts)).toBe('2026-07-18 09:05');
  });

  it('defaultTitle 用檔名去副檔名', () => {
    expect(defaultTitle('週會 2026.m4a', 0)).toBe('週會 2026');
  });

  it('defaultTitle 檔名為空時用日期', () => {
    const ts = new Date('2026-07-18T09:05:00').getTime();
    expect(defaultTitle('', ts)).toBe('會議 2026-07-18 09:05');
  });
});
```

- [ ] **Step 2: 執行確認失敗**

Run: `npx vitest run tests/format.test.js`
Expected: FAIL（找不到 `../js/format.js`）。

- [ ] **Step 3: 實作 js/format.js**

```js
function pad(n) { return String(n).padStart(2, '0'); }

export function formatDate(ts) {
  const d = new Date(ts);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function defaultTitle(fileName, ts) {
  const base = (fileName || '').replace(/\.[^.]+$/, '').trim();
  return base || `會議 ${formatDate(ts)}`;
}
```

- [ ] **Step 4: 執行確認通過**

Run: `npx vitest run tests/format.test.js`
Expected: PASS（3 passed）。

- [ ] **Step 5: 建立 index.html**

```html
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <title>會議記錄</title>
  <link rel="manifest" href="./manifest.webmanifest" />
  <link rel="apple-touch-icon" href="./icons/icon-180.png" />
  <link rel="stylesheet" href="./css/styles.css" />
</head>
<body>
  <header class="topbar">
    <button id="backBtn" class="ghost" hidden>‹ 返回</button>
    <h1 id="title">會議記錄</h1>
    <button id="settingsBtn" class="ghost">⚙︎</button>
  </header>
  <main id="view"></main>
  <nav class="tabbar">
    <button id="homeTab">📋 清單</button>
    <button id="newTab" class="primary">＋ 新增</button>
  </nav>
  <script type="module" src="./js/app.js"></script>
</body>
</html>
```

- [ ] **Step 6: 建立 css/styles.css**

```css
:root { --bg:#f5f5f7; --card:#fff; --ink:#1c1c1e; --muted:#6b7280; --brand:#0a84ff; --danger:#ff3b30; }
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body { margin:0; font-family:-apple-system,"PingFang TC","Helvetica Neue",sans-serif;
  background:var(--bg); color:var(--ink);
  padding: env(safe-area-inset-top) env(safe-area-inset-right) 0 env(safe-area-inset-left); }
.topbar { position:sticky; top:0; display:flex; align-items:center; gap:8px;
  padding:12px 16px; background:var(--bg); }
.topbar h1 { flex:1; font-size:18px; margin:0; text-align:center; }
.ghost { background:none; border:none; font-size:16px; color:var(--brand); padding:8px; }
main { padding:8px 16px 96px; }
.card { background:var(--card); border-radius:14px; padding:16px; margin-bottom:12px;
  box-shadow:0 1px 3px rgba(0,0,0,.06); }
.card h3 { margin:0 0 4px; font-size:16px; }
.card .meta { color:var(--muted); font-size:13px; }
.card .snippet { color:var(--muted); font-size:14px; margin-top:6px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.tabbar { position:fixed; left:0; right:0; bottom:0; display:flex; gap:8px;
  padding:10px 16px calc(10px + env(safe-area-inset-bottom)); background:#fff;
  border-top:1px solid #e5e5ea; }
.tabbar button { flex:1; padding:12px; border:none; border-radius:12px; font-size:15px; background:#eee; }
.tabbar .primary { background:var(--brand); color:#fff; }
button.big { width:100%; padding:14px; font-size:16px; border:none; border-radius:12px;
  background:var(--brand); color:#fff; margin-top:8px; }
button.danger { background:var(--danger); color:#fff; }
button.copy { background:#eee; color:var(--ink); border:none; border-radius:8px; padding:6px 10px; font-size:13px; }
input[type=text], input[type=password] { width:100%; padding:12px; border:1px solid #ddd;
  border-radius:10px; font-size:16px; }
.section-title { font-weight:600; margin:16px 0 6px; display:flex; justify-content:space-between; align-items:center; }
ul.list { margin:0; padding-left:20px; } ul.list li { margin:4px 0; }
pre.transcript { white-space:pre-wrap; word-break:break-word; background:var(--card);
  padding:14px; border-radius:12px; font:14px/1.6 inherit; }
.progress { text-align:center; color:var(--muted); padding:24px; }
.empty { text-align:center; color:var(--muted); padding:48px 16px; }
```

- [ ] **Step 7: 建立 js/app.js（主程式）**

```js
import { getApiKey, setApiKey, hasApiKey } from './settings.js';
import { list, get, save, remove, exportAll } from './store.js';
import { transcribeAndSummarize } from './gemini.js';
import { formatDate, defaultTitle } from './format.js';

const view = document.getElementById('view');
const titleEl = document.getElementById('title');
const backBtn = document.getElementById('backBtn');

document.getElementById('homeTab').onclick = () => (location.hash = '#/');
document.getElementById('newTab').onclick = () => (location.hash = '#/new');
document.getElementById('settingsBtn').onclick = () => (location.hash = '#/settings');
backBtn.onclick = () => (location.hash = '#/');

function esc(s) {
  return (s || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function uid() {
  return (crypto.randomUUID ? crypto.randomUUID() : 'm' + Date.now() + Math.round(performance.now()));
}
function setHeader(text, showBack) {
  titleEl.textContent = text;
  backBtn.hidden = !showBack;
}

async function renderList() {
  setHeader('會議記錄', false);
  const meetings = await list();
  if (!meetings.length) {
    view.innerHTML = `<div class="empty">還沒有會議記錄<br>點下方「＋ 新增」上傳錄音</div>`;
    return;
  }
  view.innerHTML = meetings.map(m => `
    <div class="card" data-id="${m.id}">
      <h3>${esc(m.title)}</h3>
      <div class="meta">${formatDate(m.createdAt)}</div>
      <div class="snippet">${esc((m.summary.keyPoints || []).join('、'))}</div>
    </div>`).join('')
    + `<button class="big" id="exportBtn" style="background:#eee;color:#1c1c1e">⬇︎ 匯出備份</button>`;
  view.querySelectorAll('.card').forEach(c => {
    c.onclick = () => (location.hash = '#/m/' + c.dataset.id);
  });
  document.getElementById('exportBtn').onclick = onExport;
}

async function onExport() {
  const json = await exportAll();
  const blob = new Blob([json], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `meetings-backup-${formatDate(Date.now()).replace(/[: ]/g, '-')}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function renderNew() {
  setHeader('新增會議', true);
  if (!hasApiKey()) {
    view.innerHTML = `<div class="card">請先到 ⚙︎ 設定填入 Gemini API 金鑰。</div>`;
    return;
  }
  view.innerHTML = `
    <div class="card">
      <p>選擇會議錄音檔（mp3 / m4a / wav 等）</p>
      <input type="file" id="audio" accept="audio/*" />
      <button class="big" id="go">開始辨識</button>
      <div class="progress" id="prog" hidden></div>
    </div>`;
  const prog = document.getElementById('prog');
  document.getElementById('go').onclick = async () => {
    const f = document.getElementById('audio').files[0];
    if (!f) { alert('請先選擇音檔'); return; }
    prog.hidden = false; prog.textContent = '準備中…';
    try {
      const { transcript, summary } = await transcribeAndSummarize(f, getApiKey(), {
        onProgress: msg => (prog.textContent = msg),
      });
      const ts = Date.now();
      const meeting = { id: uid(), title: defaultTitle(f.name, ts), createdAt: ts, transcript, summary };
      await save(meeting);
      location.hash = '#/m/' + meeting.id;
    } catch (e) {
      prog.textContent = '❌ ' + e.message;
    }
  };
}

async function renderDetail(id) {
  const m = await get(id);
  if (!m) { location.hash = '#/'; return; }
  setHeader(m.title, true);
  const listHtml = (arr) => arr && arr.length
    ? `<ul class="list">${arr.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`
    : `<div class="meta">（無）</div>`;
  view.innerHTML = `
    <div class="card">
      <input type="text" id="titleInput" value="${esc(m.title)}" />
      <div class="meta" style="margin-top:6px">${formatDate(m.createdAt)}</div>
    </div>
    <div class="card">
      <div class="section-title">重點條列 <button class="copy" data-copy="kp">複製</button></div>
      ${listHtml(m.summary.keyPoints)}
      <div class="section-title">待辦事項 <button class="copy" data-copy="ai">複製</button></div>
      ${listHtml(m.summary.actionItems)}
      <div class="section-title">決議事項 <button class="copy" data-copy="dc">複製</button></div>
      ${listHtml(m.summary.decisions)}
    </div>
    <div class="section-title">逐字稿 <button class="copy" data-copy="tr">複製</button></div>
    <pre class="transcript">${esc(m.transcript)}</pre>
    <button class="big danger" id="del">刪除這場會議</button>`;

  document.getElementById('titleInput').onchange = async (e) => {
    m.title = e.target.value.trim() || m.title;
    await save(m);
  };
  const texts = {
    kp: m.summary.keyPoints.join('\n'),
    ai: m.summary.actionItems.join('\n'),
    dc: m.summary.decisions.join('\n'),
    tr: m.transcript,
  };
  view.querySelectorAll('.copy').forEach(b => {
    b.onclick = async () => {
      await navigator.clipboard.writeText(texts[b.dataset.copy] || '');
      b.textContent = '已複製'; setTimeout(() => (b.textContent = '複製'), 1200);
    };
  });
  document.getElementById('del').onclick = async () => {
    if (confirm('確定刪除這場會議記錄？')) { await remove(id); location.hash = '#/'; }
  };
}

function renderSettings() {
  setHeader('設定', true);
  view.innerHTML = `
    <div class="card">
      <p>Gemini API 金鑰（存在本機，不上傳）</p>
      <input type="password" id="key" placeholder="貼上金鑰" value="${esc(getApiKey())}" />
      <button class="big" id="saveKey">儲存</button>
      <p class="meta" style="margin-top:12px">從 <b>aistudio.google.com</b> → API Keys 複製你的免費金鑰。</p>
    </div>`;
  document.getElementById('saveKey').onclick = () => {
    setApiKey(document.getElementById('key').value);
    alert('已儲存'); location.hash = '#/';
  };
}

function router() {
  const h = location.hash || '#/';
  if (h.startsWith('#/m/')) return renderDetail(h.slice(4));
  if (h === '#/new') return renderNew();
  if (h === '#/settings') return renderSettings();
  return renderList();
}
window.addEventListener('hashchange', router);
router();
```

- [ ] **Step 8: 全套測試回歸**

Run: `npm test`
Expected: 全部 PASS（含 format）。

- [ ] **Step 9: 手動端到端（桌機瀏覽器先驗證）**

Run: `npx vitest --version >/dev/null; python -m http.server 8000`（或 `npx serve .`）
在瀏覽器開 `http://localhost:8000`：
1. ⚙︎ 設定 → 貼入真實 Gemini 金鑰 → 儲存。
2. ＋ 新增 → 選一段短會議音檔 → 開始辨識 → 應顯示進度並在完成後跳到詳情頁。
3. 詳情頁應有繁中逐字稿、重點／待辦／決議三區、複製鈕、改標題、刪除。
4. 返回清單應看到該場記錄；匯出備份應下載 JSON。
Expected: 上述皆正常；若 Gemini 呼叫失敗，記錄實際錯誤訊息以便除錯（不可跳過）。

- [ ] **Step 10: Commit**

```bash
git add index.html css/styles.css js/app.js js/format.js tests/format.test.js
git commit -m "feat: UI 與頁面（清單/新增/詳情/設定/匯出）"
```

---

### Task 6: PWA（加到主畫面＋離線殼）

**Files:**
- Create: `manifest.webmanifest`
- Create: `sw.js`
- Create: `icons/icon-180.png`
- Create: `icons/icon-512.png`
- Modify: `js/app.js`（結尾註冊 service worker）

**Interfaces:**
- Consumes: 既有靜態檔
- Produces: 可「加到主畫面」、離線可開啟並瀏覽已存記錄

- [ ] **Step 1: 建立 manifest.webmanifest**

```json
{
  "name": "會議記錄",
  "short_name": "會議記錄",
  "start_url": "./",
  "scope": "./",
  "display": "standalone",
  "background_color": "#f5f5f7",
  "theme_color": "#0a84ff",
  "icons": [
    { "src": "./icons/icon-180.png", "sizes": "180x180", "type": "image/png" },
    { "src": "./icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
  ]
}
```

- [ ] **Step 2: 產生圖示**

以 Node（已安裝）產生純色圓角 PNG 圖示，無需外部套件——建立臨時腳本 `make-icons.mjs`：
```js
import { writeFileSync } from 'node:fs';
import zlib from 'node:zlib';
function png(size, [r, g, b]) {
  const bpp = 3, raw = Buffer.alloc((size * bpp + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * bpp + 1)] = 0;
    for (let x = 0; x < size; x++) {
      const o = y * (size * bpp + 1) + 1 + x * bpp;
      raw[o] = r; raw[o + 1] = g; raw[o + 2] = b;
    }
  }
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td) >>> 0);
    return Buffer.concat([len, td, crc]);
  };
  function crc32(buf) {
    let c = ~0;
    for (let i = 0; i < buf.length; i++) {
      c ^= buf[i];
      for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xEDB88320 & -(c & 1));
    }
    return ~c;
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; ihdr[9] = 2;
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  return Buffer.concat([sig, chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
writeFileSync('icons/icon-180.png', png(180, [10, 132, 255]));
writeFileSync('icons/icon-512.png', png(512, [10, 132, 255]));
console.log('icons written');
```
Run: `mkdir -p icons && node make-icons.mjs && rm make-icons.mjs`
Expected: 產生 `icons/icon-180.png`、`icons/icon-512.png`（純藍底，iOS 可接受）。

- [ ] **Step 3: 建立 sw.js**

```js
const CACHE = 'meeting-app-v1';
const ASSETS = [
  './', './index.html', './css/styles.css',
  './js/app.js', './js/settings.js', './js/store.js', './js/gemini.js', './js/format.js',
  './manifest.webmanifest', './icons/icon-180.png', './icons/icon-512.png',
];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // 只快取自家靜態資源；Gemini API 一律走網路
  if (url.origin !== location.origin) return;
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
```

- [ ] **Step 4: 在 js/app.js 結尾註冊 SW**

在 `router();` 之後新增：
```js
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));
}
```

- [ ] **Step 5: 手動驗證（桌機）**

Run: `python -m http.server 8000`
開 `http://localhost:8000`，DevTools → Application：
- Manifest 無錯、Service Worker 已啟用。
- 離線（DevTools Offline 勾選）重整仍可開清單頁。
Expected: 皆正常。

- [ ] **Step 6: Commit**

```bash
git add manifest.webmanifest sw.js icons/ js/app.js
git commit -m "feat: PWA（manifest + service worker + 圖示）"
```

---

### Task 7: 部署到 GitHub Pages 並於 iPhone 驗證

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: 全部靜態檔
- Produces: 公開網址；iPhone 可加到主畫面使用

- [ ] **Step 1: 建立 README.md**

```markdown
# 會議記錄 App

掛在 GitHub Pages 的手機網頁 App（PWA）。上傳會議錄音，透過你自己的 Gemini API 金鑰產生繁體中文逐字稿與摘要，記錄存在手機本機。

## 使用
1. 打開網址，⚙︎ 設定貼入 Gemini 金鑰（aistudio.google.com 免費申請）。
2. ＋ 新增，選錄音檔，等辨識完成。
3. iPhone Safari「加到主畫面」即可像 App 使用。

## 開發
- `npm install && npm test` 執行測試。
- 純靜態，無建置；本機預覽 `python -m http.server 8000`。
```

- [ ] **Step 2: 提交 README**

```bash
git add README.md
git commit -m "docs: README"
```

- [ ] **Step 3: 建立 GitHub 儲存庫並推送**

優先用 gh CLI：
```bash
gh repo create meeting-transcriber --public --source=. --push
```
若 gh 未安裝／未登入，改手動：於 github.com 建立空 repo，然後：
```bash
git branch -M main
git remote add origin https://github.com/<你的帳號>/meeting-transcriber.git
git push -u origin main
```

- [ ] **Step 4: 啟用 GitHub Pages**

用 gh：
```bash
gh api -X POST repos/<你的帳號>/meeting-transcriber/pages -f source[branch]=main -f source[path]=/
```
或手動：GitHub repo → Settings → Pages → Source 選 `main` 分支、`/ (root)` → Save。
記下網址：`https://<你的帳號>.github.io/meeting-transcriber/`。

- [ ] **Step 5: iPhone 端到端驗證**

用 iPhone Safari 開該網址：
1. ⚙︎ 設定貼入你的 Gemini 金鑰。
2. ＋ 新增，從「檔案 / 語音備忘錄」選一段真實會議錄音，開始辨識。
3. 確認得到繁中逐字稿＋重點/待辦/決議，可複製、改標題、刪除。
4. 分享鈕 →「加入主畫面」，從主畫面開啟確認像 App、離線可看清單。
Expected: 全流程可用。記錄任何失敗的實際訊息以便修正（不可宣稱成功而未實測）。

- [ ] **Step 6: 最終確認**

Run: `npm test`
Expected: 全部 PASS。並確認部署網址於 iPhone 實際可用。

---

## Self-Review

**1. Spec coverage：**
- 靜態網站/GitHub Pages → Task 7 ✅
- Gemini 免費金鑰、瀏覽器端呼叫、Files API → Task 4 ✅
- 繁中逐字稿＋摘要(重點/待辦/決議) → Task 4 SCHEMA/PROMPT、Task 5 詳情頁 ✅
- IndexedDB 記憶、不存原音檔 → Task 3、Task 5 meeting 物件（無 audio 欄位）✅
- 匯出備份 → Task 3 exportAll、Task 5 onExport ✅
- PWA 加到主畫面/離線 → Task 6 ✅
- 錯誤處理（無金鑰、API 錯、格式）→ Task 4 丟錯、Task 5 renderNew 檢查與 catch 顯示 ✅
- 手機優先版面/safe-area → Task 5 CSS ✅
- 範圍外（即時、語者辨識、雲端同步、存音檔）→ 未實作 ✅

**2. Placeholder scan：** 無 TBD/TODO；`gemini-2.5-flash` 為具體值並附實作前驗證步驟。✅

**3. Type consistency：** `transcribeAndSummarize` 回傳 `{transcript, summary:{keyPoints,actionItems,decisions}}` 與 store 的 Meeting 結構、Task 5 詳情頁渲染欄位一致；`formatDate`/`defaultTitle` 簽章跨 Task 5 一致。✅
