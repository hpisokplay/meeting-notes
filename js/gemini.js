// Gemini 客戶端：上傳長音檔（Files API）→ 等待處理 → 產生「語者分段逐字稿 + 摘要」
//
// 設計重點（對應需求：長錄音、多語者、手機穩定）：
// - 用 Files API 串流上傳，1 小時以上的大檔不佔滿手機記憶體。
// - thinkingBudget:0：關掉 2.5-flash 預設思考，避免思考 token 吃掉輸出額度導致長逐字稿被截斷。
// - maxOutputTokens 開到上限 65535，容納長逐字稿。
// - responseSchema 強制結構化輸出，segments 陣列做語者辨識。

const BASE = 'https://generativelanguage.googleapis.com';
const MODEL = 'gemini-2.5-flash'; // 音訊能力 + 免費層可用；若官方更名於此處調整

const PROMPT = `你是專業的會議記錄助理。輸入是一段會議錄音，可能長達數小時，內容以台灣中文為主，偶爾夾雜英文。請完成兩件事：

一、逐字稿（segments，依時間順序）：
- 辨識不同的說話者，標記為「說話者1」「說話者2」「說話者3」以此類推；同一個人自始至終使用同一個標籤。
- 若整段幾乎只有一個人在說，就都標「說話者1」；一旦出現對話，務必區分不同說話者。
- 中文一律使用繁體中文（台灣用語）；英文保留原文。
- 每個 segment 格式為 {"speaker":"說話者1","text":"這句話的內容"}；請適度斷句，不要一個 segment 塞入過長內容。

二、摘要（依整場內容整理）：
- keyPoints：重點條列。
- actionItems：待辦事項（盡量標明誰負責做什麼）。
- decisions：決議事項。
某個類別若沒有內容，回傳空陣列。`;

const SCHEMA = {
  type: 'object',
  properties: {
    segments: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          speaker: { type: 'string' },
          text: { type: 'string' },
        },
        required: ['speaker', 'text'],
      },
    },
    keyPoints: { type: 'array', items: { type: 'string' } },
    actionItems: { type: 'array', items: { type: 'string' } },
    decisions: { type: 'array', items: { type: 'string' } },
  },
  required: ['segments', 'keyPoints', 'actionItems', 'decisions'],
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
  if (!start.ok) throw new Error(`上傳啟動失敗 (${start.status})：${(await start.text()).slice(0, 200)}`);
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
  let state = fileInfo.state;
  let uri = fileInfo.uri;
  let name = fileInfo.name;
  let mimeType = fileInfo.mimeType;
  while (state === 'PROCESSING') {
    onProgress && onProgress('雲端處理音檔中…');
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

async function generate(fileUri, mimeType, apiKey, onProgress) {
  onProgress && onProgress('辨識語者與摘要中…（長會議可能需數分鐘）');
  const res = await fetch(`${BASE}/v1beta/models/${MODEL}:generateContent?key=${apiKey}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      contents: [
        {
          parts: [
            { file_data: { mime_type: mimeType, file_uri: fileUri } },
            { text: PROMPT },
          ],
        },
      ],
      generationConfig: {
        responseMimeType: 'application/json',
        responseSchema: SCHEMA,
        maxOutputTokens: 65535,
        thinkingConfig: { thinkingBudget: 0 },
      },
    }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`辨識失敗 (${res.status})：${t.slice(0, 300)}`);
  }
  const data = await res.json();
  const cand = data && data.candidates && data.candidates[0];
  const text =
    cand && cand.content && cand.content.parts && cand.content.parts[0] && cand.content.parts[0].text;
  if (!text) {
    if (cand && cand.finishReason === 'MAX_TOKENS') {
      throw new Error('這段錄音內容太長，超過單次輸出上限。建議把錄音切成較短的檔案（例如每段 30–60 分鐘）再分次上傳。');
    }
    throw new Error('未取得辨識結果，請稍後再試。');
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    if (cand && cand.finishReason === 'MAX_TOKENS') {
      throw new Error('這段錄音內容太長，逐字稿被截斷。建議把錄音切成較短的檔案再分次上傳。');
    }
    throw new Error('辨識結果解析失敗，請重試一次。');
  }
}

export async function transcribeAndSummarize(file, apiKey, opts = {}) {
  const onProgress = opts.onProgress;
  if (!apiKey) throw new Error('尚未設定 API 金鑰，請先到設定填入。');
  const fileInfo = await uploadFile(file, apiKey, onProgress);
  const active = await waitActive(fileInfo, apiKey, onProgress);
  const mime = active.mimeType || file.type || 'audio/mpeg';
  const result = await generate(active.uri, mime, apiKey, onProgress);
  return {
    transcript: result.segments || [],
    summary: {
      keyPoints: result.keyPoints || [],
      actionItems: result.actionItems || [],
      decisions: result.decisions || [],
    },
  };
}
