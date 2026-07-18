// Gemini 客戶端：上傳長音檔（Files API）→ 等待處理 → 產生「語者分段逐字稿 + 摘要」
//
// 設計重點（對應需求：長錄音、多語者、手機穩定）：
// - 用 Files API 串流上傳，1 小時以上的大檔不佔滿手機記憶體。
// - thinkingBudget:0：關掉 2.5-flash 預設思考，避免思考 token 吃掉輸出額度導致長逐字稿被截斷。
// - maxOutputTokens 開到上限 65535，容納長逐字稿。
// - responseSchema 強制結構化輸出，segments 陣列做語者辨識。

const BASE = 'https://generativelanguage.googleapis.com';

// 動態挑選型號：向 API 詢問目前可用的模型，挑最適合做「長音檔 + 語者辨識」的 flash 型號。
// 這樣 Google 汰換型號名稱（如 2.5-flash → 3.5-flash）時 App 不會壞。
export function pickModel(models) {
  const bad = /embedding|aqa|imagen|image|veo|tts|audio-native|gemma|learnlm|robotics|computer-use|live/i;
  const scored = (models || [])
    .map((m) => {
      const name = String(m.name || '').replace(/^models\//, '');
      const methods = m.supportedGenerationMethods || m.supported_generation_methods || [];
      if (!methods.includes('generateContent')) return null;
      if (bad.test(name)) return null;
      const ver = (name.match(/gemini-(\d+(?:\.\d+)?)/) || [])[1];
      let score = (ver ? parseFloat(ver) : 0) * 100;
      if (/flash/.test(name) && !/flash-lite/.test(name)) score += 40; // flash：快、免費額度高
      else if (/pro/.test(name)) score += 25;
      else if (/flash-lite/.test(name)) score += 15;
      if (/preview|exp|thinking|latest/.test(name)) score -= 12; // 偏好穩定版
      return { name, score };
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score);
  return scored.length ? scored[0].name : null;
}

async function resolveModel(apiKey) {
  const res = await fetch(`${BASE}/v1beta/models?key=${apiKey}`);
  if (!res.ok) throw new Error(`取得可用型號失敗 (${res.status})：${(await res.text()).slice(0, 200)}`);
  const data = await res.json();
  const name = pickModel(data.models || []);
  if (!name) throw new Error('這組金鑰找不到可用的辨識型號，請確認金鑰是否正確、或是否已啟用 Gemini API。');
  return name;
}


// 進度回報統一格式：{ phase, pct, message }。pct 為 null 代表該階段無精確百分比。
function report(onProgress, phase, pct, message) {
  if (onProgress) onProgress({ phase, pct, message });
}

async function uploadFile(file, apiKey, onProgress) {
  report(onProgress, 'upload', 5, '準備上傳…');
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
  if (!start.ok) throw new Error(`上傳啟動失敗 (${start.status})：${(await start.text()).slice(0, 200)}`);
  const uploadUrl = start.headers.get('X-Goog-Upload-URL');
  if (!uploadUrl) throw new Error('未取得上傳網址');

  // 用 XHR 上傳位元組，才能取得真實上傳進度
  const info = await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', uploadUrl);
    xhr.setRequestHeader('X-Goog-Upload-Command', 'upload, finalize');
    xhr.setRequestHeader('X-Goog-Upload-Offset', '0');
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const frac = e.loaded / e.total;
        report(onProgress, 'upload', 5 + frac * 30, `上傳音檔中… ${Math.round(frac * 100)}%`);
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText).file);
        } catch (err) {
          reject(new Error('上傳回應解析失敗'));
        }
      } else {
        reject(new Error(`上傳失敗 (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error('上傳失敗（網路中斷）'));
    xhr.send(file);
  });
  return info; // { uri, name, state, mimeType }
}

async function waitActive(fileInfo, apiKey, onProgress) {
  let state = fileInfo.state;
  let uri = fileInfo.uri;
  let name = fileInfo.name;
  let mimeType = fileInfo.mimeType;
  while (state === 'PROCESSING') {
    report(onProgress, 'processing', 40, '雲端處理音檔中…');
    await new Promise((r) => setTimeout(r, 2500));
    const res = await fetch(`${BASE}/v1beta/${name}?key=${apiKey}`);
    if (!res.ok) throw new Error(`檔案狀態查詢失敗 (${res.status})`);
    const f = await res.json();
    state = f.state;
    uri = f.uri;
    name = f.name;
    mimeType = f.mimeType || mimeType;
  }
  if (state !== 'ACTIVE') throw new Error(`音檔處理失敗 (${state})`);
  return { uri, mimeType };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export function isTransientStatus(status) {
  return status === 429 || status === 500 || status === 502 || status === 503 || status === 504;
}

// 帶自動重試的 POST（處理暫時性錯誤：網路中斷、5xx、429）
async function postJsonWithRetry(url, body, onProgress, label) {
  for (let attempt = 0; ; attempt++) {
    report(onProgress, 'transcribe', null, attempt ? `連線不穩，重試中…（第 ${attempt} 次）` : label);
    let res;
    try {
      res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
    } catch (e) {
      if (attempt < 2) {
        await sleep(1500 * (attempt + 1));
        continue;
      }
      throw new Error('網路連線中斷，請確認網路後再試一次。');
    }
    if (res.ok) return res;
    if (isTransientStatus(res.status) && attempt < 2) {
      await sleep(1500 * (attempt + 1));
      continue;
    }
    const t = await res.text();
    throw new Error(`辨識失敗 (${res.status})：${t.slice(0, 300)}`);
  }
}

// ---- 逐字稿（可依時間分段，長錄音自動切割）----
const WINDOW_SEC = 20 * 60; // 每段最長 20 分鐘，避免單次輸出超過上限
const SEG_SCHEMA = {
  type: 'object',
  properties: {
    segments: {
      type: 'array',
      items: {
        type: 'object',
        properties: { speaker: { type: 'string' }, text: { type: 'string' } },
        required: ['speaker', 'text'],
      },
    },
  },
  required: ['segments'],
};
const SEG_PROMPT =
  `你是專業會議記錄助理。請把這段會議錄音整理成「語者分段逐字稿」：\n` +
  `- 辨識不同說話者，標記「說話者1」「說話者2」…同一個人自始至終用同一標籤。\n` +
  `- 中文一律使用繁體中文（台灣用語），英文保留原文。\n` +
  `- 每個 segment 格式 {"speaker":"說話者1","text":"…"}，適度斷句。`;

function mmss(sec) {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// 辨識單一時間窗；若輸出被截斷（內容太密）則自動對半再切，直到塞得下
async function transcribeWindow(fileUri, mime, apiKey, model, start, end, whole, onProgress, label, depth) {
  const range = whole ? '' : `\n\n【只處理 ${mmss(start)} 到 ${mmss(end)} 這段時間範圍】的內容，此範圍以外請完全略過。說話者請從「說話者1」開始標記。`;
  const res = await postJsonWithRetry(
    `${BASE}/v1beta/models/${model}:generateContent?key=${apiKey}`,
    JSON.stringify({
      contents: [{ parts: [{ file_data: { mime_type: mime, file_uri: fileUri } }, { text: SEG_PROMPT + range }] }],
      generationConfig: {
        responseMimeType: 'application/json',
        responseSchema: SEG_SCHEMA,
        maxOutputTokens: 65535,
        thinkingConfig: { thinkingBudget: 0 },
      },
    }),
    onProgress,
    label
  );
  const data = await res.json();
  const cand = data && data.candidates && data.candidates[0];
  const text = cand && cand.content && cand.content.parts && cand.content.parts[0] && cand.content.parts[0].text;
  const truncated = cand && cand.finishReason === 'MAX_TOKENS';
  let segments = null;
  if (text) {
    try {
      segments = (JSON.parse(text).segments) || [];
    } catch (_) {
      segments = null;
    }
  }
  // 內容太密被截斷 → 對半再切（有時間範圍時才能切）
  if ((truncated || segments === null) && !whole && depth < 4 && end - start > 120) {
    const mid = Math.floor((start + end) / 2);
    const a = await transcribeWindow(fileUri, mime, apiKey, model, start, mid, false, onProgress, label, depth + 1);
    const b = await transcribeWindow(fileUri, mime, apiKey, model, mid, end, false, onProgress, label, depth + 1);
    return a.concat(b);
  }
  if (segments === null) {
    if (truncated) throw new Error('這段錄音內容太密集，無法完整辨識，請重試一次。');
    throw new Error('辨識結果解析失敗，請重試一次。');
  }
  return segments;
}

async function transcribeAudio(fileUri, mime, apiKey, model, durationSec, onProgress) {
  if (!durationSec) {
    // 讀不到長度：以整檔一次辨識（多數情況用不到）
    return transcribeWindow(fileUri, mime, apiKey, model, 0, 0, true, onProgress, '辨識語者與逐字稿中…', 0);
  }
  const n = Math.max(1, Math.ceil(durationSec / WINDOW_SEC));
  const all = [];
  for (let i = 0; i < n; i++) {
    const start = i * WINDOW_SEC;
    const end = Math.min(durationSec, (i + 1) * WINDOW_SEC);
    const label = n > 1 ? `辨識第 ${i + 1}/${n} 段（${mmss(start)}–${mmss(end)}）…` : '辨識語者與逐字稿中…';
    const segs = await transcribeWindow(fileUri, mime, apiKey, model, start, end, false, onProgress, label, 0);
    all.push(...segs);
  }
  return all;
}

async function summarizeSegments(segments, apiKey, model, onProgress) {
  const text = (segments || []).map((s) => `${s.speaker}：${s.text}`).join('\n');
  const res = await postJsonWithRetry(
    `${BASE}/v1beta/models/${model}:generateContent?key=${apiKey}`,
    JSON.stringify({
      contents: [{ parts: [{ text: SUMMARY_PROMPT + text }] }],
      generationConfig: {
        responseMimeType: 'application/json',
        responseSchema: SUMMARY_SCHEMA,
        maxOutputTokens: 65535,
        thinkingConfig: { thinkingBudget: 0 },
      },
    }),
    onProgress,
    '整理摘要中…'
  );
  const data = await res.json();
  const out =
    data && data.candidates && data.candidates[0] && data.candidates[0].content &&
    data.candidates[0].content.parts && data.candidates[0].content.parts[0] && data.candidates[0].content.parts[0].text;
  if (!out) throw new Error('未取得摘要結果，請重試。');
  const r = JSON.parse(out);
  return { actionItems: r.actionItems || [], mainPoints: r.mainPoints || [], qa: r.qa || [] };
}

export async function transcribeAndSummarize(file, apiKey, opts = {}) {
  const onProgress = opts.onProgress;
  const durationSec = opts.durationSec || 0;
  if (!apiKey) throw new Error('尚未設定 API 金鑰，請先到設定填入。');
  report(onProgress, 'model', 3, '選擇辨識型號中…');
  const model = await resolveModel(apiKey);
  const fileInfo = await uploadFile(file, apiKey, onProgress);
  const active = await waitActive(fileInfo, apiKey, onProgress);
  const mime = active.mimeType || file.type || 'audio/mpeg';
  const segments = await transcribeAudio(active.uri, mime, apiKey, model, durationSec, onProgress);
  report(onProgress, 'summary', null, '整理摘要中…');
  const summary = await summarizeSegments(segments, apiKey, model, onProgress);
  return { transcript: segments, summary };
}

// 只根據既有逐字稿重新整理摘要（不需重傳音檔，快又省額度）
const SUMMARY_SCHEMA = {
  type: 'object',
  properties: {
    actionItems: { type: 'array', items: { type: 'string' } },
    mainPoints: { type: 'array', items: { type: 'string' } },
    qa: { type: 'array', items: { type: 'string' } },
  },
  required: ['actionItems', 'mainPoints', 'qa'],
};
const SUMMARY_PROMPT =
  `以下是一段會議逐字稿。請依內容整理成三類（全部使用繁體中文）：\n` +
  `- actionItems（待辦事項）：逐條列出，每項結尾標註「[DRI: 負責人]」，判斷不出負責人就寫「[DRI: 待指派]」。\n` +
  `- mainPoints（會議重點）：逐條列出。\n` +
  `- qa（提問／Q&A）：格式「問：… 答：…」，若沒有問答就回傳空陣列。\n\n逐字稿：\n`;

export async function regenerateSummary(segments, apiKey, opts = {}) {
  const onProgress = opts.onProgress;
  if (!apiKey) throw new Error('尚未設定 API 金鑰');
  report(onProgress, 'model', 3, '選擇型號中…');
  const model = await resolveModel(apiKey);
  return summarizeSegments(segments, apiKey, model, onProgress);
}
