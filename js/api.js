// 與 NAS 伺服器溝通的唯一通道（同網域 /api/*）。
// 所有函式失敗時丟出 Error，訊息就是伺服器回的 detail 文字；連不上時 err.offline = true。

export const config = {
  backoffMs: 1000, // 上傳單塊失敗後的等待（每次重試加倍）
  pollMs: 2000, // 等 AI 工作時的輪詢間隔
  maxPollFailMs: 5 * 60 * 1000, // 輪詢時連續連不上／伺服器暫時錯誤最多忍這麼久才放棄
};
const RETRIES = 3;
const enc = encodeURIComponent;

function detailText(d) {
  if (d == null) return '';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => (x && (x.msg || x.message)) || JSON.stringify(x)).join('；');
  if (typeof d === 'object') return d.msg || d.message || JSON.stringify(d);
  return String(d);
}

async function errorFrom(res) {
  let msg = '';
  try {
    const body = await res.json();
    msg = detailText(body && (body.detail != null ? body.detail : body.error));
  } catch (_) {}
  const e = new Error(msg || `伺服器錯誤（HTTP ${res.status}）`);
  e.status = res.status;
  return e;
}

async function rawFetch(path, opts = {}) {
  try {
    return await fetch(path, opts);
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    const e = new Error('連不上 NAS（' + ((err && err.message) || '網路錯誤') + '）');
    e.offline = true;
    throw e;
  }
}

async function req(method, path, body, opts = {}) {
  const init = { method, signal: opts.signal };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const res = await rawFetch(path, init);
  if (!res.ok) throw await errorFrom(res);
  if (res.status === 204) return null;
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (_) {
    return text;
  }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal && signal.aborted) return reject(abortError());
    const t = setTimeout(resolve, ms);
    if (signal)
      signal.addEventListener(
        'abort',
        () => {
          clearTimeout(t);
          reject(abortError());
        },
        { once: true }
      );
  });
}
function abortError() {
  const e = new Error('已取消');
  e.name = 'AbortError';
  return e;
}

// ---- 會議 ----
export async function listMeetings() {
  const r = await req('GET', '/api/meetings');
  return Array.isArray(r) ? r : (r && r.meetings) || [];
}
export const getMeeting = (id) => req('GET', `/api/meetings/${enc(id)}`);
export async function saveMeeting(m) {
  const r = await req('PUT', `/api/meetings/${enc(m.id)}`, m);
  return r && typeof r === 'object' && r.id ? r : m;
}
export const deleteMeeting = (id) => req('DELETE', `/api/meetings/${enc(id)}`);
export const retranscribe = (id) => req('POST', `/api/meetings/${enc(id)}/retranscribe`, {});

// ---- 分類 ----
export async function getGroups() {
  const r = await req('GET', '/api/groups');
  return Array.isArray(r) ? r : (r && r.groups) || [];
}
export const saveGroups = (g) => req('PUT', '/api/groups', g);

// ---- 狀態與設定 ----
export const getStatus = () => req('GET', '/api/status');
export const getSettings = () => req('GET', '/api/settings');
export const saveSettings = (s) => req('PUT', '/api/settings', s);

// ---- 分塊上傳（可續傳）----
// 同一個檔案（名稱＋大小＋修改時間）記住它的 uploadId：中斷後重選同一個檔案，就從伺服器已收到的塊接著傳。
const uploadKey = (file) => `upload:${file.name}:${file.size}:${file.lastModified || 0}`;
function lsGet(k) {
  try {
    return localStorage.getItem(k);
  } catch (_) {
    return null;
  }
}
function lsSet(k, v) {
  try {
    localStorage.setItem(k, v);
  } catch (_) {}
}
function lsDel(k) {
  try {
    localStorage.removeItem(k);
  } catch (_) {}
}

function normUpload(r, file) {
  const chunkSize = r.chunkSize || 8 * 1024 * 1024;
  const chunks = r.chunks != null ? r.chunks : Math.ceil(file.size / chunkSize);
  return { uploadId: r.uploadId, chunkSize, chunks, received: Array.isArray(r.received) ? r.received : [] };
}

export async function startUpload(file, signal) {
  const key = uploadKey(file);
  const known = lsGet(key);
  if (known) {
    try {
      const r = await req('GET', `/api/uploads/${enc(known)}`, undefined, { signal });
      if (r && r.uploadId) return normUpload(r, file);
    } catch (e) {
      if (e.status !== 404) throw e; // 連不上等暫時錯誤：別丟掉續傳點
    }
    lsDel(key);
  }
  const r = await req('POST', '/api/uploads', { name: file.name, size: file.size, mime: file.type || '' }, { signal });
  lsSet(key, r.uploadId);
  return normUpload(r, file);
}

async function putChunk(uploadId, index, blob, signal) {
  let lastErr = null;
  for (let attempt = 0; attempt <= RETRIES; attempt++) {
    if (attempt) await sleep(config.backoffMs * 2 ** (attempt - 1), signal);
    try {
      const res = await rawFetch(`/api/uploads/${enc(uploadId)}/${index}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: blob,
        signal,
      });
      if (res.ok) return;
      lastErr = await errorFrom(res);
      if (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429) throw lastErr;
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      if (e && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) throw e;
      lastErr = e;
    }
  }
  throw lastErr || new Error('上傳失敗');
}

// 傳完缺少的塊，回傳 uploadId。onProgress({sent, total, pct})（位元組）
export async function uploadFile(file, onProgress, signal) {
  const up = await startUpload(file, signal);
  const total = file.size;
  const have = new Set(up.received);
  const sizeOf = (i) => Math.max(0, Math.min(up.chunkSize, total - i * up.chunkSize));
  let sent = 0;
  for (const i of have) sent += sizeOf(i);
  const report = () => onProgress && onProgress({ sent, total, pct: total ? Math.round((sent / total) * 100) : 100 });
  report();
  for (let i = 0; i < up.chunks; i++) {
    if (have.has(i)) continue;
    if (signal && signal.aborted) throw abortError();
    const blob = file.slice(i * up.chunkSize, Math.min(total, (i + 1) * up.chunkSize));
    await putChunk(up.uploadId, i, blob, signal);
    sent += sizeOf(i);
    report();
  }
  return up.uploadId;
}

export async function createMeeting({ title, group, uploads }) {
  const m = await req('POST', '/api/meetings', { title, group: group || null, uploads });
  // 這些上傳已經變成會議的錄音 → 清掉續傳記錄
  try {
    const used = new Set(uploads || []);
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && k.startsWith('upload:') && used.has(localStorage.getItem(k))) localStorage.removeItem(k);
    }
  } catch (_) {}
  return m;
}

// ---- AI 工作 ----
export async function runAI(id, action, params = {}) {
  const r = await req('POST', `/api/meetings/${enc(id)}/ai`, { action, ...params });
  return r && r.jobId;
}
// 回應裡的工作編號（{jobId} 或含 aiJobId 的會議）；沒有就回 null
export const jobIdOf = (r) => (r && typeof r === 'object' && (r.jobId || r.aiJobId)) || null;

// ---- 待確認清單／講座會議模式／還原原始逐字稿 ----
// action: 'confirm' | 'edit' | 'delete'；edit 帶 text。回傳更新後的會議。
export const reviewAction = (id, rid, action, text) =>
  req('POST', `/api/meetings/${enc(id)}/review/${enc(rid)}`, text != null ? { action, text } : { action });
// 改模式後伺服器會排入重新整理，回傳 {jobId}（或含 aiJobId 的會議）
export const setMeetingMode = (id, mode) => req('POST', `/api/meetings/${enc(id)}/mode`, { mode });
export const restoreRaw = (id) => req('POST', `/api/meetings/${enc(id)}/restore-raw`, {});

export const getJob = (jobId) => req('GET', `/api/jobs/${enc(jobId)}`);
export const cancelJob = (jobId) => req('POST', `/api/jobs/${enc(jobId)}/cancel`, {});

// 輪詢到工作結束：done → resolve(job)；failed → reject(伺服器錯誤文字)；cancelled → reject('已取消')
// 暫時性錯誤（連不上、5xx、429）：手機切網路、NAS 重啟時常見，不該讓整個工作被當成失敗
const isTransient = (e) => !!e && (e.offline || e.status >= 500 || e.status === 429 || e.status === 408);

export async function waitJob(jobId, onProgress, signal) {
  let failSince = null;
  for (;;) {
    let j;
    try {
      j = await getJob(jobId);
      failSince = null;
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      if (!isTransient(e)) throw e;
      if (failSince == null) failSince = Date.now();
      if (Date.now() - failSince >= config.maxPollFailMs) throw e;
      await sleep(config.pollMs, signal);
      continue;
    }
    if (onProgress) onProgress((j && j.progress) || null, j);
    const st = j && j.state;
    if (st === 'done') return j;
    if (st === 'failed') throw new Error((j && j.error) || 'AI 工作失敗');
    if (st === 'cancelled') throw new Error('已取消');
    await sleep(config.pollMs, signal);
  }
}

// ---- 錄音與備份 ----
export const audioUrl = (id, n) => `/api/meetings/${enc(id)}/audio/${n}`;
export const importBackup = (obj) => req('POST', '/api/import', obj);
export const exportAllUrl = () => '/api/export';
export const docxUrl = (id) => `/api/meetings/${enc(id)}/export.docx`;
