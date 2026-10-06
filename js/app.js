import * as api from './api.js';
import { cacheList, cachedList, cacheMeeting, cachedMeeting, uncacheMeeting } from './store.js';
import * as wakeLock from './wakelock.js';
import { getGroups, addGroup, renameGroup, removeGroup, groupName, groupColor, loadGroups, pushGroups } from './groups.js';
import { formatDate, defaultTitle, transcriptToText } from './format.js';
import { matchMeeting } from './search.js';
import { exportPdf, exportWord, splitQA } from './export.js';
import {
  itemText,
  itemSrc,
  fmtSec,
  meetingMode,
  summarySections,
  ALL_SUMMARY_KEYS,
  groupItems,
  pendingReview,
  reviewItems,
  exportLockText,
  REVIEW_KIND,
  srcSegIndex,
  listItemIds,
  confirmOnly,
  isWordReview,
  reviewRows,
  legacyPending,
  reviewScope,
  scopeText,
  splitTimes,
  splitContext,
  gapMinutes,
} from './items.js';

const APP_VERSION = 'v112';
export const DEEP_CORRECT_CONFIRM = '會逐段重新掃描整份逐字稿的錯字（約 3–5 分鐘），只新增待確認項目，不會改動已確認的內容。確定？';

const view = document.getElementById('view');
const titleEl = document.getElementById('title');
const backBtn = document.getElementById('backBtn');

document.getElementById('homeTab').onclick = () => (location.hash = '#/');
const groupsTabEl = document.getElementById('groupsTab');
if (groupsTabEl) groupsTabEl.onclick = () => (location.hash = '#/groups');
document.getElementById('newTab').onclick = () => (location.hash = '#/new');
document.getElementById('settingsBtn').onclick = () => (location.hash = '#/settings');
backBtn.onclick = () => (location.hash = '#/');

const SPEAKER_PALETTE = ['#0a84ff', '#34c759', '#ff9500', '#af52de', '#ff2d55', '#5ac8fa', '#ffcc00'];

function esc(s) {
  // 含引號跳脫：避免把使用者/AI/伺服器文字插進 HTML 屬性時被注入（attribute injection XSS）
  return String(s == null ? '' : s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );
}
function setHeader(text, showBack) {
  titleEl.textContent = text;
  backBtn.hidden = !showBack;
  backBtn.onclick = () => (location.hash = '#/'); // 預設返回清單，個別頁面可覆寫
}
const errText = (e) => (e && e.message ? e.message : String(e));

// 繪製世代：每次 router() 換頁就 +1。各頁的輪詢（清單刷新、進度頁、計時）只在自己那一代還是最新時才繼續，
// 否則快速換頁或啟動時的兩次繪製會讓輪詢越疊越多。
let renderGen = 0;

// ===== 處理狀態（伺服器的 meeting.status / meeting.progress）=====
const STAGE_TEXT = {
  uploading: '上傳中',
  queued: '排隊中（等電腦轉錄器）',
  transcribing: '轉錄中',
  summarizing: '摘要中',
  done: '完成',
  failed: '失敗',
};
export const NAS_CPU_TEXT = 'NAS 備援轉錄（較慢、不分說話者）';
// 狀態 → 給人看的一行字（失敗時附上伺服器錯誤原因）
export function statusText(m) {
  const st = (m && m.status) || 'done';
  const base = STAGE_TEXT[st] || st;
  if (st === 'failed') return m.error ? `${base}：${m.error}` : base;
  return base;
}
// 進度頁：伺服器回報的細階段（progress.stage）→ 中文。每個階段進度條會從 0 重新開始，
// 所以要把「現在在第幾步、做什麼」寫出來，免得看起來像倒退。
const PROGRESS_STAGE_TEXT = {
  download: '下載錄音',
  asr: '語音轉文字',
  diar: '分辨說話者',
  upload: '上傳結果',
  transcribe: '語音轉文字',
  claude: '摘要中',
  summarizing: '摘要中',
  queued: '排隊中（等電腦轉錄器）',
  transcribing: '轉錄中',
};
const GPU_STEPS = ['download', 'asr', 'diar', 'upload'];
// 重新整理（不重新轉錄）的各步驟：只在確定是 reprocess 工作時才編號，一般摘要／筆記工作維持舊標籤
const REPROCESS_STAGE_TEXT = {
  correction: '名詞校正中',
  mode: '判斷講座／會議',
  summary: '整理摘要中',
  notes: '整理筆記中',
  slips: '檢查講者口誤',
  verify: '驗證中',
};
const REPROCESS_STEPS = ['correction', 'mode', 'summary', 'notes', 'verify'];
// 只有 reprocess 才會出現的步驟：看過其中之一，就能確定這是 reprocess 工作
const REPROCESS_ONLY = ['correction', 'mode', 'slips', 'verify'];
// 伺服器回報的進度文字（progress.msg）→ 步驟（stage 沒給時用文字判斷）
const REPROCESS_MSG = [
  ['名詞校正', 'correction'],
  ['判斷講座', 'mode'],
  ['整理摘要', 'summary'],
  ['整理筆記', 'notes'],
  ['口誤', 'slips'],
  ['驗證', 'verify'],
];
// 這次進度屬於重新整理的哪一步（認不出回 ''）
export function reprocessStage(m) {
  const p = (m && m.progress) || {};
  let stage = p.stage === 'correct' ? 'correction' : p.stage || '';
  if (REPROCESS_STAGE_TEXT[stage]) return stage;
  if (typeof p.msg === 'string') {
    const hit = REPROCESS_MSG.find(([w]) => p.msg.includes(w));
    if (hit) return hit[1];
  }
  return '';
}
// 是不是 reprocess 工作：伺服器有標動作就照動作；沒標就要看過 reprocess 專屬步驟（seen 由呼叫端記住）
export function isReprocessJob(m, seen) {
  const p = (m && m.progress) || {};
  const action = p.action || (m && m.aiAction) || '';
  if (action) return action === 'reprocess';
  return !!seen || REPROCESS_ONLY.includes(reprocessStage(m));
}
export function progressLabel(m, seenReprocess) {
  const base = STAGE_TEXT[m && m.status] || (m && m.status) || '';
  const p = (m && m.progress) || {};
  if ((p.action || (m && m.aiAction)) === 'deep_correct') return `${base}・${p.msg || '加強校對中'}`;
  if (isReprocessJob(m, seenReprocess)) {
    const rs = reprocessStage(m);
    if (rs) {
      const step = REPROCESS_STEPS.indexOf(rs);
      return `${base}・${REPROCESS_STAGE_TEXT[rs]}${step >= 0 ? `（第 ${step + 1}/${REPROCESS_STEPS.length} 步）` : ''}`;
    }
  }
  const stage = p.stage || '';
  const sub = PROGRESS_STAGE_TEXT[stage];
  if (!sub || sub === base) return base;
  const step = GPU_STEPS.indexOf(stage);
  return `${base}・${sub}${step >= 0 ? `（第 ${step + 1}/${GPU_STEPS.length} 步）` : ''}`;
}
// 停止／取消要取消「正在跑的那個工作」：摘要中是 AI 工作，其餘是轉錄工作
export function activeJobId(m) {
  if (!m) return null;
  return (m.status === 'summarizing' ? m.aiJobId : m.jobId) || null;
}
// 詳情頁頂端狀態：狀態文字＋伺服器的進度訊息（訊息跟狀態重複時不重複顯示）
export function bannerStatus(m) {
  const st = statusText(m);
  const msg = String((m && m.progress && m.progress.msg) || '').trim();
  return msg && !st.includes(msg) ? `${st}・${msg}` : st;
}
export function isProcessing(m) {
  const st = m && m.status;
  return !!st && st !== 'done' && st !== 'failed';
}

// 時間戳 → 要播哪一支錄音的第幾秒。
// 舊資料的多檔會議用 fi 標第幾支（t 是該支內秒數）；NAS 轉錄的 t 是整場絕對秒數，依各支長度換算。
// 舊資料的 t 可能是 "MM:SS"／"H:MM:SS" 字串
export function segSeconds(t) {
  if (typeof t === 'number') return isFinite(t) ? Math.max(0, t) : 0;
  const s = String(t == null ? '' : t).trim();
  if (!s) return 0;
  if (s.includes(':')) return s.split(':').reduce((acc, x) => acc * 60 + (Number(x) || 0), 0);
  return Math.max(0, Number(s) || 0);
}
export function seekTarget(seg, audio) {
  const parts = Array.isArray(audio) ? audio : [];
  const t = segSeconds(seg && seg.t);
  if (!parts.length) return null;
  if (seg && seg.fi != null) {
    const p = Math.min(parts.length - 1, Math.max(0, +seg.fi || 0));
    return { part: p, time: t };
  }
  let start = 0;
  for (let i = 0; i < parts.length; i++) {
    const d = Number(parts[i] && parts[i].durationSec) || 0;
    if (!d || t < start + d || i === parts.length - 1) return { part: i, time: Math.max(0, t - start) };
    start += d;
  }
  return { part: 0, time: t };
}

// 找出「這條摘要最可能出自哪一段逐字稿」：用字元二字組（bigram）重疊評分，免 API、即時
// 快速捲動軸：thumb 位置（0~1）↔ 頁面捲動位置。範圍從逐字稿頂端捲到「逐字稿底端剛好貼住畫面底」
export function railMap(frac, topY, bottomY, viewH) {
  const end = Math.max(topY, bottomY - viewH);
  const f = Math.min(1, Math.max(0, Number(frac) || 0));
  return topY + f * (end - topY);
}
export function railFrac(scrollY, topY, bottomY, viewH) {
  const end = Math.max(topY, bottomY - viewH);
  if (end <= topY) return 0;
  return Math.min(1, Math.max(0, (scrollY - topY) / (end - topY)));
}

// 秒數 → 顯示字串。單檔錄音用 h:mm:ss；多檔則標明是第幾支，方便回去找那個檔案。
export function timeLabel(seg) {
  const s = Math.max(0, Math.round((seg && seg.t) || 0));
  const mm = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  if (seg && seg.fi != null) return `第 ${seg.fi + 1} 支 ${mm + Math.floor(s / 3600) * 60}:${ss}`;
  const h = Math.floor(s / 3600);
  return h ? `${h}:${String(mm).padStart(2, '0')}:${ss}` : `${mm}:${ss}`;
}

// 條目（字串或 {text} 物件）的文字轉換：物件只改 text，其他欄位（id、src、group）保留
export const fixItem = (fix) => (x) =>
  typeof x === 'string' ? fix(x) : x && typeof x === 'object' && typeof x.text === 'string' ? { ...x, text: fix(x.text) } : x;

// 把字串轉換套用到學習筆記的每一個文字欄位（就地修改）。
// 專有名詞訂正若只改逐字稿與摘要，筆記會留著錯字——那正是使用者複習時在看的東西。
export function applyTermInNotes(notes, fix) {
  if (!notes || typeof notes !== 'object') return notes;
  const f = (v) => (typeof v === 'string' ? fix(v) : v);
  (notes.outline || []).forEach((o) => {
    o.title = f(o.title);
    o.anchor = f(o.anchor);
    o.points = (o.points || []).map(fixItem(fix));
  });
  (notes.concepts || []).forEach((c) => {
    c.term = f(c.term);
    c.plain = f(c.plain);
    c.why = f(c.why);
  });
  (notes.tables || []).forEach((t) => {
    t.title = f(t.title);
    t.headers = (t.headers || []).map(f);
    t.rows = (t.rows || []).map((r) => (Array.isArray(r) ? r.map(f) : r));
  });
  notes.figures = (notes.figures || []).map((x) =>
    typeof x === 'string' ? f(x) : { ...x, group: f(x.group), label: f(x.label), value: f(x.value) }
  );
  (notes.quiz || []).forEach((q) => {
    q.q = f(q.q);
    q.a = f(q.a);
  });
  return notes;
}

// ===== 加強前後的兩版切換 =====
// 加強有時會讓某一區變差。把前一版存在 meeting.alt['kind:section']，
// 做成「來回切換」而不是單向還原：按錯不會失去新版，可以反覆比較後再決定停在哪一版。
const altKey = (kind, section) => `${kind}:${section}`;

export function swapSectionVersion(meeting, kind, section) {
  const alt = meeting && meeting.alt;
  if (!alt) return false;
  const k = altKey(kind, section);
  if (!Array.isArray(alt[k])) return false;
  const box = meeting[kind] || (meeting[kind] = {});
  const cur = Array.isArray(box[section]) ? box[section] : [];
  box[section] = alt[k];
  alt[k] = cur;
  return true;
}

// 兩版各有好料時：聯集去重（目前版在前），並把合併前的目前版留作備份，之後仍可切換退回
export function mergeSectionVersions(meeting, kind, section) {
  const alt = meeting && meeting.alt;
  if (!alt) return false;
  const k = altKey(kind, section);
  if (!Array.isArray(alt[k])) return false;
  const box = meeting[kind] || (meeting[kind] = {});
  const cur = Array.isArray(box[section]) ? box[section] : [];
  // 各區判斷「同一條」的欄位不同；字串就用字串本身
  const keyOf = (x) =>
    typeof x === 'string' ? x : String((x && (x.term || x.title || x.q || x.text)) || JSON.stringify(x));
  const seen = new Set();
  const out = [];
  for (const x of cur.concat(alt[k])) {
    const kk = keyOf(x);
    if (!kk || seen.has(kk)) continue;
    seen.add(kk);
    out.push(x);
  }
  box[section] = out;
  alt[k] = cur; // 合併前的版本 → 之後按切換就能退回
  return true;
}

// 存下目前版當備份（加強前呼叫）
export function backupSection(meeting, kind, section) {
  const box = (meeting && meeting[kind]) || {};
  const cur = Array.isArray(box[section]) ? box[section] : [];
  if (!meeting.alt) meeting.alt = {};
  meeting.alt[altKey(kind, section)] = cur.slice();
}

export function hasAltVersion(meeting, kind, section) {
  return !!(meeting && meeting.alt && Array.isArray(meeting.alt[altKey(kind, section)]));
}

// 對著「從 NAS 重抓的最新版」刪除畫面上看到的那一條：先比對第 i 條是不是同一個內容，
// 不是就找內容相同的那條（同內容多條時取最靠近 i 的）；找不到回 false（內容已被別處改過，不要刪錯）。
export function removeSectionItemMatching(meeting, kind, section, i, value) {
  const box = (meeting && meeting[kind]) || {};
  const arr = box[section];
  if (!Array.isArray(arr)) return false;
  const key = JSON.stringify(value);
  let at = -1;
  if (i >= 0 && i < arr.length && JSON.stringify(arr[i]) === key) at = i;
  else {
    let best = Infinity;
    arr.forEach((x, j) => {
      if (JSON.stringify(x) === key && Math.abs(j - i) < best) {
        best = Math.abs(j - i);
        at = j;
      }
    });
  }
  if (at < 0) return false;
  arr.splice(at, 1);
  return true;
}

// 刪掉某一區的第 i 條（編輯模式用）
export function removeSectionItem(meeting, kind, section, i) {
  const box = (meeting && meeting[kind]) || {};
  const arr = box[section];
  if (!Array.isArray(arr) || i < 0 || i >= arr.length) return false;
  arr.splice(i, 1);
  return true;
}

export function bestSegIndex(text, transcript) {
  const clean = (s) =>
    String(s == null ? '' : s)
      .replace(/\[DRI:[^\]]*\]/g, '')
      .replace(/^問：|^答：|^Q:\s*|^A:\s*/g, '')
      .toLowerCase();
  const t = clean(text);
  const grams = new Set();
  for (let i = 0; i < t.length - 1; i++) {
    const g = t.slice(i, i + 2);
    if (/\S\S/.test(g)) grams.add(g);
  }
  if (!grams.size) return -1;
  let best = -1;
  let bestScore = 0;
  (transcript || []).forEach((seg, i) => {
    const st = clean((seg.speaker || '') + (seg.text || ''));
    let score = 0;
    grams.forEach((g) => {
      if (st.includes(g)) score++;
    });
    if (score > bestScore) {
      bestScore = score;
      best = i;
    }
  });
  return bestScore >= 2 ? best : -1;
}

// 在找到的段落「裡面」再往下找一層：指出最相符的那一句。
// 只捲到段落並整段閃爍時，段落一長就還是看不出出自哪裡。
export function bestSentence(text, segText) {
  const norm = (s) =>
    String(s == null ? '' : s)
      .replace(/\[DRI:[^\]]*\]/g, '')
      .replace(/^問：|^答：|^Q:\s*|^A:\s*/g, '')
      .toLowerCase();
  const src = String(segText == null ? '' : segText);
  // 中英文的句末標點都斷；保留標點在句尾，才能在原文中原樣找回來
  const parts = src.split(/(?<=[。！？；!?;\n])/).map((s) => s.trim()).filter(Boolean);
  if (parts.length <= 1) return src.trim();
  const t = norm(text);
  const grams = new Set();
  for (let i = 0; i < t.length - 1; i++) {
    const g = t.slice(i, i + 2);
    if (/\S\S/.test(g)) grams.add(g);
  }
  if (!grams.size) return '';
  let best = '';
  let bestScore = 0;
  for (const p of parts) {
    const np = norm(p);
    let score = 0;
    grams.forEach((g) => {
      if (np.includes(g)) score++;
    });
    // 長句天生比較容易命中 → 用長度稍微折抵，避免整段最長的那句總是贏
    const adj = score / Math.sqrt(Math.max(4, np.length));
    if (adj > bestScore) {
      bestScore = adj;
      best = p;
    }
  }
  return bestScore >= 0.25 ? best : '';
}

function speakerColors(segments) {
  const map = {};
  let i = 0;
  (segments || []).forEach((s) => {
    if (!(s.speaker in map)) {
      map[s.speaker] = SPEAKER_PALETTE[i % SPEAKER_PALETTE.length];
      i++;
    }
  });
  return map;
}
function toast(msg) {
  let t = document.getElementById('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), 2600);
}

// ===== 會議詳情頁的 AI 工作（加強／掃詞／問答／翻譯／筆記）=====
// AI 全部在 NAS 上跑：前端送出 runAI → 輪詢 waitJob → 重新抓會議 → 重繪。
// 等待期間使用者可能切到別頁再回來 → 畫面重繪、按鈕狀態被重置；工作其實還在跑。
// 所以工作狀態存在畫面之外，重繪後再畫回去（按鈕上顯示進度、點一下可取消）。
const detailTasks = new Map(); // `${meetingId}|${taskKey}` → { label, jobId }
const taskId = (meetingId, key) => `${meetingId}|${key}`;
// 詳情頁的介面狀態：校對面板開關、編輯模式。存檔／AI 完成會整頁重繪，狀態要留著；換到別場會議才重置。
// toTranscript：校對確認後重繪完要直接捲到逐字稿
const detailUi = { id: '', panel: false, edit: false, toTranscript: false };

function currentDetailId() {
  return location.hash.startsWith('#/m/') ? decodeURIComponent(location.hash.slice(4)) : '';
}
// ---- 單字訂正卡（ReviewItem v2：有 span 的待確認項目）----
const timeChip = (t, label) =>
  t != null && isFinite(t) ? `<button class="src-chip" type="button" data-rt="${Number(t)}">▶ ${esc(label || fmtSec(t))}</button>` : esc(label || '');
// 勾選清單的一列：☑ 錯字 → 正確（N 處）▸；展開看上下文、理由、自己改
function wordRowHtml(row, checked) {
  const r = row.cards[0];
  const ctx = splitContext(r.context || `【${r.span}】`)
    .map((p) => (p.hit ? `<mark class="word-hit">${esc(p.text)}</mark>` : esc(p.text)))
    .join('');
  const reason = splitTimes(r.reason || '')
    .map((p) => (p.label ? timeChip(p.t, p.label) : esc(p.text)))
    .join('');
  const scope = row.cards.reduce(
    (a, c) => {
      const x = reviewScope(c);
      return { transcript: a.transcript + x.transcript, items: a.items + x.items };
    },
    { transcript: 0, items: 0 }
  );
  const low = row.confidence === 'low' ? '<span class="wl-low" title="AI 沒把握，請看一下">⚠️</span>' : '';
  const editVal = row.action === 'replace' ? row.to : '';
  return `<div class="wl-row" data-row="${esc(row.id)}" data-rid="${esc(r.id)}">
    <div class="wl-line">
      <label class="wl-main"><input type="checkbox" class="wl-check"${checked ? ' checked' : ''}>
        <span class="wl-pair"><span class="wl-from">${esc(row.from)}</span><span class="wl-arrow">→</span><b class="wl-to">${esc(row.to)}</b></span>${low}</label>
      <span class="wl-n">${Number(row.count) || 0} 處</span>
      <button class="wl-more" type="button" aria-expanded="false" aria-label="看詳細">▸</button>
    </div>
    <div class="wl-detail" hidden>
      <div class="word-ctx">${ctx}</div>
      ${reason ? `<div class="word-reason">${reason}</div>` : ''}
      <div class="word-scope">${esc(scopeText(scope, '會改到：'))}</div>
      <div class="wl-edit"><input class="word-input" type="text" enterkeyhint="done" aria-label="自己改成" placeholder="自己改成…" value="${esc(editVal)}">
        <button class="act-btn wl-edit-btn" type="button">✏️ 自己改</button></div>
    </div>
  </div>`;
}
// 未辨識段落：匯出列下方一行，點開列出各段時間（時間鈕跳逐字稿＋播放）
function gapsLineHtml(m) {
  const gaps = (Array.isArray(m && m.gaps) ? m.gaps : []).filter((g) => g && isFinite(Number(g.start)) && isFinite(Number(g.end)));
  const min = gapMinutes(gaps);
  if (!min) return '';
  const chips = gaps
    .map((g) => `<button class="src-chip" type="button" data-gap-t="${Number(g.start)}">▶ ${esc(fmtSec(g.start))}–${esc(fmtSec(g.end))}</button>`)
    .join(' ');
  return `<details class="gaps-line" id="gapsLine"><summary>⚠️ 約 ${min} 分鐘未辨識</summary><div class="gaps-list">${chips}</div></details>`;
}

// 把執行中的工作狀態畫回按鈕（畫面可能剛重繪過）
function paintDetailTasks() {
  const id = currentDetailId();
  if (!id) return;
  let busy = false;
  let runningBtn = null;
  document.querySelectorAll('[data-task]').forEach((b) => {
    const k = taskId(id, b.dataset.task);
    const t = detailTasks.get(k);
    if (t) {
      busy = true;
      runningBtn = b;
      if (!b.dataset.origLabel) {
        b.dataset.origLabel = b.textContent;
        b._origOnclick = b.onclick;
        // AI 排隊（Claude 一次一件）可能要等一陣子，一定要留一條取消的路
        b.onclick = async () => {
          const cur = detailTasks.get(k);
          if (!cur || !cur.jobId) {
            toast('正在送出，請稍候…');
            return;
          }
          try {
            await api.cancelJob(cur.jobId);
            toast('正在取消…');
          } catch (e) {
            toast('取消失敗：' + errText(e));
          }
        };
      }
      b.textContent = `⏳ ${t.label}（點此取消）`;
    } else if (b.dataset.origLabel) {
      b.textContent = b.dataset.origLabel;
      b.removeAttribute('data-orig-label');
      if (b._origOnclick) {
        b.onclick = b._origOnclick;
        b._origOnclick = null;
      }
    }
  });
  // 有工作在跑就停用其他動作鈕，避免同時開第二個；執行中的那顆保持可按（用來取消）
  document.querySelectorAll('.act-btn').forEach((x) => (x.disabled = (busy && x !== runningBtn) || x.hasAttribute('data-locked')));
  // 執行中的按鈕在收合的選單／面板裡（加強選單、校對面板）→ 展開，才看得到進度、按得到取消
  const host = runningBtn && runningBtn.closest('[data-autoshow]');
  if (host) host.hidden = false;
}
function detailBusy() {
  const id = currentDetailId();
  if (!id) return false;
  for (const k of detailTasks.keys()) if (k.startsWith(id + '|')) return true;
  return false;
}

// 在 NAS 跑一項 AI 工作並等它結束；回傳伺服器寫回結果後的最新會議（已存進快取）
// action 可以是 AI 動作名稱，或是一個「送出請求並回傳伺服器回應」的函式（模式切換、還原原始逐字稿等）
// labelPrefix：伺服器回報的進度訊息前面加這段（全部加強用來保留「全部加強 2/3…」）
async function runServerAI(meetingId, key, initialLabel, action, params, onMsg, labelPrefix) {
  const k = taskId(meetingId, key);
  const t = { label: initialLabel, jobId: null };
  detailTasks.set(k, t);
  paintDetailTasks();
  try {
    t.jobId = typeof action === 'function' ? api.jobIdOf(await action()) : await api.runAI(meetingId, action, params || {});
    if (t.jobId) await api.waitJob(t.jobId, (p) => {
      const raw = p && (p.msg || (p.pct != null ? `${initialLabel.replace(/…$/, '')} ${p.pct}%` : ''));
      const msg = raw && labelPrefix ? `${labelPrefix.replace(/…$/, '')} ${raw}` : raw;
      if (msg && msg !== t.label) {
        t.label = msg;
        paintDetailTasks();
        if (onMsg) onMsg(msg);
      }
    });
    const fresh = await api.getMeeting(meetingId);
    await cacheMeeting(fresh);
    return fresh;
  } finally {
    detailTasks.delete(k);
    paintDetailTasks();
  }
}

// 匯出時要包含哪些段落（記住上次的選擇，通常每次都一樣）
const EXPORT_SEC_KEY = 'export_sections';
const EXPORT_SECTIONS = [
  { k: 'actionItems', label: '✅ 待辦事項 Action Item' },
  { k: 'mainPoints', label: '📌 會議重點 Main Point' },
  { k: 'qa', label: '❓ 會議提問 Q&A' },
  { k: 'notes', label: '📚 學習筆記（含表格）' },
  { k: 'transcript', label: '🗣️ 逐字稿 Transcribe' },
];
// 講座模式：沒有待辦，改為核心結論／講者建議／風險提醒／案例／重點／Q&A
const EXPORT_SECTIONS_LECTURE = [
  ...summarySections({ mode: 'lecture' }).map((s) => ({ k: s.k, label: s.label })),
  ...EXPORT_SECTIONS.slice(3),
];
function getExportSections(list = EXPORT_SECTIONS) {
  try {
    const o = JSON.parse(localStorage.getItem(EXPORT_SEC_KEY)) || {};
    return list.reduce((a, s) => ((a[s.k] = s.k === 'notes' ? o.notes === true : o[s.k] !== false), a), {});
  } catch (_) {
    return list.reduce((a, s) => ((a[s.k] = s.k !== 'notes'), a), {});
  }
}
// 匯出前選段落；回傳選項物件，取消則回傳 null
function pickExportSections(kind, lecture) {
  const list = lecture ? EXPORT_SECTIONS_LECTURE : EXPORT_SECTIONS;
  return new Promise((resolve) => {
    const cur = getExportSections(list);
    const ov = document.createElement('div');
    ov.className = 'sheet-ov';
    ov.innerHTML = `<div class="sheet">
      <div class="sheet-title">匯出${esc(kind)}：要包含哪些段落？</div>
      ${list.map(
        (s) => `<label class="sheet-btn"><input type="checkbox" data-k="${s.k}"${cur[s.k] ? ' checked' : ''} style="margin-right:10px">${esc(s.label)}</label>`
      ).join('')}
      <button class="sheet-btn primary" id="expGo">開始匯出</button>
      <button class="sheet-btn cancel" id="expCancel">取消</button>
    </div>`;
    document.body.appendChild(ov);
    const close = (v) => {
      ov.remove();
      resolve(v);
    };
    ov.onclick = (e) => {
      if (e.target === ov) close(null);
    };
    document.getElementById('expCancel').onclick = () => close(null);
    document.getElementById('expGo').onclick = () => {
      const picked = {};
      ov.querySelectorAll('input[data-k]').forEach((c) => (picked[c.dataset.k] = c.checked));
      if (!Object.values(picked).some(Boolean)) {
        alert('至少要選一個段落');
        return;
      }
      // 只覆寫這次清單上的鍵：講座與會議的選擇互不洗掉
      let prev = {};
      try {
        prev = JSON.parse(localStorage.getItem(EXPORT_SEC_KEY)) || {};
      } catch (_) {}
      localStorage.setItem(EXPORT_SEC_KEY, JSON.stringify({ ...prev, ...picked }));
      close(picked);
    };
  });
}

// 上傳中防止誤關頁面（關了也能續傳，但要重選檔案）
window.addEventListener('beforeunload', (e) => {
  if (upload && upload.phase !== 'error') {
    e.preventDefault();
    e.returnValue = '錄音還在上傳，離開會中斷（之後重選同樣的檔案可續傳），確定要離開嗎？';
    return e.returnValue;
  }
});

// ===== 連線狀態 =====
// 連不上 NAS 時顯示快取的資料，並在上方掛一條提示
function setOffline(on) {
  let b = document.getElementById('offline-banner');
  if (!on) {
    if (b) b.remove();
    return;
  }
  if (b) return;
  b = document.createElement('div');
  b.id = 'offline-banner';
  b.className = 'offline-banner';
  b.textContent = '⚠️ 連不上 NAS，目前顯示的是這台裝置上次存的資料（無法修改）';
  // 放在頂欄與內容之間（body 是上下排列的 App 外殼）
  if (view.parentNode) view.parentNode.insertBefore(b, view);
  else document.body.appendChild(b);
}
function noteFetchError(e) {
  if (e && e.offline) setOffline(true);
}

// 會議清單：伺服器成功就更新快取；失敗回 null（呼叫端沿用快取）
async function fetchList() {
  try {
    const l = await api.listMeetings();
    setOffline(false);
    await cacheList(l);
    return l.slice().sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
  } catch (e) {
    noteFetchError(e);
    return null;
  }
}
// 完整會議：伺服器優先，連不上用快取；都沒有回 null
async function loadMeeting(id) {
  try {
    const m = await api.getMeeting(id);
    setOffline(false);
    await cacheMeeting(m);
    return m;
  } catch (e) {
    noteFetchError(e);
    if (e && e.status === 404) return null;
    return await cachedMeeting(id);
  }
}
async function refreshGroups() {
  try {
    await loadGroups();
  } catch (e) {
    noteFetchError(e);
  }
}
async function savePushGroups() {
  try {
    await pushGroups();
  } catch (e) {
    toast('分類存到 NAS 失敗：' + errText(e));
  }
}

// 底部彈出的分類選單：回傳群組 id / null（移出分類）/ undefined（取消）
function pickGroup(current) {
  return new Promise((resolve) => {
    const ov = document.createElement('div');
    ov.className = 'sheet-ov';
    const gs = getGroups();
    ov.innerHTML = `<div class="sheet">
      <div class="sheet-title">設定分類</div>
      ${gs.map((g) => `<button class="sheet-btn${g.id === current ? ' cur' : ''}" data-g="${esc(g.id)}"><span class="g-dot" style="background:${esc(groupColor(g.id))}"></span>${esc(g.name)}${g.id === current ? ' ✓' : ''}</button>`).join('')}
      <button class="sheet-btn" data-g="__new">➕ 新增群組</button>
      ${current ? '<button class="sheet-btn" data-g="__none">🚫 移出分類</button>' : ''}
      <button class="sheet-btn cancel" data-g="__cancel">取消</button>
    </div>`;
    document.body.appendChild(ov);
    const done = (v) => {
      ov.remove();
      resolve(v);
    };
    ov.onclick = (e) => {
      if (e.target === ov) done(undefined);
    };
    ov.querySelectorAll('.sheet-btn').forEach((b) => {
      b.onclick = async () => {
        const g = b.dataset.g;
        if (g === '__cancel') return done(undefined);
        if (g === '__none') return done(null);
        if (g === '__new') {
          const name = prompt('新群組名稱：');
          if (!name || !name.trim()) return done(undefined);
          const ng = addGroup(name);
          if (ng) await savePushGroups();
          return done(ng ? ng.id : undefined);
        }
        done(g);
      };
    });
  });
}

// 清單卡片上的處理狀態徽章（完成的不顯示）
function statusBadge(m) {
  const st = m && m.status;
  if (!st || st === 'done') return '';
  const p = m.progress || {};
  const pct = isProcessing(m) && p.pct != null ? ` ${Math.round(p.pct)}%` : '';
  const label = st === 'failed' ? STAGE_TEXT.failed : (STAGE_TEXT[st] || st) + pct;
  return `<span class="st-badge st-${esc(st)}">${esc(label)}</span>`;
}
// 處理中／失敗的會議點進去看進度頁；完成的看詳情
const meetingHref = (m) => (m.status && m.status !== 'done' ? '#/job/' : '#/m/') + encodeURIComponent(m.id);

// groupId：undefined = 全部；'__none' = 未分類；其他 = 該群組
async function renderList(groupId) {
  const gen = renderGen; // 這次繪製的世代：換頁（router）後舊的輪詢自動停止
  const title = groupId === '__none' ? '🗂 未分類' : groupId ? `📂 ${groupName(groupId) || '分類'}` : 'DD會議紀錄';
  setHeader(title, !!groupId);
  if (groupId) backBtn.onclick = () => (location.hash = '#/groups');
  view.innerHTML = `<input type="search" id="search" class="search" placeholder="🔍 搜尋標題或內容" autocomplete="off" />
    <div id="listBody"></div>`;
  const body = document.getElementById('listBody');
  const si = document.getElementById('search');
  let all = await cachedList();
  const full = new Map(); // id → 完整會議（搜尋逐字稿用）

  const filtered = () => {
    const known = new Set(getGroups().map((g) => g.id));
    return groupId === '__none'
      ? all.filter((m) => !m.group || !known.has(m.group))
      : groupId
        ? all.filter((m) => m.group === groupId)
        : all;
  };
  const cardHtml = (m) => {
    const known = new Set(getGroups().map((g) => g.id));
    const sm = m.summary || {};
    const mp = (sm.conclusions && sm.conclusions.length ? sm.conclusions : sm.mainPoints || sm.keyPoints) || [];
    const snip = mp.length ? mp.map(itemText).join('、') : transcriptToText(m.transcript).slice(0, 60);
    const gn = m.group && known.has(m.group) ? groupName(m.group) : '';
    const gc = gn ? esc(groupColor(m.group)) : '';
    return `<div class="card tap" data-id="${esc(m.id)}">
        <div class="card-top"><h3>${esc(m.title)}</h3><button class="grp-chip${gn ? ' has' : ''}" type="button"${gn ? ` style="color:${gc};border-color:${gc};background:color-mix(in srgb, ${gc} 14%, transparent)"` : ''}><span class="g-dot" style="background:${gc || 'var(--muted)'}"></span>${esc(gn || '未分類')}</button></div>
        <div class="meta">${formatDate(m.createdAt)} ${statusBadge(m)}</div>
        <div class="snippet">${esc(snip)}</div>
      </div>`;
  };
  // 搜尋要比對逐字稿，但清單不含逐字稿 → 用完整會議（快取或向 NAS 補抓）
  let fetchingFull = false;
  const ensureFull = async () => {
    if (fetchingFull) return;
    fetchingFull = true;
    try {
      for (const m of all) {
        if (gen !== renderGen) return;
        if (full.has(m.id)) continue;
        let fm = await cachedMeeting(m.id);
        if (!fm || (m.updatedAt && (fm.updatedAt || 0) < m.updatedAt)) {
          try {
            fm = await api.getMeeting(m.id);
            await cacheMeeting(fm);
          } catch (_) {}
        }
        if (fm) full.set(m.id, fm);
      }
    } finally {
      fetchingFull = false;
    }
    if (gen === renderGen && si.value.trim()) draw();
  };
  const searchable = (m) => {
    const fm = full.get(m.id);
    return fm ? { ...m, transcript: fm.transcript, summary: fm.summary || m.summary } : m;
  };
  const draw = () => {
    const q = si.value;
    const ms = filtered();
    if (!ms.length && !q.trim()) {
      body.innerHTML = `<div class="empty">${groupId ? '這個分類還沒有會議<br>到「清單」點會議卡片上的分類標籤加入' : '還沒有會議記錄<br>點下方「＋ 新增會議」上傳錄音檔'}</div>`;
      return;
    }
    const shown = ms.filter((m) => matchMeeting(searchable(m), q));
    body.innerHTML = shown.length ? shown.map(cardHtml).join('') : '<div class="empty">找不到符合的會議</div>';
    body.querySelectorAll('.card').forEach((c) => {
      const m = all.find((x) => x.id === c.dataset.id);
      c.onclick = () => (location.hash = m ? meetingHref(m) : '#/m/' + encodeURIComponent(c.dataset.id));
      const chip = c.querySelector('.grp-chip');
      if (chip)
        chip.onclick = async (e) => {
          e.stopPropagation();
          if (!m) return;
          const r = await pickGroup(m.group);
          if (r === undefined) return;
          try {
            const fresh = await api.getMeeting(m.id); // 整份存檔 → 先拿最新完整版
            if (r === null) fresh.group = null;
            else fresh.group = r;
            fresh.editedAt = Date.now(); // 分類是真實編輯
            await cacheMeeting(await api.saveMeeting(fresh));
            m.group = fresh.group;
            await cacheList(all);
            draw();
          } catch (err) {
            alert('改分類失敗：' + errText(err));
          }
        };
    });
  };
  draw();
  si.oninput = () => {
    draw();
    if (si.value.trim()) ensureFull();
  };
  // 先秒開快取，再向 NAS 更新；有會議還在處理就每 5 秒刷新狀態
  const refresh = async () => {
    if (gen !== renderGen) return;
    const fresh = await fetchList();
    if (gen !== renderGen) return;
    if (fresh) {
      all = fresh;
      draw();
    }
    if (all.some(isProcessing)) setTimeout(refresh, 5000);
  };
  refresh();
}

// 分類頁：群組清單（新增／改名／刪除／點入看該組會議）
async function renderGroups() {
  setHeader('分類', false);
  const gen = renderGen; // 這次繪製的世代：換頁（router）後舊的輪詢自動停止
  const paint = (meetings) => {
    const gs = getGroups();
    const known = new Set(gs.map((g) => g.id));
    const count = (gid) => meetings.filter((m) => m.group === gid).length;
    const unCount = meetings.filter((m) => !m.group || !known.has(m.group)).length;
    view.innerHTML = `
    <button class="big" id="addGroupBtn">➕ 新增群組</button>
    <div id="groupList" style="margin-top:12px">
      ${gs
        .map((g) => {
          const gc = esc(groupColor(g.id));
          return `<div class="card tap group-card" data-g="${esc(g.id)}" style="border-left:5px solid ${gc}">
            <div class="card-top">
              <h3><span class="g-ico" style="background:color-mix(in srgb, ${gc} 20%, transparent);color:${gc}">📁</span>${esc(g.name)}</h3>
              <span class="grp-ops"><button class="grp-op g-edit" type="button" title="改名">✎</button><button class="grp-op g-del" type="button" title="刪除">🗑</button></span></div>
            <div class="meta" style="color:${gc};font-weight:600">${count(g.id)} 場會議</div>
          </div>`;
        })
        .join('')}
      ${unCount ? `<div class="card tap group-card" data-g="__none" style="border-left:5px solid var(--muted)"><div class="card-top"><h3><span class="g-ico" style="background:color-mix(in srgb, var(--muted) 20%, transparent)">🗂</span>未分類</h3></div><div class="meta">${unCount} 場會議</div></div>` : ''}
      ${!gs.length && !unCount ? '<div class="empty">還沒有群組<br>點上方「＋ 新增群組」建立</div>' : ''}
    </div>`;
    document.getElementById('addGroupBtn').onclick = async () => {
      const name = prompt('新群組名稱：');
      if (name && name.trim()) {
        addGroup(name);
        await savePushGroups();
        paint(meetings);
      }
    };
    view.querySelectorAll('#groupList .card').forEach((c) => {
      c.onclick = () => (location.hash = '#/g/' + c.dataset.g);
      const ed = c.querySelector('.g-edit');
      const del = c.querySelector('.g-del');
      if (ed)
        ed.onclick = async (e) => {
          e.stopPropagation();
          const g = gs.find((x) => x.id === c.dataset.g);
          const nn = prompt('群組改名：', g ? g.name : '');
          if (nn && nn.trim()) {
            renameGroup(c.dataset.g, nn);
            await savePushGroups();
            paint(meetings);
          }
        };
      if (del)
        del.onclick = async (e) => {
          e.stopPropagation();
          const n = count(c.dataset.g);
          if (!confirm(`刪除這個群組？${n ? `裡面的 ${n} 場會議不會被刪，會變成「未分類」。` : ''}`)) return;
          removeGroup(c.dataset.g);
          await savePushGroups();
          paint(meetings);
        };
    });
  };
  const cached = await cachedList();
  if (gen !== renderGen) return;
  paint(cached);
  await refreshGroups();
  const fresh = await fetchList();
  if (gen === renderGen) paint(fresh || (await cachedList()));
}

// ===== 上傳（分塊、可續傳）=====
// 上傳狀態存在畫面之外：使用者中途切到別頁再回來，還接得上進度。
let upload = null; // { files:[{name,size,sent}], total, startAt, phase:'uploading'|'creating'|'error', error, ac, retry }
// 新版 SW 接手時若正在上傳，先記著，等上傳結束再重整
let reloadAfterUpload = false;
export const uploadInProgress = (u) => !!u && (u.phase === 'uploading' || u.phase === 'creating');
function maybeReloadAfterUpload() {
  if (!reloadAfterUpload || uploadInProgress(upload)) return;
  reloadAfterUpload = false;
  window.location.reload();
}
const fmtMB = (b) => (b / 1048576).toFixed(b >= 1048576 * 100 ? 0 : 1) + ' MB';
const mmss = (sec) => {
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
};
// 檔名拆成「基底＋結尾數字」：沒有結尾數字的算 0（排在同基底的 1、2、3… 之前）
const splitTrailingNum = (name) => {
  const stem = String(name).replace(/\.[^./\\]+$/, '').trim();
  const m = stem.match(/^(.*?)[\s_\-(]*\(?(\d+)\)?$/);
  if (m && m[1].trim()) return { base: m[1].trim(), num: Number(m[2]) };
  return { base: stem, num: 0 };
};
const collate = (a, b) => a.localeCompare(b, undefined, { numeric: true });
export const sortByName = (files) =>
  files.slice().sort((a, b) => {
    const x = splitTrailingNum(a.name);
    const y = splitTrailingNum(b.name);
    return collate(x.base, y.base) || x.num - y.num || collate(a.name, b.name);
  });

function paintUpload() {
  const c = document.getElementById('upprog');
  if (!c || !upload) return;
  const sent = upload.files.reduce((a, f) => a + f.sent, 0);
  const pct = upload.total ? Math.floor((sent / upload.total) * 100) : 100;
  const label =
    upload.phase === 'creating'
      ? '上傳完成，建立會議中…'
      : upload.phase === 'error'
        ? '上傳中斷'
        : `上傳中 ${pct}%（${fmtMB(sent)} / ${fmtMB(upload.total)}）`;
  c.innerHTML = `
    <div class="prog-bar"><div class="prog-fill" style="width:${pct}%"></div></div>
    <div class="prog-label">${esc(label)}</div>
    <div class="up-files">${upload.files
      .map((f) => {
        const fp = f.size ? Math.floor((f.sent / f.size) * 100) : 100;
        return `<div class="up-file"><span class="up-name">${esc(f.name)}</span><span class="up-pct">${fp}%</span></div>`;
      })
      .join('')}</div>
    <div class="prog-time">已經過 ${mmss((Date.now() - upload.startAt) / 1000)}</div>
    ${upload.phase === 'error' ? `<div class="err" style="margin-top:8px">❌ ${esc(upload.error)}</div>` : ''}`;
  const ops = document.getElementById('upops');
  if (ops && ops.dataset.phase !== upload.phase) {
    ops.dataset.phase = upload.phase;
    ops.innerHTML =
      upload.phase === 'error'
        ? `<button class="big" id="upRetry">從中斷處繼續上傳</button><button class="big secondary" id="upDrop" style="margin-top:8px">放棄這次上傳</button>`
        : `<button class="big danger" id="upStop">■ 取消上傳</button>`;
    const r = document.getElementById('upRetry');
    if (r) r.onclick = () => upload && upload.retry && upload.retry();
    const d = document.getElementById('upDrop');
    if (d)
      d.onclick = () => {
        upload = null;
        refreshBanner();
        renderNew();
      };
    const s = document.getElementById('upStop');
    if (s)
      s.onclick = () => {
        if (!confirm('取消上傳？已傳的部分會留在 NAS 一陣子，重選同樣的檔案可以接著傳。')) return;
        if (upload && upload.ac) upload.ac.abort();
      };
  }
}

async function uploadAndCreate(files, title, group) {
  const list = files.slice(); // 順序＝畫面上使用者看到的順序，不再重排
  const ac = new AbortController();
  upload = {
    files: list.map((f) => ({ name: f.name, size: f.size, sent: 0 })),
    total: list.reduce((a, f) => a + f.size, 0),
    startAt: Date.now(),
    phase: 'uploading',
    error: '',
    ac,
    retry: () => uploadAndCreate(files, title, group),
  };
  const me = upload;
  if (location.hash === '#/new') renderNew();
  refreshBanner();
  // 上傳期間螢幕恆亮：畫面一暗頁面就被系統暫停，上傳隨即中斷
  await wakeLock.start();
  const ticker = setInterval(() => {
    paintUpload();
    refreshBanner();
  }, 1000);
  try {
    const ids = [];
    for (let i = 0; i < list.length; i++) {
      ids.push(
        await api.uploadFile(
          list[i],
          (p) => {
            me.files[i].sent = p.sent;
            paintUpload();
          },
          ac.signal
        )
      );
    }
    me.phase = 'creating';
    paintUpload();
    const m = await api.createMeeting({ title, group: group || null, uploads: ids });
    await cacheMeeting(m);
    if (upload === me) upload = null;
    location.hash = '#/job/' + encodeURIComponent(m.id);
  } catch (e) {
    if (upload !== me) return;
    if (ac.signal.aborted || (e && e.name === 'AbortError')) {
      upload = null;
      toast('已取消上傳');
      if (location.hash === '#/new') renderNew();
    } else {
      me.phase = 'error';
      me.error = errText(e);
      paintUpload();
    }
  } finally {
    clearInterval(ticker);
    await wakeLock.stop();
    refreshBanner();
    maybeReloadAfterUpload();
  }
}

function renderNew() {
  setHeader('新增會議', true);
  if (upload) {
    view.innerHTML = `<div class="card">
      <div class="warn">錄音正在上傳到 NAS。可以在 App 內切到別頁，<b>但不要切出 App 或鎖螢幕</b>；中斷了也沒關係，重選同樣的檔案會從中斷處接著傳。</div>
      ${wakeLock.isSupported() ? '<div class="hint" style="margin-top:8px">🔆 上傳期間會維持螢幕恆亮。</div>' : ''}
      <div class="progress" id="upprog"></div>
      <div id="upops"></div>
      <div class="hint" style="margin-top:6px">上傳完成後就可以關掉 App，轉錄與摘要在電腦／NAS 上進行。</div>
    </div>`;
    paintUpload();
    return;
  }
  const gs = getGroups();
  view.innerHTML = `
    <div class="card">
      <p style="margin-top:0">選擇會議錄音檔（mp3 / m4a / wav 等，可以很大、很長）</p>
      <label for="audio" class="file-pick" id="filePick">
        <span class="fp-icon">📁</span>
        <span class="fp-main">點此選擇錄音檔（可多選）</span>
        <span class="fp-name" id="fileName">尚未選擇檔案</span>
      </label>
      <input type="file" id="audio" accept="audio/*,video/*,.m4a,.mp3,.wav,.aac,.caf,.aiff,.mp4,.mov" multiple hidden />
      <div id="fileList" class="file-list"></div>
      <p style="margin-bottom:6px"><b>標題</b></p>
      <input type="text" id="newTitle" placeholder="（預設用檔名）" />
      <p style="margin-bottom:6px"><b>分類</b></p>
      <select id="newGroup" style="width:100%">
        <option value="">未分類</option>
        ${gs.map((g) => `<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('')}
      </select>
      <button class="big" id="go">上傳並開始轉錄</button>
      <div class="warn">多支檔案會依<b>檔名順序</b>接成一份逐字稿。上傳時請保持螢幕開啟、勿切出 App；萬一中斷，<b>重選同樣的檔案</b>會從中斷處續傳。上傳完就能關掉 App，轉錄在電腦顯示卡上跑。</div>
      <details class="hint" style="margin-top:12px">
        <summary style="cursor:pointer;font-weight:600">📌 錄音在「語音備忘錄」裡？點這看怎麼匯入</summary>
        <div style="margin-top:8px">
          iPhone 不允許網頁直接讀取語音備忘錄，只要先匯出一次即可：<br>
          1. 開「語音備忘錄」App → 點該則錄音<br>
          2. 點 <b>⋯ 或分享鈕</b> → <b>儲存到「檔案」</b> → 選個位置<br>
          3. 回這裡按上面的欄位 → <b>選擇檔案</b> → 找到那個 .m4a 選取
        </div>
      </details>
    </div>`;
  const titleInp = document.getElementById('newTitle');
  let selected = []; // 目前順序（上傳與 createMeeting 都照這個）
  let titleEdited = false;
  titleInp.addEventListener('input', () => {
    titleEdited = true;
  });
  const paintList = () => {
    const nameEl = document.getElementById('fileName');
    const listEl = document.getElementById('fileList');
    const fs = selected;
    if (!fs.length) nameEl.textContent = '尚未選擇檔案';
    else if (fs.length === 1) nameEl.textContent = `${fs[0].name}（${fmtMB(fs[0].size)}）`;
    else nameEl.textContent = `已選 ${fs.length} 支（依此順序接合）：`;
    listEl.innerHTML =
      fs.length > 1
        ? fs
            .map(
              (f, i) => `<div class="file-row"><span class="fr-no">${i + 1}.</span><span class="fr-name">${esc(f.name)}（${fmtMB(f.size)}）</span>` +
                `<button type="button" class="fr-btn" data-mv="up" data-i="${i}" aria-label="上移"${i === 0 ? ' disabled' : ''}>↑</button>` +
                `<button type="button" class="fr-btn" data-mv="down" data-i="${i}" aria-label="下移"${i === fs.length - 1 ? ' disabled' : ''}>↓</button></div>`
            )
            .join('')
        : '';
    if (fs.length) {
      titleInp.placeholder = defaultTitle(fs[0].name, Date.now());
      if (!titleEdited) titleInp.value = defaultTitle(fs[0].name, Date.now());
    }
    document.getElementById('filePick').classList.toggle('picked', !!fs.length);
  };
  document.getElementById('fileList').addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('button[data-mv]');
    if (!btn || btn.disabled) return;
    const i = Number(btn.dataset.i);
    const j = btn.dataset.mv === 'up' ? i - 1 : i + 1;
    if (j < 0 || j >= selected.length) return;
    [selected[i], selected[j]] = [selected[j], selected[i]];
    paintList();
  });
  document.getElementById('audio').onchange = (e) => {
    selected = sortByName(Array.from(e.target.files || []));
    paintList();
  };
  document.getElementById('go').onclick = async () => {
    if (!selected.length) {
      alert('請先選擇音檔');
      return;
    }
    const title = titleInp.value.trim() || defaultTitle(selected[0].name, Date.now());
    await uploadAndCreate(selected.slice(), title, document.getElementById('newGroup').value || null);
  };
}

// 常駐橫幅：上傳中離開上傳頁時，任何頁面都能一鍵回去
function refreshBanner() {
  const existing = document.getElementById('resume-banner');
  if (!upload || location.hash === '#/new') {
    if (existing) existing.remove();
    return;
  }
  const sent = upload.files.reduce((a, f) => a + f.sent, 0);
  const pct = upload.total ? Math.floor((sent / upload.total) * 100) : 100;
  const text = upload.phase === 'error' ? '⚠️ 上傳中斷 · 點此繼續' : `⬆️ 上傳中 ${pct}% · 點此查看`;
  if (existing) {
    existing.querySelector('#resumeGo').textContent = text;
    return;
  }
  const b = document.createElement('div');
  b.id = 'resume-banner';
  b.className = 'install-banner';
  b.innerHTML = `<span id="resumeGo"></span>`;
  b.querySelector('#resumeGo').textContent = text;
  b.onclick = () => (location.hash = '#/new');
  document.body.appendChild(b);
}

// ===== 處理進度頁（轉錄／摘要在 NAS 與電腦上跑，這裡每 3 秒問一次）=====
async function renderProgress(id) {
  setHeader('處理進度', true);
  const gen = renderGen; // 這次繪製的世代：換頁（router）後舊的輪詢自動停止
  view.innerHTML = `
    <div class="card">
      <h3 id="pgTitle" style="margin:0 0 8px"></h3>
      <div class="progress" id="jobprog"><div class="spinner"></div></div>
      <div id="jobops"></div>
      <div class="hint" style="margin-top:8px">轉錄在電腦顯示卡上跑，可以關掉 App，好了再回來看（有開 TG 通知會傳訊息給你）。</div>
    </div>`;
  let m = null;
  let opsKey = '';
  let seenReprocessJob = null; // 這個進度頁看過的 reprocess 工作（換成別的 AI 工作就忘掉）
  const paint = () => {
    const c = document.getElementById('jobprog');
    if (!c || !m) return;
    document.getElementById('pgTitle').textContent = m.title || '';
    const p = m.progress || {};
    const pct = m.status === 'failed' ? 0 : Math.max(0, Math.min(100, Number(p.pct) || 0));
    // 看過 reprocess 專屬步驟後，同一個工作的「整理摘要／整理筆記」也照 reprocess 編號
    if (REPROCESS_ONLY.includes(reprocessStage(m))) seenReprocessJob = m.aiJobId || m.jobId || true;
    else if (seenReprocessJob && seenReprocessJob !== true && m.aiJobId && m.aiJobId !== seenReprocessJob) seenReprocessJob = null;
    const stage = progressLabel(m, !!seenReprocessJob);
    c.innerHTML = `
      ${m.status === 'failed' ? `<div class="err">❌ ${esc(statusText(m))}</div>` : `<div class="prog-bar"><div class="prog-fill" style="width:${pct}%"></div></div>
      <div class="prog-label">${esc(stage)}${p.pct != null ? ` ${Math.round(pct)}%` : ''}</div>`}
      ${p.msg && m.status !== 'failed' ? `<div class="prog-time">${esc(p.msg)}</div>` : ''}
      ${p.engine === 'nas-cpu' ? `<div class="prog-key">🖥️ ${esc(NAS_CPU_TEXT)}</div>` : ''}
      ${m.createdAt ? `<div class="prog-time">已耗時 ${mmss((Date.now() - m.createdAt) / 1000)}</div>` : ''}`;
    const hasAudio = Array.isArray(m.audio) && m.audio.length > 0;
    const hasTr = Array.isArray(m.transcript) && m.transcript.length > 0;
    const key = `${m.status}|${hasAudio}|${hasTr}`;
    if (key === opsKey) return;
    opsKey = key;
    const ops = document.getElementById('jobops');
    const processing = isProcessing(m);
    ops.innerHTML =
      (processing ? `<button class="big danger" id="pgStop" style="margin-top:14px">■ 停止</button>` : '') +
      (!processing && hasAudio ? `<button class="big" id="pgRetr" style="margin-top:14px">🔁 重新轉錄</button>` : '') +
      (hasTr ? `<button class="big secondary" id="pgOpen" style="margin-top:8px">先看逐字稿</button>` : '') +
      `<button class="big danger" id="pgDel" style="margin-top:8px">取消並刪除</button>`;
    const stop = document.getElementById('pgStop');
    if (stop)
      stop.onclick = async () => {
        const jid = activeJobId(m);
        if (!jid) {
          toast('找不到這場的工作編號，請稍後再試');
          return;
        }
        const ask = m.status === 'summarizing'
          ? '停止這場摘要？逐字稿會保留，之後可以按「重新產生摘要」。'
          : '停止這場轉錄？之後可以按「重新轉錄」再跑一次。';
        if (!confirm(ask)) return;
        try {
          await api.cancelJob(jid);
          toast('已停止');
          tick();
        } catch (e) {
          alert('停止失敗：' + errText(e));
        }
      };
    const retr = document.getElementById('pgRetr');
    if (retr)
      retr.onclick = async () => {
        try {
          await api.retranscribe(id);
          toast('已重新排入轉錄');
          opsKey = '';
          tick();
        } catch (e) {
          alert('重新轉錄失敗：' + errText(e));
        }
      };
    const open = document.getElementById('pgOpen');
    if (open) open.onclick = () => (location.hash = '#/m/' + encodeURIComponent(id));
    document.getElementById('pgDel').onclick = async () => {
      if (!confirm('取消並刪除這場會議？（會移到 NAS 的垃圾桶，不會真的消失）')) return;
      try {
        if (isProcessing(m) && activeJobId(m)) await api.cancelJob(activeJobId(m)).catch(() => {});
        await api.deleteMeeting(id);
        await uncacheMeeting(id);
        toast('已取消並刪除');
        location.hash = '#/';
      } catch (e) {
        alert('刪除失敗：' + errText(e));
      }
    };
  };
  let timer = null;
  const tick = async () => {
    clearTimeout(timer);
    if (gen !== renderGen) return;
    let fresh = null;
    try {
      fresh = await api.getMeeting(id);
      setOffline(false);
      await cacheMeeting(fresh);
    } catch (e) {
      noteFetchError(e);
      if (e && e.status === 404) {
        toast('這場會議已不存在');
        location.hash = '#/';
        return;
      }
      fresh = m || (await cachedMeeting(id));
    }
    if (gen !== renderGen) return;
    if (fresh) {
      m = fresh;
      if (!m.status || m.status === 'done') {
        location.hash = '#/m/' + encodeURIComponent(id);
        return;
      }
      paint();
    }
    if (!m || isProcessing(m)) timer = setTimeout(tick, 3000);
  };
  // 已耗時每秒更新
  const clock = setInterval(() => {
    if (gen !== renderGen) clearInterval(clock);
    else if (m && isProcessing(m)) paint();
  }, 1000);
  tick();
}

// 詳情頁的浮動元件（錄音膠囊、逐字稿快速捲動軸）與觀察器；重畫或離開時卸掉
const detailWidgets = [];
function releaseDetailWidgets() {
  while (detailWidgets.length) {
    try {
      detailWidgets.pop()();
    } catch (_) {}
  }
}

async function renderDetail(id, preloaded) {
  // 先秒開快取，再向 NAS 拿最新版；有變就重畫
  let m = preloaded || null;
  if (!m) {
    const cached = await cachedMeeting(id);
    if (cached) {
      m = cached;
      api
        .getMeeting(id)
        .then(async (fresh) => {
          setOffline(false);
          await cacheMeeting(fresh);
          const changed = fresh.updatedAt !== cached.updatedAt || fresh.status !== cached.status;
          if (changed && currentDetailId() === id && !detailBusy()) renderDetail(id, fresh);
        })
        .catch((e) => {
          noteFetchError(e);
          if (e && e.status === 404 && currentDetailId() === id) {
            uncacheMeeting(id);
            toast('這場會議已不在 NAS 上');
            location.hash = '#/';
          }
        });
    } else {
      m = await loadMeeting(id);
    }
  }
  if (!m) {
    toast('找不到這場會議');
    location.hash = '#/';
    return;
  }
  if (currentDetailId() !== id) return; // 讀取期間使用者已離開
  setHeader('會議詳情', true);
  let lang = 'orig';

  // 統一存檔：存前先向 NAS 重讀最新版，把本次變更套在最新資料上再整份寫回
  // → 避免用「舊的 m」整份覆寫掉伺服器剛寫進來的 AI 結果（stale closure 競態）。
  // opts.edit=true 代表真實內容編輯（逐字稿/摘要/標題/分類），會 bump editedAt。
  // 失敗（連不上 NAS 等）時跳提示並回傳 null，畫面維持原狀。
  const LOCK_MSG = '整理中，完成後再修改';
  const locked = () => isProcessing(m);
  const lockNote = () => (locked() ? `<div class="hint lock-note" style="margin:0 0 8px">🔒 ${LOCK_MSG}</div>` : '');
  // opts.content=true：會改到逐字稿／摘要／筆記的存檔；整理期間一律擋下（伺服器也會 409）
  const persist = async (mutate, opts = {}) => {
    if (opts.content && locked()) {
      toast(LOCK_MSG);
      return null;
    }
    try {
      const fresh = await api.getMeeting(id);
      if (mutate(fresh) === STALE) {
        // 畫面上的內容已經不是最新版（別台或伺服器剛改過）→ 不存，換成最新版請使用者再操作一次
        m = fresh;
        await cacheMeeting(fresh);
        toast('內容已更新，請再操作一次');
        return null;
      }
      if (opts.edit) fresh.editedAt = Date.now();
      const saved = await api.saveMeeting(fresh);
      await cacheMeeting(saved);
      m = saved;
      return saved;
    } catch (e) {
      noteFetchError(e);
      if (e && e.status === 409) {
        try {
          const fresh = await api.getMeeting(id);
          await cacheMeeting(fresh);
          m = fresh;
          toast(LOCK_MSG);
          if (currentDetailId() === id) renderDetail(id, fresh);
          return null;
        } catch (e2) {
          noteFetchError(e2);
        }
      }
      alert('存檔失敗：' + errText(e));
      return null;
    }
  };

  const STALE = Symbol('stale');
  const sectionOf = (mm, src) => ((mm && mm[src[0]]) || {})[src[1]];

  // del：編輯模式下附上刪除鈕；此時不掛 data-jump，避免點刪除誤觸「跳到出處」
  const delBtn = (key, i) => `<button class="item-del" data-del="${key}:${i}" type="button">✕</button>`;
  // 出處時間標記：每個 src 一顆小鈕，點了跳到逐字稿那一句（有錄音就從那裡播放）
  const srcChips = (x, del) =>
    del
      ? ''
      : itemSrc(x)
          .map((s) => `<button class="src-chip" type="button" data-src-t="${s.t}" data-src-q="${esc(s.quote)}" title="${esc(s.quote)}">▶ ${esc(fmtSec(s.t))}</button>`)
          .join('');
  // 條目清單（字串或物件）；有 group 時依主題分組、編號連續；data-jump／刪除用原始索引
  const olHtml = (arr, key, del) => {
    if (!arr || !arr.length) return `<div class="meta" style="padding-left:4px">（無）</div>`;
    let n = 0;
    return groupItems(arr)
      .map((g) => {
        const start = n + 1;
        n += g.items.length;
        return (
          (g.group ? `<div class="item-group">${esc(g.group)}</div>` : '') +
          `<ol class="list${del ? ' editing' : ''}"${start > 1 ? ` start="${start}"` : ''}>${g.items
            .map(([x, i]) => `<li${del ? '' : ` data-jump="${key}:${i}"`}>${esc(itemText(x))}${srcChips(x, del)}${del ? delBtn(key, i) : ''}</li>`)
            .join('')}</ol>`
        );
      })
      .join('');
  };
  const qaHtml = (arr, del) =>
    arr && arr.length
      ? `<ol class="list qa${del ? ' editing' : ''}">${arr
          .map((x, i) => {
            const { q, a } = splitQA(x);
            return `<li${del ? '' : ` data-jump="qa:${i}"`}><div class="qa-q"><b>問：</b>${esc(q)}</div>${a ? `<div class="qa-a"><b>答：</b>${esc(a)}</div>` : ''}${srcChips(x, del)}${del ? delBtn('qa', i) : ''}</li>`;
          })
          .join('')}</ol>`
      : `<div class="meta" style="padding-left:4px">無</div>`;
  const numbered = (arr) => {
    let n = 0;
    return groupItems(arr || [])
      .map((g) => (g.group ? `【${g.group}】\n` : '') + g.items.map(([x]) => `${++n}. ${itemText(x)}`).join('\n'))
      .join('\n');
  };
  const qaText = (arr) =>
    arr && arr.length
      ? arr.map((x, i) => { const { q, a } = splitQA(x); return `${i + 1}. 問：${q}\n   答：${a}`; }).join('\n')
      : '無';
  // 匯出把關：待確認清單沒清空前，分享／PDF／Word 都不能用
  const exportBlocked = () => {
    const n = pendingReview(m).length;
    if (!n) return false;
    alert(exportLockText(n));
    return true;
  };

  const contentFor = (l) => {
    if (l === 'orig') return { transcript: m.transcript || [], summary: m.summary || {}, notes: m.notes || null };
    const t = m.translations && m.translations[l];
    // 譯本沒有筆記（例如翻譯當時還沒產生）→ 退回原文筆記，總比整區空白好
    return t ? { transcript: t.transcript || [], summary: t.summary || {}, notes: t.notes || m.notes || null } : null;
  };
  const viewMeeting = () => {
    const c = contentFor(lang) || contentFor('orig');
    // notes 也要跟著語言走，否則切到英文匯出時會變成「英文逐字稿＋中文筆記」
    return Object.assign({}, m, { transcript: c.transcript, summary: c.summary, notes: c.notes || null });
  };

  const audioParts = Array.isArray(m.audio) ? m.audio : [];
  const hasTranscript = Array.isArray(m.transcript) && m.transcript.length > 0;
  // 還在處理（例如重新轉錄、摘要中）或失敗：頂端掛一條狀態
  // 轉錄完成但摘要失敗：狀態是 done、error 有值 → 也要顯示錯誤與「重新產生摘要」
  const summaryFailed = m.status === 'done' && !!m.error;
  const stBanner = (!m.status || m.status === 'done') && !summaryFailed
    ? ''
    : isProcessing(m)
      ? `<div class="warn st-warn" id="stBanner">⏳ ${esc(bannerStatus(m))}${m.progress && m.progress.pct != null ? ` ${Math.round(m.progress.pct)}%` : ''}　<b>點此看進度</b></div>`
      : `<div class="warn st-warn"><div class="err">❌ ${esc(summaryFailed ? `摘要失敗：${m.error}` : statusText(m))}</div>${hasTranscript ? '<button class="act-btn" id="reSummary" data-task="summary" style="margin-top:8px">🔄 重新產生摘要</button>' : ''}</div>`;
  // 錄音放在逐字稿正上方（點時間標記播放）；不在 drawBody 重畫範圍內，切模式／存檔時播放不會中斷
  const audioCard = audioParts.length
    ? `<div class="card" id="audioCard">
      <div class="section-title" style="margin-top:0"><span>🎧 錄音</span></div>
      ${audioParts
        .map(
          (a, n) =>
            `<div class="audio-part" data-part="${n}">${audioParts.length > 1 ? `<div class="meta">第 ${n + 1} 支：${esc((a && (a.name || a.file)) || '')}</div>` : ''}` +
            `<audio controls preload="none" src="${esc(api.audioUrl(id, n))}"></audio></div>`
        )
        .join('')}
      <button type="button" class="act-btn audio-stop" id="audioStopBtn" aria-label="停止播放">⏹ 停止</button>
      <div class="hint">點逐字稿裡的<b>時間標記</b>可以從那裡開始播放。</div>
    </div>`
    : '';
  if (detailUi.id !== id) Object.assign(detailUi, { id, panel: false, edit: false, toTranscript: false });
  const lectureNow = meetingMode(m) === 'lecture';
  const warnHtml = (Array.isArray(m.warnings) ? m.warnings : []).filter((w) => typeof w === 'string' && w).map((w) => `<div class="export-warn">⚠️ ${esc(w)}</div>`).join('');
  // 講座／會議切換（有逐字稿才能重整）
  const kindSeg = hasTranscript
    ? `<div class="kind-row"><span class="kind-l">類型</span>
        <span class="kind-toggle"><button type="button" class="act-btn${lectureNow ? ' on' : ''}" data-kind="lecture" data-task="kind:lecture">🎤 講座</button><button type="button" class="act-btn${lectureNow ? '' : ' on'}" data-kind="meeting" data-task="kind:meeting">👥 會議</button></span></div>`
    : '';
  // AI 處理第一列：重新整理｜重新轉錄（只有一顆時獨佔一列）
  const row1Btns = [
    hasTranscript ? '<button type="button" class="act-btn" id="reprocessBtn" data-task="reprocess" title="重新整理（不重新轉錄）" aria-label="重新整理（不重新轉錄）">🔁 重新整理</button>' : '',
    audioParts.length ? '<button type="button" class="act-btn" id="retrBtn">🎙 重新轉錄</button>' : '',
  ].filter(Boolean);
  const aiRow1 = row1Btns.length ? `<div class="tb-row tb-${row1Btns.length}">${row1Btns.join('')}</div>` : '';
  // 第二列：加強結論（講座）／加強待辦（會議）｜加強重點｜加強Q&A
  const enhSecs = [
    lectureNow ? { k: 'conclusions', label: '加強結論', name: '核心結論', short: '結論' } : { k: 'actionItems', label: '加強待辦', name: '待辦事項', short: '待辦' },
    { k: 'mainPoints', label: '加強重點', name: lectureNow ? '重點' : '會議重點', short: '重點' },
    { k: 'qa', label: '加強Q&A', name: lectureNow ? '提問 Q&A' : '會議提問 Q&A', short: 'Q&A' },
  ];

  // 版面：標題 → 工具列（所有按鈕依功能分四組）→ 校對面板（按「校對」才展開）→ 狀態列 → 內容 → 錄音＋逐字稿 → 問答
  view.innerHTML = `
    <div class="card detail-head">
      <input type="text" id="titleInput" value="${esc(m.title)}" />
      <div class="meta" style="margin-top:8px">${formatDate(m.createdAt)}</div>
    </div>
    <div class="card toolbar" id="toolbar">
      <div class="tb-group" data-group="export">
        <div class="tb-label">📤 匯出</div>
        <div class="tb-row tb-4">
          <button class="act-btn" id="shareBtn" type="button">📤 分享</button>
          <button class="act-btn" id="pdfBtn" type="button">📄 PDF</button>
          <button class="act-btn" id="wordBtn" type="button">📝 Word</button>
          <button class="act-btn" id="copyAllBtn" type="button">📋 複製全文</button>
        </div>
        <div class="export-lock" id="exportLock" hidden></div>
      </div>
      <div class="tb-group" data-group="view">
        <div class="tb-label">👁 檢視</div>
        <div class="lang-toggle" id="langToggle">
          <button data-l="orig" class="active" type="button">原文</button>
          <button data-l="zh" type="button">中文</button>
          <button data-l="en" type="button">English</button>
          <button data-l="ja" type="button">日本語</button>
        </div>
        <div class="lang-toggle" id="modeToggle">
          <button data-mode="meeting" type="button">📋 ${lectureNow ? '講座摘要' : '會議摘要'}</button>
          <button data-mode="notes" type="button">📚 學習筆記</button>
        </div>
        ${kindSeg}
      </div>
      <div class="tb-group" data-group="review">
        <div class="tb-label">✅ 校對</div>
        <div class="tb-row${hasTranscript ? ' tb-review' : ' tb-1'}">
          <button class="tb-btn" id="reviewBtn" type="button" aria-controls="reviewPanel" aria-expanded="false"></button>
          ${hasTranscript ? '<button type="button" class="act-btn" id="deepCorrectBtn" data-task="deepCorrect" title="逐段重新掃描整份逐字稿的錯字">🔍 加強校對</button>' : ''}
        </div>
        ${gapsLineHtml(m)}
      </div>
      <div class="tb-group" data-group="ai">
        <div class="tb-label">🤖 AI 處理</div>
        ${aiRow1}
        <div class="tb-row tb-3" id="aiEnhRow">
          ${enhSecs.map((x) => `<button class="act-btn" type="button" data-enh="${x.k}" data-task="enh:${x.k}">${x.label}</button>`).join('')}
        </div>
        <div class="tb-row tb-2">
          <button type="button" class="act-btn" id="enhAllBtn" data-task="enhAll">⚡ 全部加強</button>
          <button type="button" class="tb-btn" id="editModeBtn" aria-pressed="false">✏️ 編輯模式</button>
        </div>
        <div class="tb-row tb-1" id="aiNotesRow" hidden></div>
      </div>
    </div>
    <div class="card review-panel" id="reviewPanel" data-autoshow hidden>
      <div class="review-box" id="reviewCard" hidden></div>
      <div id="corrBody"></div>
      <div class="terms-sub" id="termsSub">
        <div class="panel-sub-t">📝 專有名詞（手動訂正）</div>
        <div id="termsBody"></div>
      </div>
    </div>
    ${stBanner}
    ${warnHtml}
    <div id="detailBody"><div id="sumBody"></div>${audioCard}<div id="trBody"></div></div>
    <div class="card" id="chatCard">
      <div class="section-title" style="margin-top:0">💬 問這場會議 <button class="copy" id="chatClear" hidden>清除紀錄</button></div>
      <div id="chatLog"></div>
      <textarea id="chatInput" rows="2" placeholder="輸入問題，例如：這場會議最後的結論是什麼？"></textarea>
      <button class="big" id="chatAsk" data-task="ask">送出問題</button>
      <div class="hint">AI 只根據這場會議的逐字稿回答；問答會存在這場會議裡。</div>
    </div>
    <button class="big danger" id="del" style="margin-top:16px">刪除這場會議</button>`;

  const bodyEl = document.getElementById('detailBody'); // 內容＋錄音＋逐字稿的外框（跳轉、摺疊都在這裡面找）
  const sumBody = document.getElementById('sumBody'); // 摘要／筆記卡（隨模式、語言重畫）
  const trBody = document.getElementById('trBody'); // 逐字稿（隨語言重畫）

  // ===== 錄音播放 =====
  // 只有一個浮動元件：迷你播放器。條件＝有一支在播（或播到一半暫停）且錄音卡不在畫面上（IntersectionObserver）。
  // 卡片看得到時一律不浮出任何東西，避免蓋住卡片本身。
  releaseDetailWidgets(); // 重畫詳情頁：先卸掉上一次的浮動元件／觀察器
  let audioActive = null; // 目前（最近一次）播放中的 <audio>；停止後為 null
  let audioCardVisible = true;
  const audioCardEl = document.getElementById('audioCard');
  const audioEls = [...view.querySelectorAll('#audioCard audio')];
  if (audioCardEl) {
    view.insertAdjacentHTML(
      'beforeend',
      `<div class="mini-player" id="miniPlayer" role="region" aria-label="迷你播放器" hidden>` +
        `<span class="mp-part" id="mpPart"></span>` +
        `<button type="button" class="mp-btn" id="mpToggle" aria-label="暫停">⏸</button>` +
        `<span class="mp-time" id="mpTime">0:00</span>` +
        `<button type="button" class="mp-btn mp-stop" id="mpStop" aria-label="停止">⏹ 停止</button>` +
        `<button type="button" class="mp-btn mp-x" id="mpClose" aria-label="關閉並停止">✕</button>` +
        `</div>`
    );
  }
  const mini = document.getElementById('miniPlayer');
  const paintMini = () => {
    if (!mini) return;
    mini.hidden = !(audioActive && !audioCardVisible);
    if (!audioActive) return;
    const playing = audioActive._playing === true;
    const tg = document.getElementById('mpToggle');
    tg.textContent = playing ? '⏸' : '▶';
    tg.setAttribute('aria-label', playing ? '暫停' : '繼續播放');
    document.getElementById('mpPart').textContent = `第 ${audioEls.indexOf(audioActive) + 1} 支`;
    document.getElementById('mpTime').textContent = fmtSec(audioActive.currentTime || 0);
  };
  const markCur = (a) =>
    view.querySelectorAll('#audioCard .audio-part').forEach((w) => w.classList.toggle('cur', !!a && w.contains(a)));
  audioEls.forEach((a) => {
    a.addEventListener('play', () => {
      audioEls.forEach((o) => o !== a && !o.paused && o.pause());
      a._playing = true;
      audioActive = a;
      markCur(a);
      paintMini();
    });
    a.addEventListener('pause', () => {
      a._playing = false;
      paintMini();
    });
    a.addEventListener('ended', () => {
      a._playing = false;
      audioActive = null;
      markCur(null);
      paintMini();
    });
    a.addEventListener('timeupdate', paintMini);
  });
  if (audioCardEl && typeof IntersectionObserver === 'function') {
    const io = new IntersectionObserver((es) => {
      es.forEach((e) => (audioCardVisible = e.isIntersecting));
      paintMini();
    });
    io.observe(audioCardEl);
    detailWidgets.push(() => io.disconnect());
  }
  detailWidgets.push(() => mini && mini.remove());

  // 點時間標記：找出是第幾支、第幾秒，從那裡播放（卡片不在畫面時由迷你播放器控制，不用捲回頂端）
  const seekTo = (seg) => {
    const tgt = seekTarget(seg, audioParts);
    if (!tgt) return;
    audioEls.forEach((a, n) => n !== tgt.part && a.pause());
    const a = audioEls[tgt.part];
    if (!a) return;
    markCur(a);
    a._playStart = tgt.time;
    const go = () => {
      try {
        a.currentTime = tgt.time;
      } catch (_) {}
      const pr = a.play && a.play();
      if (pr && pr.catch) pr.catch(() => {});
    };
    if (a.readyState >= 1) go();
    else {
      a.addEventListener('loadedmetadata', go, { once: true });
      a.preload = 'metadata';
      if (a.load) a.load();
    }
  };
  // 停止：暫停並回到這次播放的起點（沒有指定起點就回 0）；從時間標記／出處鈕開始的播放也一併停掉
  const stopAllAudio = () => {
    audioEls.forEach((a) => {
      try {
        a.pause();
      } catch (_) {}
      try {
        a.currentTime = a._playStart || 0;
      } catch (_) {}
      a._playStart = 0;
    });
    audioActive = null;
    markCur(null);
    paintMini();
  };
  if (mini) {
    document.getElementById('mpToggle').onclick = () => {
      const a = audioActive;
      if (!a) return;
      if (a._playing === true) a.pause();
      else {
        const pr = a.play && a.play();
        if (pr && pr.catch) pr.catch(() => {});
      }
    };
    document.getElementById('mpStop').onclick = stopAllAudio;
    document.getElementById('mpClose').onclick = stopAllAudio;
  }
  const stopBtn = document.getElementById('audioStopBtn');
  if (stopBtn) stopBtn.onclick = stopAllAudio;
  const retrBtn = document.getElementById('retrBtn');
  if (retrBtn)
    retrBtn.onclick = async () => {
      if (isProcessing(m)) {
        location.hash = '#/job/' + encodeURIComponent(id);
        return;
      }
      if (!confirm('用電腦重新轉錄這場錄音？完成後會取代目前的逐字稿與摘要（手動改過的字會被覆蓋）。')) return;
      try {
        await api.retranscribe(id);
        location.hash = '#/job/' + encodeURIComponent(id);
      } catch (e) {
        alert('重新轉錄失敗：' + errText(e));
      }
    };
  const stB = document.getElementById('stBanner');
  if (stB) stB.onclick = () => (location.hash = '#/job/' + encodeURIComponent(id));
  const reSum = document.getElementById('reSummary');
  if (reSum)
    reSum.onclick = async () => {
      if (detailBusy()) return;
      try {
        const fresh = await runServerAI(id, 'summary', '摘要中…', 'summary');
        renderDetail(id, fresh);
        toast('摘要完成 ✓');
      } catch (e) {
        alert('摘要失敗：' + errText(e));
      }
    };

  // ===== 摘要模式：會議摘要（原有三區）／學習筆記（研討會、上課用的五區）=====
  // 每場會議各自記住上次看的模式：研討會下次進來就直接是學習筆記。
  const MODE_KEY = 'detail_mode';
  const getModeMap = () => {
    try {
      return JSON.parse(localStorage.getItem(MODE_KEY)) || {};
    } catch (_) {
      return {};
    }
  };
  let mode = getModeMap()[id] === 'notes' ? 'notes' : 'meeting';
  const setMode = (v, force) => {
    const next = v === 'notes' ? 'notes' : 'meeting';
    // 模式沒變就別重繪：重繪會把表格的橫向捲動位置歸零，看起來像「滑一下就彈回去」
    if (!force && next === mode && sumBody.childElementCount) return;
    mode = next;
    const mm = getModeMap();
    mm[id] = mode;
    localStorage.setItem(MODE_KEY, JSON.stringify(mm));
    document.querySelectorAll('#modeToggle button').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
    // AI 處理：學習筆記模式多一列「產生／重新產生學習筆記」
    const nr = document.getElementById('aiNotesRow');
    if (nr) {
      nr.hidden = mode !== 'notes';
      nr.innerHTML =
        mode === 'notes'
          ? `<button class="act-btn" type="button" data-gen-notes data-task="notes">📚 ${m.notes ? '重新產生學習筆記' : '產生學習筆記'}</button>`
          : '';
      wireEnhButtons();
      paintDetailTasks();
    }
    drawBody(lang);
  };

  const tableHtml = (t, del) => {
    const head = t.headers.map((h) => `<th>${esc(h)}</th>`).join('');
    const body = t.rows
      .map((r) => `<tr>${t.headers.map((_, i) => `<td>${esc(r[i] || '')}</td>`).join('')}</tr>`)
      .join('');
    return `<div class="nt-table">${t.title || del ? `<div class="nt-table-title">${esc(t.title || '')}${del || ''}</div>` : ''}
      <div class="nt-table-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></div>`;
  };

  // 學習筆記卡片。沒產生過就顯示產生按鈕（按需產生，不自動燒額度）
  const notesCardHtml = (c, secHead, coll) => {
    const n = c.notes || m.notes;
    if (!n) {
      return `<div class="card mode-pane">
        <div class="hint" style="margin-top:0">學習筆記把研討會／課程的內容重整成<b>複習用</b>的形式：章節大綱、重要概念、對照表、關鍵數據、自我測驗。</div>
        <div class="warn" style="margin-top:8px">建議<b>先做完上方「校對」</b>再產生——筆記的品質完全取決於逐字稿。</div>
        <button class="big" id="genNotes" data-task="notes">📚 產生學習筆記</button>
      </div>`;
    }
    const ed = (k) => editSec === k;
    const outline = (n.outline || [])
      .map(
        (o, i) =>
          `<div class="nt-sec"${ed('nt-o') ? '' : ` data-jump="nt:${i}"`}><div class="nt-sec-t">${i + 1}. ${esc(o.title)}${srcChips(o, ed('nt-o'))}${ed('nt-o') ? delBtn('nt-o', i) : ''}</div>` +
          `${(o.points || []).length ? `<ul class="list">${o.points.map((p) => `<li>${esc(itemText(p))}${srcChips(p, ed('nt-o'))}</li>`).join('')}</ul>` : ''}</div>`
      )
      .join('');
    const concepts = (n.concepts || [])
      .map(
        (x, i) =>
          `<div class="nt-concept"><div class="nt-term">${esc(x.term)}${srcChips(x, ed('nt-c'))}${ed('nt-c') ? delBtn('nt-c', i) : ''}</div><div class="nt-plain">${esc(x.plain)}</div>` +
          `${x.why ? `<div class="nt-why">→ ${esc(x.why)}</div>` : ''}</div>`
      )
      .join('');
    const tables = (n.tables || []).map((t, i) => tableHtml(t, ed('nt-t') ? delBtn('nt-t', i) : '')).join('');
    // 依 group 分組顯示：同主題的數字排在一起才能互相對照；標籤在左、數值靠右對齊便於掃描
    const figs = n.figures || [];
    let figures = '';
    if (figs.length) {
      const order = [];
      const byGroup = new Map();
      figs.forEach((f, i) => {
        const g = (f && f.group) || '其他';
        if (!byGroup.has(g)) {
          byGroup.set(g, []);
          order.push(g);
        }
        byGroup.get(g).push([f, i]);
      });
      figures = order
        .map(
          (g) =>
            `${order.length > 1 ? `<div class="fig-g">${esc(g)}</div>` : ''}` +
            byGroup
              .get(g)
              .map(
                ([f, i]) =>
                  `<div class="fig-row"><span class="fig-l">${esc(f.label)}</span>` +
                  `<span class="fig-v">${esc(f.value)}</span>${srcChips(f, ed('nt-f'))}${ed('nt-f') ? delBtn('nt-f', i) : ''}</div>`
              )
              .join('')
        )
        .join('');
    }
    const quiz = (n.quiz || [])
      .map((x, i) => `<div class="nt-quiz"><div class="nt-q">Q${i + 1}. ${esc(x.q)}${srcChips(x, ed('nt-q'))}${ed('nt-q') ? delBtn('nt-q', i) : ''}</div><div class="nt-a">${esc(x.a)}</div></div>`)
      .join('');
    const none = '<div class="meta" style="padding-left:4px">（無）</div>';
    return `<div class="card mode-pane">
      ${secHead('nt-o', '📑 章節大綱 Outline', 'nt-o', 'margin-top:0')}
      <div class="sec-body" data-secbody="nt-o"${coll['nt-o'] ? ' hidden' : ''}>${outline || none}</div>
      ${secHead('nt-c', '💡 重要概念 Concepts', 'nt-c')}
      <div class="sec-body" data-secbody="nt-c"${coll['nt-c'] ? ' hidden' : ''}>${concepts || none}</div>
      ${secHead('nt-t', '📊 對照表 Tables', 'nt-t')}
      <div class="sec-body" data-secbody="nt-t"${coll['nt-t'] ? ' hidden' : ''}>${tables || none}</div>
      ${secHead('nt-f', '🔢 關鍵數據 Key Figures', 'nt-f')}
      <div class="sec-body" data-secbody="nt-f"${coll['nt-f'] ? ' hidden' : ''}>${figures || none}</div>
      ${secHead('nt-q', '✍️ 自我測驗 Quiz', 'nt-q')}
      <div class="sec-body" data-secbody="nt-q"${coll['nt-q'] ? ' hidden' : ''}>${quiz || none}</div>
      <div class="hint" style="margin-top:10px">點章節可跳到它在逐字稿中的出處；標題可摺疊／展開。重新產生在上方「AI 處理」。</div>
    </div>`;
  };

  let editSec = null; // 目前處於編輯模式的區塊代碼（一次只開一區）
  let railSegs = []; // drawBody 設定：目前檢視語言的逐字稿（快速捲動軸的時間氣泡用）
  let copyAllText = () => ''; // drawBody 設定：目前畫面的全文
  let curJump = null; // drawBody 設定：(秒數, 原句) → 跳到逐字稿那一句（待確認清單的時間鈕也用）

  const drawBody = (l) => {
    const c = contentFor(l);
    if (!c) {
      sumBody.innerHTML = `<div class="card"><div class="progress"><div class="spinner"></div><div id="tprogmsg">翻譯中…</div></div></div>`;
      trBody.innerHTML = '';
      return;
    }
    railSegs = c.transcript || [];
    const s = c.summary || {};
    const actionItems = s.actionItems || [];
    const mainPoints = s.mainPoints || s.keyPoints || [];
    const qa = s.qa || [];
    const lecture = meetingMode(m) === 'lecture';
    const SECS = summarySections(m); // 講座／會議各自的區塊
    const colors = speakerColors(c.transcript);
    const isOrig = l === 'orig';
    // 時間標記：約每分鐘插一個，畫面才不會被時間塞滿。多檔錄音各自從 0 分開始計。
    let lastMark = null;
    const segHtml = (c.transcript || [])
      .map((seg, i) => {
        let mark = '';
        if (seg.t != null) {
          const key = `${seg.fi == null ? '' : seg.fi}:${Math.floor(seg.t / 60)}`;
          if (key !== lastMark) {
            lastMark = key;
            mark = audioParts.length
              ? `<div class="time-mark seekable" data-seek="${i}" title="從這裡播放">▶ ${esc(timeLabel(seg))}</div>`
              : `<div class="time-mark">${esc(timeLabel(seg))}</div>`;
          }
        }
        return (
          mark +
          `<div class="seg" data-seg="${i}"${isOrig ? ` data-i="${i}"` : ''}>` +
          `<span class="spk" style="color:${colors[seg.speaker] || 'var(--ink)'}">${esc(seg.speaker)}</span>` +
          `<span class="seg-t">${esc(seg.text)}</span></div>`
        );
      })
      .join('');
    const speakers = Object.keys(colors);
    const chipsHtml =
      isOrig && speakers.length
        ? `<div class="spk-rename">${speakers
            .map((sp) => `<button class="spk-chip" data-spk="${esc(sp)}" style="color:${colors[sp]};border-color:${colors[sp]}">✎ ${esc(sp)}</button>`)
            .join('')}</div>`
        : '';
    // 摺疊狀態（跨會議記住偏好）
    const COLL_KEY = 'sec_collapsed';
    const getColl = () => {
      try {
        return JSON.parse(localStorage.getItem(COLL_KEY)) || {};
      } catch (_) {
        return {};
      }
    };
    const setCollapsed = (k, v) => {
      const cc = getColl();
      cc[k] = v ? 1 : 0;
      localStorage.setItem(COLL_KEY, JSON.stringify(cc));
      const b = bodyEl.querySelector(`[data-secbody="${k}"]`);
      const ch = bodyEl.querySelector(`.sec-head[data-sec="${k}"] .chev`);
      if (b) b.hidden = !!v;
      if (ch) ch.textContent = v ? '▸' : '▾';
    };
    const coll = getColl();
    // 區塊代碼 → 實際的資料位置，供「兩版切換／合併／編輯」使用
    const SEC_SRC = {
      ai: ['summary', 'actionItems'], mp: ['summary', 'mainPoints'], qa: ['summary', 'qa'],
      lc: ['summary', 'conclusions'], la: ['summary', 'advice'], lr: ['summary', 'risks'], lk: ['summary', 'cases'],
      'nt-o': ['notes', 'outline'], 'nt-c': ['notes', 'concepts'], 'nt-t': ['notes', 'tables'],
      'nt-f': ['notes', 'figures'], 'nt-q': ['notes', 'quiz'],
    };
    const secHead = (k, title, copyKey, extra) => {
      const src = SEC_SRC[k];
      // 只有原文檢視可以編輯／切換版本（譯本是衍生資料，改了會被下次翻譯蓋掉）
      const editable = src && isOrig;
      const hasAlt = editable && hasAltVersion(m, src[0], src[1]);
      const inEdit = editable && editSec === k;
      // 換版／合併／編輯／複製只在「編輯模式」出現；平常只留摺疊箭頭，畫面乾淨
      const tools = detailUi.edit
        ? (hasAlt ? `<button class="copy" data-swap="${k}">↩ 換版</button><button class="copy" data-mergev="${k}">⊕ 合併</button>` : '') +
          (editable ? `<button class="copy${inEdit ? ' on' : ''}" data-edit="${k}">${inEdit ? '完成' : '✏️ 編輯'}</button>` : '') +
          `<button class="copy" data-copy="${copyKey}">複製</button>`
        : '';
      return `<div class="section-title sec-head" data-sec="${k}"${extra ? ` style="${extra}"` : ''}><span class="sec-t">${title}</span><span class="sec-right">${tools}<span class="chev">${coll[k] ? '▸' : '▾'}</span></span></div>`;
    };

    // 會議摘要三區 vs 學習筆記五區：只有這張卡片隨模式切換，逐字稿以下兩模式共用
    const secItems = (sec) => (sec.k === 'mainPoints' ? mainPoints : s[sec.k] || []);
    const meetingCard = `
      <div class="card mode-pane">
        ${SECS.map(
          (sec, n) =>
            `${secHead(sec.code, esc(sec.label), sec.code, n === 0 ? 'margin-top:0' : '')}
        <div class="sec-body" data-secbody="${sec.code}"${coll[sec.code] ? ' hidden' : ''}>${sec.k === 'qa' ? qaHtml(qa, editSec === 'qa') : olHtml(secItems(sec), sec.code, editSec === sec.code)}</div>`
        ).join('')}
        <div class="hint" style="margin-top:10px">點各區標題可摺疊／展開；<b>點任一條內容</b>可跳到它在逐字稿中的出處</div>
      </div>`;

    const editing = detailUi.edit && isOrig; // 逐字稿改字、講者改名只在編輯模式
    sumBody.innerHTML = mode === 'notes' ? notesCardHtml(c, secHead, coll) : meetingCard;
    trBody.innerHTML = `
      ${secHead('tr', '🗣️ 逐字稿', 'tr')}
      <div class="sec-body" data-secbody="tr"${coll.tr ? ' hidden' : ''}>
        ${editing ? `<div class="hint" style="margin:0 4px 6px">點語者可改名；<b>點段落文字可直接修改錯字</b></div>` : ''}
        ${editing ? chipsHtml : ''}
        <div class="transcript-box">${segHtml || '<div class="meta">（無逐字稿）</div>'}</div>
      </div>`;

    // 摺疊/展開（點「複製」不觸發）
    bodyEl.querySelectorAll('.sec-head').forEach((h) => {
      h.onclick = (e) => {
        if (e.target.closest('.copy')) return;
        const k = h.dataset.sec;
        setCollapsed(k, !getColl()[k]);
      };
    });

    // 點摘要條目 → 跳到逐字稿出處（本機文字比對，不耗額度）
    // srcText：這一條摘要/筆記的原文，用來在段落裡再標出最相符的那一句
    const jumpTo = (idx, srcText) => {
      setCollapsed('tr', false);
      const target = bodyEl.querySelector(`.seg[data-seg="${idx}"]`);
      if (!target) return;
      // 先清掉上一次的螢光
      bodyEl.querySelectorAll('mark.hit').forEach((el) => el.replaceWith(...el.childNodes));
      const seg = (c.transcript || [])[idx];
      const hit = srcText && seg ? bestSentence(srcText, seg.text || '') : '';
      if (hit) {
        const txtNode = target.querySelector('.seg-t');
        if (txtNode) {
          const raw = seg.text || '';
          const at = raw.indexOf(hit);
          if (at >= 0) {
            txtNode.innerHTML =
              esc(raw.slice(0, at)) + '<mark class="hit">' + esc(hit) + '</mark>' + esc(raw.slice(at + hit.length));
          }
        }
      }
      if (target.scrollIntoView) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.classList.add('flash');
      setTimeout(() => target.classList.remove('flash'), 2400);
    };
    // 學習筆記的章節用「原話錨點」定位（逐字稿沒有時間戳），錨點不可靠時退回用標題比對
    const noteOutline = (c.notes || m.notes || {}).outline || [];
    const noteAnchors = noteOutline.map((o) => o.anchor || o.title);
    const arrMap = {
      ai: actionItems, mp: mainPoints, qa, lc: s.conclusions || [], la: s.advice || [], lr: s.risks || [], lk: s.cases || [],
      'nt': noteAnchors,
    };
    const srcMap = { ...arrMap, nt: noteOutline };
    // 有出處（src）的條目直接跳到那個時間點的那一句
    // 先找出真正的那一段（多支錄音時每支 t 從 0 開始），再跳逐字稿；play=true 時用那段的 fi 播放
    const jumpToSrc = (t, quote, play) => {
      const tr = c.transcript || [];
      const idx = srcSegIndex(t, quote, tr);
      if (idx < 0) {
        toast('找不到明確的逐字稿出處');
        return;
      }
      jumpTo(idx, quote);
      if (play && audioParts.length) {
        const seg = tr[idx];
        const near = Math.abs(segSeconds(seg.t) - (Number(t) || 0)) <= 30;
        seekTo(near ? { fi: seg.fi, t: Number(t) || 0 } : seg);
      }
    };
    curJump = jumpToSrc;
    bodyEl.querySelectorAll('.src-chip').forEach((b) => {
      b.onclick = (e) => {
        e.stopPropagation(); // 別觸發條目本身的跳轉或區塊摺疊
        jumpToSrc(Number(b.dataset.srcT), b.dataset.srcQ || '', true);
      };
    });
    bodyEl.querySelectorAll('[data-jump]').forEach((li) => {
      li.onclick = () => {
        const [k, iStr] = li.dataset.jump.split(':');
        const item = (srcMap[k] || [])[+iStr];
        const src = itemSrc(item);
        if (src.length) {
          jumpToSrc(src[0].t, src[0].quote);
          toast(`出處約在 ${fmtSec(src[0].t)}`);
          return;
        }
        const itemText_ = itemText((arrMap[k] || [])[+iStr]);
        const idx = bestSegIndex(itemText_, c.transcript || []);
        if (idx < 0) {
          toast('找不到明確的逐字稿出處');
          return;
        }
        jumpTo(idx, itemText_);
        const seg = (c.transcript || [])[idx];
        if (seg && seg.t != null) toast(`出處約在 ${timeLabel(seg)}`);
      };
    });

    const nt = c.notes || m.notes || {};
    const texts = {
      ai: numbered(actionItems),
      mp: numbered(mainPoints),
      qa: qaText(qa),
      lc: numbered(s.conclusions),
      la: numbered(s.advice),
      lr: numbered(s.risks),
      lk: numbered(s.cases),
      tr: transcriptToText(c.transcript),
      'nt-o': (nt.outline || []).map((o, i) => `${i + 1}. ${o.title}\n${(o.points || []).map((p) => `   - ${itemText(p)}`).join('\n')}`).join('\n'),
      'nt-c': (nt.concepts || []).map((x) => `${x.term}：${x.plain}${x.why ? `（${x.why}）` : ''}`).join('\n'),
      'nt-t': (nt.tables || [])
        .map((t) => [t.title, t.headers.join('\t'), ...t.rows.map((r) => r.join('\t'))].filter(Boolean).join('\n'))
        .join('\n\n'),
      'nt-f': (nt.figures || []).map((f) => `${f.group ? `[${f.group}] ` : ''}${f.label}：${f.value}`).join('\n'),
      'nt-q': (nt.quiz || []).map((x, i) => `Q${i + 1}. ${x.q}\nA. ${x.a}`).join('\n\n'),
    };
    // 「複製全文」：目前畫面（語言＋模式）的各區文字，跟各區「複製」一樣的格式串起來
    copyAllText = () => {
      const parts = [];
      if (mode === 'notes') {
        if (c.notes || m.notes)
          [['nt-o', '📑 章節大綱'], ['nt-c', '💡 重要概念'], ['nt-t', '📊 對照表'], ['nt-f', '🔢 關鍵數據'], ['nt-q', '✍️ 自我測驗']].forEach(([k, label]) =>
            parts.push(`■ ${label}\n${texts[k] || '（無）'}`)
          );
      } else {
        SECS.forEach((sec) => parts.push(`■ ${sec.label}\n${texts[sec.code] || '（無）'}`));
      }
      parts.push(`■ 🗣️ 逐字稿\n${texts.tr || '（無）'}`);
      return `【${m.title}】\n${formatDate(m.createdAt)}\n\n` + parts.join('\n\n');
    };
    bodyEl.querySelectorAll('[data-copy]').forEach((b) => {
      b.onclick = async () => {
        if (exportBlocked()) return; // 複製也是對外交付，待確認清單沒清空前不行
        try {
          await navigator.clipboard.writeText(texts[b.dataset.copy] || '');
          const old = b.textContent;
          b.textContent = '已複製';
          setTimeout(() => (b.textContent = old), 1200);
        } catch (_) {
          alert('複製失敗，請手動選取');
        }
      };
    });

    // 兩版切換／合併／編輯模式／刪除
    bodyEl.querySelectorAll('[data-swap],[data-mergev],[data-edit],[data-del]').forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation(); // 別觸發到區塊標題的摺疊
        const k = b.dataset.swap || b.dataset.mergev || b.dataset.edit;
        if (b.dataset.edit) {
          editSec = editSec === k ? null : k;
          drawBody(lang);
          return;
        }
        if (b.dataset.del) {
          const [sec, iStr] = b.dataset.del.split(':');
          const src = SEC_SRC[sec];
          if (!src) return;
          const shown = (sectionOf(m, src) || [])[+iStr];
          await persist(
            (fresh) => (removeSectionItemMatching(fresh, src[0], src[1], +iStr, shown) ? undefined : STALE),
            { edit: true, content: true }
          );
          drawBody(lang);
          drawReview(); // 存檔時伺服器重新驗證：刪掉的條目，它的待確認項目跟著消失
          return;
        }
        const src = SEC_SRC[k];
        if (!src) return;
        const merge = !!b.dataset.mergev;
        const seen = JSON.stringify(sectionOf(m, src) || []);
        const ok = await persist((fresh) => {
          // 最新版的這一區跟畫面上看到的不同 → 換版／合併的對象已變，不能盲目套用
          if (JSON.stringify(sectionOf(fresh, src) || []) !== seen) return STALE;
          const done = merge ? mergeSectionVersions(fresh, src[0], src[1]) : swapSectionVersion(fresh, src[0], src[1]);
          return done ? undefined : STALE;
        }, { edit: true, content: true });
        drawBody(lang);
        drawReview(); // 換版／合併後伺服器重新驗證，回傳的會議帶著新的待確認清單
        if (ok) toast(merge ? '已合併兩版（重複的已去除）' : '已換版');
      };
    });

    // 寬表格才顯示右緣漸層提示；捲到底就移除
    bodyEl.querySelectorAll('.nt-table-scroll').forEach((el) => {
      const upd = () => el.classList.toggle('more', el.scrollWidth - el.clientWidth - el.scrollLeft > 4);
      el.onscroll = upd;
      upd();
    });

    bodyEl.querySelectorAll('[data-seek]').forEach((el) => {
      el.onclick = (e) => {
        e.stopPropagation();
        const seg = (c.transcript || [])[+el.dataset.seek];
        if (seg) seekTo(seg);
      };
    });

    const gn = document.getElementById('genNotes');
    if (gn) gn.onclick = () => doGenNotes();
    paintDetailTasks(); // 重繪後把仍在跑的作業畫回按鈕
    if (locked()) {
      // 整理期間：換版／合併／編輯／刪除／講者改名全部停用，並在上方提示
      bodyEl.querySelectorAll('[data-swap],[data-mergev],[data-edit],[data-del],.spk-chip').forEach((b) => {
        b.disabled = true;
        b.setAttribute('data-locked', '');
        b.title = LOCK_MSG;
      });
      bodyEl.querySelectorAll('.seg[data-i]').forEach((el) => el.classList.add('locked'));
      if (!sumBody.querySelector('.lock-note')) sumBody.insertAdjacentHTML('afterbegin', lockNote());
    }

    if (editing) {
      // 點段落 → 直接編輯逐字稿文字
      bodyEl.querySelectorAll('.seg[data-i]').forEach((el) => {
        el.onclick = () => {
          if (locked()) { toast(LOCK_MSG); return; }
          if (el.querySelector('textarea')) return; // 已在編輯中
          const i = +el.dataset.i;
          const seg = m.transcript[i];
          if (!seg) return;
          el.innerHTML = `<span class="spk" style="color:${colors[seg.speaker] || 'var(--ink)'}">${esc(seg.speaker)}</span>
            <textarea class="seg-edit" rows="3"></textarea>
            <div class="seg-edit-ops"><button class="act-btn primary seg-save" type="button">儲存</button><button class="act-btn seg-cancel" type="button">取消</button></div>`;
          const ta = el.querySelector('textarea');
          ta.value = seg.text;
          ta.focus();
          el.querySelector('.seg-cancel').onclick = (e) => {
            e.stopPropagation();
            drawBody('orig');
          };
          el.querySelector('.seg-save').onclick = async (e) => {
            e.stopPropagation();
            const nt = ta.value.trim();
            if (nt && nt !== seg.text) {
              const segIdx = i;
              const saved = await persist((fresh) => {
                if (fresh.transcript && fresh.transcript[segIdx]) fresh.transcript[segIdx].text = nt;
                fresh.translations = {}; // 原文改了，清掉舊翻譯
              }, { edit: true, content: true });
              if (saved) toast('已修改 ✓');
            }
            drawBody('orig');
          };
        };
      });
      bodyEl.querySelectorAll('.spk-chip').forEach((chip) => {
        chip.onclick = async () => {
          if (locked()) { toast(LOCK_MSG); return; }
          const cur = chip.dataset.spk;
          const nn = prompt(`把「${cur}」改成：`, cur);
          if (nn && nn.trim() && nn.trim() !== cur) {
            const name = nn.trim();
            await persist((fresh) => {
              (fresh.transcript || []).forEach((seg) => {
                if (seg.speaker === cur) seg.speaker = name;
              });
              fresh.translations = {}; // 原文改了，清掉舊翻譯
            }, { edit: true, content: true });
            renderDetail(id);
          }
        };
      });
    }
  };

  const setLang = async (l) => {
    document.querySelectorAll('#langToggle button').forEach((b) => b.classList.toggle('active', b.dataset.l === l));
    if (l !== 'orig' && !contentFor(l)) {
      lang = l;
      if (detailBusy()) {
        toast('已有一項作業進行中，請等它完成');
        setLang('orig');
        return;
      }
      drawBody(l); // 顯示「翻譯中…」
      try {
        m = await runServerAI(id, 'tr:' + l, '翻譯中…', 'translate', { lang: l }, (msg) => {
          const el = document.getElementById('tprogmsg');
          if (el) el.textContent = msg;
        });
        if (!contentFor(l)) throw new Error('NAS 沒有寫回這個語言的譯文');
        if (lang === l) drawBody(l);
      } catch (e) {
        if (lang !== l) return; // 使用者已切走
        trBody.innerHTML = '';
        sumBody.innerHTML = `<div class="card"><div class="err">❌ 翻譯失敗：${esc(e && e.message ? e.message : e)}</div>
          <button class="big" id="retryTr" style="margin-top:10px">重試翻譯</button>
          <button class="big secondary" id="backZh" style="margin-top:8px">返回原文</button></div>`;
        document.getElementById('retryTr').onclick = () => {
          if (m.translations) delete m.translations[l];
          setLang(l);
        };
        document.getElementById('backZh').onclick = () => setLang('orig');
      }
    } else {
      lang = l;
      drawBody(l);
    }
  };
  document.querySelectorAll('#langToggle button').forEach((b) => (b.onclick = () => setLang(b.dataset.l)));

  document.getElementById('pdfBtn').onclick = async () => {
    if (exportBlocked()) return;
    const opts = await pickExportSections('PDF', meetingMode(m) === 'lecture');
    if (!opts) return;
    // iOS 主畫面 App 無法控制列印檔名 → 改開 Safari 分頁，需要告知使用者接下來怎麼做
    if (exportPdf(viewMeeting(), opts) === 'newtab') toast('已在 Safari 開啟，請用分享鈕 → 列印 → 儲存 PDF');
  };
  document.getElementById('wordBtn').onclick = async () => {
    if (exportBlocked()) return;
    const opts = await pickExportSections('Word', meetingMode(m) === 'lecture');
    if (opts) exportWord(viewMeeting(), opts);
  };

  // ---- 專有名詞訂正（併在「校對」面板裡：掃描／手動新增是次要動作，詞表收在可展開的清單裡）----
  const termsBody = document.getElementById('termsBody');
  let termsListOpen = false;

  // 保護式全文替換：先把已正確的 newV 藏起來，只換掉落單的 oldV，再還原
  // → 避免「台積 → 台積電」把原本就正確的「台積電」變成「台積電電」
  const protectedReplace = (text, oldV, newV) => {
    if (!oldV || oldV === newV) return text;
    const SENT = '';
    return String(text == null ? '' : text).split(newV).join(SENT).split(oldV).join(newV).split(SENT).join(newV);
  };
  // 一筆訂正可能對應多種寫法（同一個名字被聽成好幾種），全部一起取代
  const applyTermAll = (fresh, olds, newV) => {
    for (const o of olds) if (o && o !== newV) applyTerm(fresh, o, newV);
  };
  const applyTerm = (fresh, oldV, newV) => {
    (fresh.transcript || []).forEach((s) => { s.text = protectedReplace(s.text, oldV, newV); });
    const s = fresh.summary || {};
    const fix = fixItem((x) => protectedReplace(x, oldV, newV));
    ALL_SUMMARY_KEYS.forEach((k) => {
      if (Array.isArray(s[k])) s[k] = s[k].map(fix);
    });
    // 學習筆記也要一起訂正，否則逐字稿改對了、複習時看的筆記還是錯字
    if (fresh.notes) applyTermInNotes(fresh.notes, (x) => protectedReplace(x, oldV, newV));
    fresh.translations = {}; // 內容改了 → 清掉舊翻譯
  };

  const TERM_CATS = {
    person: { label: '人名', color: '#0a84ff' },
    org: { label: '公司', color: '#af52de' },
    product: { label: '產品', color: '#ff9500' },
    place: { label: '地名', color: '#34c759' },
    country: { label: '國家', color: '#ff2d55' },
    term: { label: '術語', color: '#5ac8fa' },
  };
  const catMeta = (c) => TERM_CATS[c] || TERM_CATS.term;

  // 目前「文中的實際寫法」：已套用過就是 applied，否則是原詞 t
  const termCurrent = (it) => it.applied || it.t;
  // 待套用：有草稿且草稿 ≠ 目前寫法
  const termPending = (it) => it.draft && it.draft !== termCurrent(it);

  const drawTerms = () => {
    const data = m.terms;
    if (!data || !data.items) {
      termsBody.innerHTML = `<div class="term-actions">
          <button class="act-btn" id="scanTerms" data-task="terms" type="button">🔍 挑出專有名詞</button>
          <button class="act-btn" id="addTerm" type="button">＋ 手動新增</button>
        </div>
        <div class="hint" style="margin-top:0">把人名、公司、產品等聽錯的字改對（逐字稿、摘要、筆記一起改）。</div>`;
      const sb = document.getElementById('scanTerms');
      if (sb) sb.onclick = () => doScanTerms(sb);
      document.getElementById('addTerm').onclick = () => openTermEditor(-1);
      if (locked()) lockTerms();
      return;
    }
    const items = data.items || [];
    const pendingCount = items.filter(termPending).length;
    const rows = items
      .map((it, i) => {
        const meta = catMeta(it.cat);
        const chip = `<span class="term-chip" style="color:${meta.color};border-color:${meta.color}">${meta.label}</span>`;
        if (termPending(it)) {
          return `<div class="term-row pending" data-i="${i}">${chip}<span class="term-pair"><span class="term-old">${esc(termCurrent(it))}</span><span class="term-arrow">→</span><span class="term-new">${esc(it.draft)}</span></span><span class="term-badge">待套用</span></div>`;
        }
        if (it.applied) {
          return `<div class="term-row done" data-i="${i}">${chip}<span class="term-pair"><span class="term-old">${esc(it.t)}</span><span class="term-arrow">→</span><span class="term-new">${esc(it.applied)}</span></span><span class="term-tick">✓</span></div>`;
        }
        const alt = (it.alts || []).length
          ? `<span class="term-alts">／${it.alts.map(esc).join('／')}</span>`
          : '';
        return `<div class="term-row" data-i="${i}">${chip}<span class="term-word">${esc(it.t)}${alt}</span><span class="term-go">訂正 ›</span></div>`;
      })
      .join('');
    // 詞表平常收起來（一長串人名公司名很佔版面）；有待套用的草稿時自動展開
    const listOpen = termsListOpen || pendingCount > 0;
    termsBody.innerHTML = `
      <div class="term-actions">
        <button class="act-btn" id="rescanTerms" data-task="terms" type="button">🔄 重新掃描</button>
        <button class="act-btn" id="addTerm" type="button">＋ 手動新增</button>
      </div>
      ${items.length
        ? `<details class="terms-list-d" id="termsListD"${listOpen ? ' open' : ''}><summary>專有名詞清單（${items.length}）</summary><div class="term-list">${rows}</div></details>`
        : '<div class="hint">這份逐字稿沒有挑到明顯的專有名詞。你可以用「手動新增」自己補。</div>'}
      ${pendingCount ? `<button class="big" id="applyTerms" style="margin-top:12px">✅ 套用全部訂正（${pendingCount}）</button><div class="hint" style="margin-top:6px">按下後才會一次改動逐字稿、會議摘要與學習筆記，並存回 NAS。</div>` : ''}`;
    document.getElementById('rescanTerms').onclick = (e) => doScanTerms(e.target);
    document.getElementById('addTerm').onclick = () => openTermEditor(-1);
    const ld = document.getElementById('termsListD');
    if (ld) ld.ontoggle = () => (termsListOpen = ld.open);
    termsBody.querySelectorAll('.term-row').forEach((r) => {
      r.onclick = () => openTermEditor(+r.dataset.i);
    });
    const ap = document.getElementById('applyTerms');
    if (ap) ap.onclick = () => applyAllTerms();
    if (locked()) lockTerms();
  };
  // 整理期間：套用／手動新增／掃描／逐筆訂正都停用
  const lockTerms = () => {
    termsBody.querySelectorAll('#applyTerms,#addTerm,#rescanTerms,#scanTerms').forEach((b) => {
      b.disabled = true;
      b.setAttribute('data-locked', '');
      b.title = LOCK_MSG;
    });
    termsBody.querySelectorAll('.term-row').forEach((r) => {
      r.onclick = () => toast(LOCK_MSG);
      r.classList.add('locked');
    });
    termsBody.insertAdjacentHTML('afterbegin', lockNote());
  };

  // 開啟訂正輸入（idx=-1 表示手動新增）；只「暫存草稿」，不立即改動全文
  const openTermEditor = (idx) => {
    const items = (m.terms && m.terms.items) || [];
    const it = idx >= 0 ? items[idx] : null;
    // 用「詞本身」當識別，不用陣列位置：同步合併會重建 terms.items，順序可能與畫面不同，
    // 若照索引去改就會改到別筆（甚至因為新值剛好等於那筆的現況而被當成取消，草稿就沒了）。
    const key = it ? it.t : null;
    const curOld = it ? termCurrent(it) : '';
    const prefill = it ? (it.draft || it.fix || termCurrent(it)) : '';
    const row = idx >= 0 ? termsBody.querySelector(`.term-row[data-i="${idx}"]`) : null;
    // 同一個名字常被聽成好幾種寫法 → 預設一起改；但 AI 可能把「同音但其實不同」的誤歸一組，
    // 所以每個變體都給勾選框，取消勾選的會拆成獨立一列自己處理。
    const alts = (it && it.alts) || [];
    const altsHtml = alts.length
      ? `<div class="term-alt-box"><div class="term-alt-t">這些寫法會一起改成上面的正確寫法：</div>` +
        `<label class="term-alt"><input type="checkbox" checked disabled /> ${esc(termCurrent(it))}<span class="term-alt-n">（主要寫法）</span></label>` +
        alts.map((a) => `<label class="term-alt"><input type="checkbox" class="alt-ck" data-alt="${esc(a)}" checked /> ${esc(a)}</label>`).join('') +
        `<div class="hint" style="margin:4px 0 0">若某個寫法其實是<b>別的東西</b>，取消勾選，它會變成獨立一列。</div></div>`
      : '';
    const editorHtml = `<div class="term-editor">
      ${idx < 0 ? '<input class="term-input" id="tOld" placeholder="辨識錯的字（原文中的寫法）" />' : ''}
      <input class="term-input" id="tNew" placeholder="正確的寫法" value="${esc(prefill)}" />
      ${altsHtml}
      <div class="term-edit-ops"><button class="act-btn primary" id="tSave">${idx < 0 ? '加入清單' : '暫存這筆'}</button><button class="act-btn" id="tCancel">取消</button></div>
      <div class="hint" style="margin:2px 0 0">先暫存，最後按「套用全部訂正」才會真正改動全文。</div>
    </div>`;
    if (row) { row.outerHTML = editorHtml; } else {
      const div = document.createElement('div');
      div.innerHTML = editorHtml;
      termsBody.querySelector('.term-list, .term-actions').after(div.firstElementChild);
    }
    const box = termsBody.querySelector('.term-editor');
    const newInp = box.querySelector('#tNew');
    newInp.focus();
    box.querySelector('#tCancel').onclick = () => drawTerms();
    box.querySelector('#tSave').onclick = async () => {
      const oldV = idx >= 0 ? curOld : (box.querySelector('#tOld').value || '').trim();
      const newV = (newInp.value || '').trim();
      if (!oldV) { alert('請填入辨識錯的字'); return; }
      // 暫存草稿（存回 NAS，但不動全文）；設回目前寫法等於取消該筆草稿
      await persist((fresh) => {
        fresh.terms = fresh.terms || { items: [] };
        fresh.terms.items = fresh.terms.items || [];
        const target = key ? fresh.terms.items.find((x) => x && x.t === key) : null;
        if (target) {
          // 被取消勾選的變體：從這一組移出，變成獨立一列（AI 誤歸組時的逃生口）
          if ((target.alts || []).length) {
            const kept = Array.from(box.querySelectorAll('.alt-ck')).filter((c) => c.checked).map((c) => c.dataset.alt);
            const dropped = (target.alts || []).filter((a) => !kept.includes(a));
            target.alts = kept;
            for (const d of dropped) {
              if (!fresh.terms.items.some((x) => x && x.t === d)) {
                fresh.terms.items.push({ t: d, cat: target.cat || 'term', fix: '', alts: [] });
              }
            }
          }
          if (!newV || newV === termCurrent(target)) delete target.draft;
          else target.draft = newV;
        } else if (newV && newV !== oldV) {
          fresh.terms.items.push({ t: oldV, cat: 'term', fix: '', draft: newV });
        }
      });
      drawTerms();
    };
  };

  // 一次套用所有「待套用」的草稿：單次全文替換 + 單次存檔 + 單次重繪
  const applyAllTerms = async () => {
    if (locked()) { toast(LOCK_MSG); return; }
    const pend = ((m.terms && m.terms.items) || []).filter(termPending);
    if (!pend.length) return;
    await persist((fresh) => {
      for (const it of fresh.terms.items) {
        if (it.draft && it.draft !== termCurrent(it)) {
          applyTermAll(fresh, [termCurrent(it), ...(it.alts || [])], it.draft);
          it.applied = it.draft;
          delete it.draft;
        }
      }
    }, { edit: true, content: true });
    lang = 'orig';
    document.querySelectorAll('#langToggle button').forEach((b) => b.classList.toggle('active', b.dataset.l === 'orig'));
    drawBody('orig');
    drawTerms();
    toast(`已套用 ${pend.length} 個訂正，全文都已更新 ✓`);
  };

  const doScanTerms = async (btn) => {
    if (!(m.transcript && m.transcript.length)) { alert('這場沒有逐字稿，無法挑詞'); return; }
    if (detailBusy()) { toast('已有一項作業進行中，請等它完成'); return; }
    // 已套用過的訂正要留著：伺服器重掃若沒保留，就補回去（前面）
    const prevApplied = ((m.terms && m.terms.items) || []).filter((x) => x && x.applied);
    try {
      m = await runServerAI(id, 'terms', '掃描中…', 'terms');
      const items = (m.terms && m.terms.items) || [];
      const lost = prevApplied.filter((x) => !items.some((y) => y && y.t === x.t && y.applied));
      if (lost.length) {
        await persist((f) => {
          f.terms = f.terms || { items: [] };
          const cur = (f.terms.items || []).filter((y) => !lost.some((x) => x.t === y.t));
          f.terms.items = lost.concat(cur);
        });
      }
      drawTerms();
      const n = ((m.terms && m.terms.items) || []).filter((x) => x && !x.applied).length;
      toast(n ? `挑出 ${n} 個待訂正的詞` : '沒有挑到需要訂正的詞');
    } catch (e) {
      alert('挑詞失敗：' + errText(e));
    }
  };
  drawTerms();

  // ---- 自動名詞校正（伺服器在摘要前已套用的高信心校正）＋ 還原原始逐字稿 ----
  // 只顯示一行「已自動校正 N 個詞」，點「查看」才列出（不再一張張卡片）；還原原始逐字稿也收在裡面
  const drawCorr = () => {
    const box = document.getElementById('corrBody');
    const items = ((m.corrections && m.corrections.items) || []).filter((x) => x && x.wrong);
    const applied = items.filter((x) => x.state === 'applied');
    if (!applied.length && !m.transcriptRaw) {
      box.innerHTML = '';
      return;
    }
    box.innerHTML = `<details class="corr-line" id="corrLine">
      <summary>🤖 ${applied.length ? `已自動校正 ${applied.length} 個詞` : '自動校正'}<span class="corr-more">查看</span></summary>
      ${applied.length
        ? `<div class="corr-list">${applied
            .map((x) => `<span class="corr-pair">${esc(x.wrong)} → ${esc(x.right)}${x.count != null ? `<span class="corr-n">（${esc(x.count)} 處）</span>` : ''}</span>`)
            .join('')}</div>`
        : '<div class="hint" style="margin-top:4px">沒有自動套用的校正。</div>'}
      ${m.transcriptRaw ? '<button class="act-btn" id="restoreRaw" data-task="restoreRaw" type="button" style="margin-top:8px">↩ 還原原始逐字稿</button>' : ''}
    </details>`;
    const rb = document.getElementById('restoreRaw');
    if (rb) rb.onclick = () => doRestoreRaw();
  };

  // 等伺服器重新整理完 → 重繪整頁（摘要、清單都可能變）
  const runAndReload = async (key, label, action, params, doneMsg) => {
    if (detailBusy()) {
      toast('已有一項作業進行中，請等它完成');
      return;
    }
    try {
      const fresh = await runServerAI(id, key, label, action, params);
      if (currentDetailId() === id) renderDetail(id, fresh);
      if (doneMsg) toast(doneMsg);
    } catch (e) {
      alert(label.replace(/中…$/, '') + '失敗：' + errText(e));
    }
  };
  const doRestoreRaw = () => {
    if (!confirm('還原成轉錄器產出的原始逐字稿？自動校正會全部取消（改回待確認），並重新整理摘要。手動改過的逐字稿也會被蓋掉。')) return;
    runAndReload('restoreRaw', '還原中…', () => api.restoreRaw(id), null, '已還原原始逐字稿並重新整理 ✓');
  };
  const doReprocess = () => {
    if (!(m.transcript && m.transcript.length)) {
      alert('這場沒有逐字稿，無法重新整理');
      return;
    }
    if (!confirm('用目前的逐字稿重新整理？會依序做：名詞校正 → 判斷講座／會議 → 整理摘要（有筆記也會重做）→ 驗證，舊版會留著可以換回來。不會重新轉錄。')) return;
    runAndReload('reprocess', '重新整理中…', 'reprocess', {}, '重新整理完成 ✓');
  };
  // 加強校對：逐段重新掃描整份逐字稿的聽錯字，只新增待確認項目（摘要／筆記不重做、已確認的不動）
  const doDeepCorrect = async () => {
    if (!(m.transcript && m.transcript.length)) {
      alert('這場沒有逐字稿，無法加強校對');
      return;
    }
    if (detailBusy()) {
      toast('已有一項作業進行中，請等它完成');
      return;
    }
    if (!confirm(DEEP_CORRECT_CONFIRM)) return;
    const before = reviewRows(m).length + legacyPending(m).length;
    try {
      const fresh = await runServerAI(id, 'deepCorrect', '加強校對中…', 'deep_correct', {});
      if (currentDetailId() === id) renderDetail(id, fresh);
      const added = reviewRows(fresh).length + legacyPending(fresh).length - before;
      toast(added > 0 ? `加強校對完成：新增 ${added} 項待確認 ✓` : '加強校對完成：沒有找到新的錯字 ✓');
    } catch (e) {
      alert('加強校對失敗：' + errText(e));
    }
  };
  const doSetKind = (kind) => {
    if (kind === meetingMode(m)) return;
    const name = kind === 'lecture' ? '講座' : '會議';
    if (!confirm(`改成「${name}」模式並重新整理摘要？（${kind === 'lecture' ? '核心結論／講者建議／風險提醒／案例／重點／Q&A' : '待辦／重點／Q&A'}；舊版會留著）`)) return;
    runAndReload('kind:' + kind, `改成${name}中…`, () => api.setMeetingMode(id, kind), null, `已改成${name}模式 ✓`);
  };

  // ---- ⚠️ 待確認清單：程式驗證找不到依據的條目＋沒把握的名詞校正；清空前不能匯出 ----
  const reviewBox = document.getElementById('reviewCard');
  const paintExportLock = () => {
    const n = pendingReview(m).length;
    const lock = document.getElementById('exportLock');
    ['shareBtn', 'pdfBtn', 'wordBtn'].forEach((bid) => {
      const b = document.getElementById(bid);
      if (!b) return;
      b.classList.toggle('locked', n > 0);
      if (n > 0) {
        b.setAttribute('aria-disabled', 'true');
        b.title = exportLockText(n);
      } else {
        b.removeAttribute('aria-disabled');
        b.removeAttribute('title');
      }
    });
    const copyAll = document.getElementById('copyAllBtn');
    if (copyAll) {
      copyAll.classList.toggle('locked', n > 0);
      if (n > 0) copyAll.setAttribute('aria-disabled', 'true');
      else copyAll.removeAttribute('aria-disabled');
    }
    lock.hidden = !n;
    lock.textContent = n ? `🔒 ${exportLockText(n)}` : '';
  };
  // 「校對」按鈕：有待確認就顯示項數；沒有就「已校對」（淡色，但還是可以點開重新掃描／手動新增）
  const reviewBtn = document.getElementById('reviewBtn');
  const reviewPanel = document.getElementById('reviewPanel');
  const reviewTotal = () => reviewRows(m).length + legacyPending(m).length;
  const paintReviewBtn = () => {
    const n = reviewTotal();
    reviewBtn.textContent = n ? `✅ 校對（${n} 項）` : '✅ 已校對';
    reviewBtn.classList.toggle('done', !n);
    reviewBtn.classList.toggle('attn', n > 0);
    reviewBtn.classList.toggle('on', detailUi.panel);
    reviewBtn.setAttribute('aria-expanded', String(detailUi.panel));
    reviewPanel.hidden = !detailUi.panel;
  };
  reviewBtn.onclick = () => {
    detailUi.panel = !detailUi.panel;
    if (detailUi.panel) drawReview();
    else paintReviewBtn();
  };
  // 待確認項目的時間鈕要找哪一句：優先用它指向的條目在該秒數附近的出處原句，否則用項目文字比對
  const reviewQuote = (r, t) => {
    if (!r) return '';
    if (isWordReview(r)) return r.span;
    const [box, key] = String(r.section || '').split('.');
    const arr = ((m[box] || {})[key]) || [];
    const at = r.itemId ? listItemIds(arr).indexOf(String(r.itemId)) : -1;
    const target = at >= 0 ? arr[at] : null;
    const src = itemSrc(target).sort((a, b) => Math.abs(a.t - t) - Math.abs(b.t - t))[0];
    return (src && src.quote) || r.text || '';
  };
  const unchecked = new Set(); // 勾選清單裡被取消勾選的列（= 保留原文）
  const drawReviewInner = () => {
    const all = reviewItems(m).filter((r) => r && r.state !== 'merged' && r.state !== 'stale'); // 已併進別張卡／原文已不存在的不顯示、不計數
    paintExportLock();
    paintReviewBtn();
    if (!all.length) {
      reviewBox.hidden = true;
      reviewBox.innerHTML = '';
      return;
    }
    reviewBox.hidden = false;
    const pend = pendingReview(m);
    if (!pend.length) {
      reviewBox.className = 'review-box ok';
      reviewBox.innerHTML = `<div class="review-head">✅ 待確認：全部確認完成</div>`;
      return;
    }
    reviewBox.className = 'review-box';
    const rows = reviewRows(m);
    const legacy = legacyPending(m);
    const total = rows.length + legacy.length;
    const rowIds = new Set(rows.map((x) => x.id));
    [...unchecked].forEach((x) => !rowIds.has(x) && unchecked.delete(x));
    const legacyHtml = legacy
      .map(
        (r) => `<div class="review-row" data-rid="${esc(r.id)}">
          <div class="review-top"><span class="review-kind k-${esc(r.kind)}">${esc(REVIEW_KIND[r.kind] || r.kind || '待確認')}</span>${r.t != null && isFinite(Number(r.t)) ? `<button class="src-chip" type="button" data-rt="${Number(r.t)}">▶ ${esc(fmtSec(r.t))}</button>` : ''}</div>
          <div class="review-text">${esc(r.text)}</div>
          ${r.detail ? `<div class="review-detail">${esc(r.detail)}</div>` : ''}
          ${r.suggestion ? `<div class="review-sugg">建議：${esc(r.suggestion)}</div>` : ''}
          <div class="review-ops">${
            confirmOnly(r)
              ? `<button class="act-btn" type="button" data-rv="confirm">✅ 知道了</button>`
              : `<button class="act-btn" type="button" data-rv="confirm">✅ 確認</button>
            <button class="act-btn" type="button" data-rv="edit">✏️ 改正</button>
            <button class="act-btn" type="button" data-rv="delete">🗑 刪除</button>`
          }</div>
        </div>`
      )
      .join('');
    const list = rows.length
      ? `<div class="hint review-hint">AI 已勾選建議，沒問題直接按確認；取消勾選 = 保留原文</div>
        <div class="wl-list">${rows.map((row) => wordRowHtml(row, !unchecked.has(row.id))).join('')}</div>
        <div class="wl-bar"><button class="act-btn primary wl-confirm" id="wlConfirm" type="button"${locked() ? ` data-locked disabled title="${LOCK_MSG}"` : ''}></button></div>`
      : '';
    const legacyHint = legacy.length
      ? `<div class="hint" style="margin:${rows.length ? '10px' : '0'} 0 6px">程式在逐字稿裡找不到依據的內容。逐一確認、改正或刪除；清空前不能分享／匯出。</div>`
      : '';
    reviewBox.innerHTML = `<div class="review-head">⚠️ 待確認（<span id="reviewCount">${total}</span> 項）</div>
      <div class="review-list">
        ${list}
        ${legacyHint}
        ${legacyHtml}
      </div>`;
    const confirmBtn = document.getElementById('wlConfirm');
    const paintConfirm = () => {
      if (!confirmBtn) return;
      const keep = rows.filter((x) => unchecked.has(x.id)).length;
      confirmBtn.textContent = `✅ 確認（套用 ${rows.length - keep} 項，保留 ${keep} 項）`;
    };
    paintConfirm();
    reviewBox.querySelectorAll('.wl-row').forEach((el) => {
      const row = rows.find((x) => x.id === el.dataset.row);
      el.querySelector('.wl-check').onchange = (e) => {
        if (e.target.checked) unchecked.delete(row.id);
        else unchecked.add(row.id);
        paintConfirm();
      };
      const more = el.querySelector('.wl-more');
      more.onclick = () => {
        const d = el.querySelector('.wl-detail');
        d.hidden = !d.hidden;
        more.textContent = d.hidden ? '▸' : '▾';
        more.setAttribute('aria-expanded', String(!d.hidden));
      };
      const inp = el.querySelector('.word-input');
      const save = () => {
        const v = inp.value.trim();
        if (!v) {
          alert('請填入要改成的文字');
          return;
        }
        sendRowEdit(row, v);
      };
      el.querySelector('.wl-edit-btn').onclick = save;
      inp.onkeydown = (e) => {
        if (e.key === 'Enter') save();
      };
    });
    if (confirmBtn)
      confirmBtn.onclick = () => {
        if (reviewSending) return;
        const accept = [];
        const keep = [];
        rows.forEach((row) => row.cards.forEach((c) => (unchecked.has(row.id) ? keep : accept).push(c.id)));
        sendBatch(accept, keep, rows.length - rows.filter((x) => unchecked.has(x.id)).length);
      };
    reviewBox.querySelectorAll('[data-rt]').forEach((b) => {
      b.onclick = () => {
        const t = Number(b.dataset.rt);
        const r = pend.find((x) => String(x.id) === b.closest('[data-rid]').dataset.rid);
        if (curJump) curJump(t, reviewQuote(r, t), true);
      };
    });
    reviewBox.querySelectorAll('[data-rv]').forEach((b) => {
      b.onclick = () => {
        const row = b.closest('.review-row');
        const r = pend.find((x) => String(x.id) === row.dataset.rid);
        if (!r) return;
        const act = b.dataset.rv;
        if (act === 'edit') return openReviewEdit(row, r);
        if (act === 'delete' && !confirm(`刪除這一條？\n\n${r.text}\n\n（會移到備份區，不會真的消失）`)) return;
        sendReview(r, act);
      };
    });
  };
  // 有 AI 作業在跑時，待確認清單的按鈕也要停用（作業結束會重寫條目，這時改會打架）
  const drawReview = () => {
    drawReviewInner();
    paintDetailTasks();
  };
  const openReviewEdit = (row, r) => {
    const ops = row.querySelector('.review-ops');
    ops.outerHTML = `<div class="review-edit"><textarea class="seg-edit" rows="3"></textarea>
      <div class="seg-edit-ops"><button class="act-btn primary" type="button" data-re="save">儲存改正</button><button class="act-btn" type="button" data-re="cancel">取消</button></div></div>`;
    const ta = row.querySelector('textarea');
    ta.value = r.suggestion || r.text || '';
    ta.focus();
    row.querySelector('[data-re="cancel"]').onclick = () => drawReview();
    row.querySelector('[data-re="save"]').onclick = () => {
      const v = ta.value.trim();
      if (!v) {
        alert('請填入改正後的文字');
        return;
      }
      sendReview(r, 'edit', v);
    };
  };
  let reviewSending = false;
  // 重抓最新的會議重畫（404／409 時）
  const refreshAfter = async (e, msg409, msg404) => {
    noteFetchError(e);
    if (e && (e.status === 404 || e.status === 409)) {
      try {
        const fresh = await api.getMeeting(id);
        await cacheMeeting(fresh);
        m = fresh;
        toast(e.status === 409 ? msg409 : msg404);
        if (currentDetailId() === id) renderDetail(id, fresh);
        return true;
      } catch (e2) {
        noteFetchError(e2);
      }
    }
    return false;
  };
  // 勾選清單的「確認」：勾選的照建議、沒勾的保留原文，一次送出
  const sendBatch = async (accept, keep, nRows) => {
    if (reviewSending) return;
    reviewSending = true;
    reviewBox.querySelectorAll('button, input').forEach((x) => (x.disabled = true));
    try {
      const r2 = await api.applyReviewBatch(id, accept, keep);
      const applied = (r2 && typeof r2 === 'object' && r2.applied) || {};
      if (r2 && r2.applied) delete r2.applied;
      const fresh = r2 && typeof r2 === 'object' && r2.id ? r2 : await api.getMeeting(id);
      await cacheMeeting(fresh);
      m = fresh;
      unchecked.clear();
      // 確認完：收起校對面板，直接帶到逐字稿
      detailUi.panel = false;
      detailUi.toTranscript = true;
      toast(`已套用 ${nRows} 項：逐字稿 ${Number(applied.transcript) || 0} 處、摘要 ${Number(applied.items) || 0} 條`);
      if (currentDetailId() === id) renderDetail(id, fresh);
    } catch (e) {
      if (await refreshAfter(e, '整理中，請稍後再確認', '內容已更新，已重新整理清單')) return;
      alert('處理失敗：' + errText(e));
      drawReview();
    } finally {
      reviewSending = false;
    }
  };
  // 一列「自己改」：這一列的每張卡都改成使用者的字（span 裡的錯字換成輸入；刪除列＝整段換成輸入）
  const sendRowEdit = async (row, value) => {
    if (reviewSending) return;
    reviewSending = true;
    reviewBox.querySelectorAll('button, input').forEach((x) => (x.disabled = true));
    const sum = { transcript: 0, items: 0 };
    try {
      let r2 = null;
      for (const c of row.cards) {
        const text = row.action === 'replace' && !row.from.endsWith('…') && c.span.includes(row.from) ? c.span.replace(row.from, value) : value;
        r2 = await api.reviewAction(id, c.id, 'edit', text);
        const a = (r2 && typeof r2 === 'object' && r2.applied) || {};
        sum.transcript += Number(a.transcript) || 0;
        sum.items += Number(a.items) || 0;
        if (r2 && r2.applied) delete r2.applied;
      }
      const fresh = r2 && typeof r2 === 'object' && r2.id ? r2 : await api.getMeeting(id);
      await cacheMeeting(fresh);
      m = fresh;
      toast(scopeText(sum, '已改：'));
      if (currentDetailId() === id) renderDetail(id, fresh);
    } catch (e) {
      if (await refreshAfter(e, '整理中，請稍後再確認', '這一項已經處理過或內容已更新，已重新整理清單')) return;
      alert('處理失敗：' + errText(e));
      drawReview();
    } finally {
      reviewSending = false;
    }
  };
  const sendReview = async (r, action, text) => {
    if (reviewSending) return;
    reviewSending = true;
    reviewBox.querySelectorAll('button').forEach((x) => (x.disabled = true));
    try {
      const r2 = await api.reviewAction(id, r.id, action, text);
      const applied = r2 && typeof r2 === 'object' ? r2.applied : null;
      if (applied) delete r2.applied; // 只是這次套用的統計，不存進會議
      // 伺服器回傳更新後的會議；沒有的話重抓
      const fresh = r2 && typeof r2 === 'object' && r2.id ? r2 : await api.getMeeting(id);
      await cacheMeeting(fresh);
      m = fresh;
      if (isWordReview(r))
        toast(action === 'keep' ? '已保留原文 ✓' : action === 'accept' && r.action === 'keep' ? '已確認 ✓' : scopeText(applied || {}, '已改：'));
      else toast(action === 'confirm' ? '已確認 ✓' : action === 'edit' ? '已改正 ✓' : '已刪除 ✓');
      // 條目內容可能變了（改正／刪除）→ 整頁重繪
      if (currentDetailId() === id) renderDetail(id, fresh);
    } catch (e) {
      noteFetchError(e);
      if (e && (e.status === 404 || e.status === 409)) {
        // 404：那一條已不存在（別台裝置、重新驗證後已結案）；409：伺服器正在整理 → 換成最新版重畫
        try {
          const fresh = await api.getMeeting(id);
          await cacheMeeting(fresh);
          m = fresh;
          toast(e.status === 409 ? '整理中，請稍後再確認' : '這一項已經處理過或內容已更新，已重新整理清單');
          if (currentDetailId() === id) renderDetail(id, fresh);
          return;
        } catch (e2) {
          noteFetchError(e2);
        }
      }
      alert('處理失敗：' + errText(e));
      drawReview();
    } finally {
      reviewSending = false;
    }
  };
  drawReview();
  drawCorr();
  document.querySelectorAll('#gapsLine [data-gap-t]').forEach((b) => {
    b.onclick = () => {
      if (curJump) curJump(Number(b.dataset.gapT), '', true);
    };
  });

  // 加強某一區塊（分段掃整份逐字稿抓完整清單）
  const doEnhance = async (section, btn) => {
    if (!(m.transcript && m.transcript.length)) {
      alert('這場沒有逐字稿，無法加強');
      return;
    }
    const name = (enhSecs.find((x) => x.k === section) || {}).name || section;
    if (detailBusy()) {
      toast('已有一項作業進行中，請等它完成');
      return;
    }
    if (!confirm(`重新從整份逐字稿抓出「完整的${name}」？會取代目前這一區的內容（其他區不變，舊版會留著可以換回來）。`)) return;
    try {
      const before = ((m.summary || {})[section] || []).length;
      // 伺服器會先把目前版備份到 alt，再寫入新版 → 之後可用「換版／合併」
      const fresh = await runServerAI(id, 'enh:' + section, `加強${name}中…`, 'enhance', { section });
      renderDetail(id, fresh);
      const after = ((fresh.summary || {})[section] || []).length;
      toast(`已加強${name}：${before} 筆 → ${after} 筆`);
    } catch (e) {
      alert('加強失敗：' + errText(e));
    }
  };
  // 全部加強：依序加強 結論（或待辦）→ 重點 → Q&A；每項等工作完成才送下一項，失敗就停
  const doEnhanceAll = async () => {
    if (!(m.transcript && m.transcript.length)) {
      alert('這場沒有逐字稿，無法加強');
      return;
    }
    if (detailBusy()) {
      toast('已有一項作業進行中，請等它完成');
      return;
    }
    if (!confirm(`會依序加強${enhSecs.map((x) => x.short).join('、')}，約需數分鐘，舊版會保留可換版。確定？`)) return;
    let fresh = null;
    let done = 0;
    try {
      for (const [i, x] of enhSecs.entries()) {
        const pre = `全部加強 ${i + 1}/${enhSecs.length}…`;
        fresh = await runServerAI(id, 'enhAll', pre, 'enhance', { section: x.k }, null, pre);
        done++;
      }
    } catch (e) {
      if (fresh) renderDetail(id, fresh);
      alert(`全部加強在第 ${done + 1} 項（${enhSecs[done].name}）失敗，已停止：${errText(e)}${done ? `\n前 ${done} 項已完成。` : ''}`);
      return;
    }
    renderDetail(id, fresh);
    toast('全部加強完成 ✓');
  };
  const enhAllBtn = document.getElementById('enhAllBtn');
  if (enhAllBtn) enhAllBtn.onclick = () => doEnhanceAll();
  function wireEnhButtons() {
    document.querySelectorAll('[data-enh]').forEach((b) => (b.onclick = () => doEnhance(b.dataset.enh, b)));
    document.querySelectorAll('[data-nenh]').forEach((b) => (b.onclick = () => doEnhanceNotes(b.dataset.nenh)));
    document.querySelectorAll('[data-gen-notes]').forEach((b) => (b.onclick = () => doGenNotes()));
  }
  wireEnhButtons();

  // ---- 工具列：AI 處理／檢視 ----
  const editModeBtn = document.getElementById('editModeBtn');
  const paintEditMode = () => {
    editModeBtn.classList.toggle('on', detailUi.edit);
    editModeBtn.setAttribute('aria-pressed', String(detailUi.edit));
    editModeBtn.textContent = detailUi.edit ? '✏️ 編輯中（點此結束）' : '✏️ 編輯模式';
  };
  paintEditMode();
  editModeBtn.onclick = () => {
    detailUi.edit = !detailUi.edit;
    editSec = null;
    paintEditMode();
    drawBody(lang);
  };
  document.querySelectorAll('#toolbar [data-kind]').forEach((b) => (b.onclick = () => doSetKind(b.dataset.kind)));
  const rp = document.getElementById('reprocessBtn');
  if (rp) rp.onclick = () => doReprocess();
  const dc = document.getElementById('deepCorrectBtn');
  if (dc) dc.onclick = () => doDeepCorrect();
  document.getElementById('copyAllBtn').onclick = async () => {
    if (exportBlocked()) return; // 跟分享／匯出一樣：待確認沒清空前不能複製
    try {
      await navigator.clipboard.writeText(copyAllText());
      toast('已複製全文 ✓');
    } catch (_) {
      alert('複製失敗，請手動選取');
    }
  };

  document.getElementById('shareBtn').onclick = async () => {
    if (exportBlocked()) return;
    const v = viewMeeting();
    const s = v.summary || {};
    const num = (arr) => (arr || []).map((x, i) => `${i + 1}. ${itemText(x)}`).join('\n');
    const head = `【${v.title}】\n${formatDate(v.createdAt)}\n\n`;
    const text =
      meetingMode(v) === 'lecture'
        ? head +
          summarySections(v)
            .filter((sec) => sec.k !== 'qa' && sec.k !== 'cases')
            .map((sec) => `■ ${sec.short}\n${num(s[sec.k]) || '（無）'}`)
            .join('\n\n')
        : head +
          `■ 待辦事項\n${num(s.actionItems) || '（無）'}\n\n` +
          `■ 會議重點\n${num(s.mainPoints || s.keyPoints) || '（無）'}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: v.title, text });
      } catch (_) {}
    } else {
      try {
        await navigator.clipboard.writeText(text);
        toast('已複製到剪貼簿，可貼給同事');
      } catch (_) {
        alert(text);
      }
    }
  };

  // 問答：把逐字稿+問題丟給品質模型回答，紀錄存在這場會議
  const chatLogEl = document.getElementById('chatLog');
  const chatClearBtn = document.getElementById('chatClear');
  const fmtAnswer = (a) =>
    esc(a)
      .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>') // AI 偶爾用 **粗體**，正確顯示
      .replace(/\n/g, '<br>');
  const drawChat = () => {
    const items = m.chat || [];
    chatLogEl.innerHTML = items
      .map(
        (c, i) =>
          `<div class="chat-q">🙋 ${esc(c.q)}</div>
           <div class="chat-a">${fmtAnswer(c.a)}<div class="chat-ops"><button class="copy chat-copy" data-ci="${i}">複製回答</button></div></div>`
      )
      .join('');
    chatClearBtn.hidden = !items.length;
    chatLogEl.querySelectorAll('.chat-copy').forEach((b) => {
      b.onclick = async () => {
        const it = (m.chat || [])[+b.dataset.ci];
        if (!it) return;
        try {
          await navigator.clipboard.writeText(it.a);
          b.textContent = '已複製 ✓';
          setTimeout(() => (b.textContent = '複製回答'), 1200);
        } catch (_) {
          alert('複製失敗，請長按文字手動選取');
        }
      };
    });
  };
  drawChat();
  chatClearBtn.onclick = async () => {
    if (!confirm('清除這場會議的所有問答紀錄？')) return;
    await persist((fresh) => {
      fresh.chat = [];
    });
    drawChat();
  };
  document.getElementById('chatAsk').onclick = async () => {
    const inp = document.getElementById('chatInput');
    const q = inp.value.trim();
    if (!q) return;
    if (!(m.transcript && m.transcript.length)) {
      alert('這場沒有逐字稿，無法問答');
      return;
    }
    if (detailBusy()) {
      toast('已有一項作業進行中，請等它完成');
      return;
    }
    try {
      m = await runServerAI(id, 'ask', '思考中…', 'ask', { question: q });
      inp.value = '';
      drawChat();
      chatLogEl.lastElementChild && chatLogEl.lastElementChild.scrollIntoView({ block: 'nearest' });
    } catch (e) {
      alert('問答失敗：' + errText(e));
    }
  };

  document.getElementById('titleInput').onchange = async (e) => {
    const nt = e.target.value.trim();
    await persist((fresh) => {
      fresh.title = nt || fresh.title;
    }, { edit: true });
  };
  document.getElementById('del').onclick = async () => {
    if (!confirm('確定刪除這場會議記錄？（會移到 NAS 的垃圾桶，需要時可以找回）')) return;
    try {
      await api.deleteMeeting(id);
      await uncacheMeeting(id);
      location.hash = '#/';
    } catch (e) {
      alert('刪除失敗：' + errText(e));
    }
  };

  // 產生學習筆記（按需，不自動跑）
  const doGenNotes = async () => {
    if (!(m.transcript && m.transcript.length)) {
      alert('這場沒有逐字稿，無法產生學習筆記');
      return;
    }
    if (detailBusy()) {
      toast('已有一項作業進行中，請等它完成');
      return;
    }
    if (m.notes && !confirm('重新產生學習筆記？會取代目前這份。')) return;
    try {
      const fresh = await runServerAI(id, 'notes', '整理學習筆記中…', 'notes');
      renderDetail(id, fresh);
      const n = fresh.notes || {};
      toast(`學習筆記完成（${(n.outline || []).length} 節、${(n.concepts || []).length} 個概念、${(n.tables || []).length} 張表）`);
    } catch (e) {
      alert('產生學習筆記失敗：' + errText(e));
    }
  };

  // 加強學習筆記的某一區（伺服器分批掃過整份逐字稿，補上一次生成漏掉的；舊版備份到 alt）
  const doEnhanceNotes = async (section) => {
    const nameMap = { outline: '章節大綱', concepts: '重要概念', tables: '對照表', figures: '關鍵數據', quiz: '自我測驗' };
    if (!(m.transcript && m.transcript.length)) {
      alert('這場沒有逐字稿，無法加強');
      return;
    }
    if (detailBusy()) {
      toast('已有一項作業進行中，請等它完成');
      return;
    }
    if (!confirm(`重新從整份逐字稿抓出「完整的${nameMap[section]}」？會取代目前這一區（其他區不變，舊版會留著可以換回來）。`)) return;
    try {
      const before = ((m.notes || {})[section] || []).length;
      const fresh = await runServerAI(id, 'nenh:' + section, `加強${nameMap[section]}中…`, 'notes_enhance', { section });
      renderDetail(id, fresh);
      // 標出加強前後的筆數：沒有變化時使用者才知道是「本來就抓完了」而不是按鈕沒作用
      toast(`已加強${nameMap[section]}：${before} 筆 → ${((fresh.notes || {})[section] || []).length} 筆`);
    } catch (e) {
      alert('加強失敗：' + errText(e));
    }
  };

  // 模式切換：分頁鈕 ＋ 內容區內左右滑動（不含左右邊緣，避免與 iOS 返回手勢打架）
  document.querySelectorAll('#modeToggle button').forEach((b) => {
    b.onclick = () => setMode(b.dataset.mode);
  });
  let sx = 0;
  let sy = 0;
  // 起點若落在可橫向捲動的元素內（例如寬表格），這一次滑動屬於那個元素，不能拿來切換模式
  const inScrollableX = (el) => {
    for (let n = el; n && n !== bodyEl; n = n.parentElement) {
      if (n.scrollWidth > n.clientWidth + 1) {
        const ox = getComputedStyle(n).overflowX;
        if (ox === 'auto' || ox === 'scroll') return true;
      }
    }
    return false;
  };
  bodyEl.addEventListener('touchstart', (e) => {
    const t = e.touches && e.touches[0];
    if (!t) return;
    // -1 → 這次不判定為切換手勢（可橫捲的表格、錄音播放器上的拖曳不算）
    sx = inScrollableX(e.target) || (e.target.closest && e.target.closest('#audioCard')) ? -1 : t.clientX;
    sy = t.clientY;
  }, { passive: true });
  bodyEl.addEventListener('touchend', (e) => {
    const t = e.changedTouches && e.changedTouches[0];
    if (!t || sx < 40 || sx > window.innerWidth - 40) return; // 起點在邊緣或可橫捲區 → 不判定為切換手勢
    const dx = t.clientX - sx;
    const dy = t.clientY - sy;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return; // 要夠橫、夠長才算滑動
    setMode(dx < 0 ? 'notes' : 'meeting');
  }, { passive: true });

  setMode(mode, true); // 套用記住的模式（同時會畫出內容）

  // ===== 逐字稿快速捲動軸：右緣窄軸＋可拖曳 thumb＋「⤒ ⤓」兩顆圓鈕；只在逐字稿區塊在畫面上時出現 =====
  view.insertAdjacentHTML(
    'beforeend',
    `<div class="tr-rail" id="trRail" hidden><button type="button" class="rail-btn" id="trTopBtn" aria-label="捲到逐字稿頂端">⤒</button><button type="button" class="rail-btn" id="trBotBtn" aria-label="捲到逐字稿底端">⤓</button><div class="rail-track" id="railTrack"><div class="rail-thumb" id="railThumb" role="slider" aria-label="逐字稿位置"><span class="rail-bubble" id="railBubble" hidden></span></div></div></div>`
  );
  {
    const rail = document.getElementById('trRail');
    const track = document.getElementById('railTrack');
    const thumb = document.getElementById('railThumb');
    const bubble = document.getElementById('railBubble');
    // 捲動的是 #view（App 外殼版面，整頁不捲）；座標都換算成 #view 內容座標
    const range = () => {
      const r = trBody.getBoundingClientRect();
      const base = view.getBoundingClientRect().top - view.scrollTop;
      return { top: r.top - base - 8, bottom: r.bottom - base };
    };
    const vh = () => view.clientHeight || window.innerHeight || 700;
    const scrollView = (y, smooth) => {
      if (view.scrollTo) view.scrollTo(smooth ? { top: y, behavior: 'smooth' } : { top: y });
      else view.scrollTop = y;
    };
    let dragging = false;
    const placeThumb = (frac) => {
      const room = Math.max(0, track.clientHeight - thumb.offsetHeight);
      thumb.style.transform = `translateY(${Math.round(frac * room)}px)`;
    };
    const syncFromScroll = () => {
      if (!rail.isConnected) return cleanupRail();
      if (dragging) return;
      const { top, bottom } = range();
      placeThumb(railFrac(view.scrollTop || 0, top, bottom, vh()));
    };
    const cleanupRail = () => view.removeEventListener('scroll', syncFromScroll);
    view.addEventListener('scroll', syncFromScroll, { passive: true });
    detailWidgets.push(cleanupRail, () => rail.remove());
    document.getElementById('trTopBtn').onclick = () => scrollView(Math.max(0, range().top), true);
    document.getElementById('trBotBtn').onclick = () => scrollView(Math.max(0, range().bottom - vh()), true);
    const dragTo = (clientY) => {
      const tr = track.getBoundingClientRect();
      const room = Math.max(1, tr.height - thumb.offsetHeight);
      const frac = Math.min(1, Math.max(0, (clientY - tr.top - thumb.offsetHeight / 2) / room));
      const { top, bottom } = range();
      scrollView(railMap(frac, top, bottom, vh()), false);
      placeThumb(frac);
      const n = railSegs.length;
      const seg = n ? railSegs[Math.min(n - 1, Math.round(frac * (n - 1)))] : null;
      bubble.textContent = seg ? timeLabel(seg) || `${Math.round(frac * 100)}%` : `${Math.round(frac * 100)}%`;
    };
    thumb.addEventListener('pointerdown', (e) => {
      dragging = true;
      thumb.classList.add('drag');
      bubble.hidden = false;
      try {
        thumb.setPointerCapture(e.pointerId);
      } catch (_) {}
      e.preventDefault();
      dragTo(e.clientY);
    });
    thumb.addEventListener('pointermove', (e) => dragging && dragTo(e.clientY));
    const endDrag = () => {
      dragging = false;
      thumb.classList.remove('drag');
      bubble.hidden = true;
    };
    thumb.addEventListener('pointerup', endDrag);
    thumb.addEventListener('pointercancel', endDrag);
    if (typeof IntersectionObserver === 'function') {
      rail.hidden = true;
      const io2 = new IntersectionObserver((es) => {
        es.forEach((e) => (rail.hidden = !e.isIntersecting));
        if (!rail.hidden) syncFromScroll();
      });
      io2.observe(trBody);
      detailWidgets.push(() => io2.disconnect());
    } else rail.hidden = false;
  }
  // 重繪後把仍在跑的作業狀態畫回按鈕（離開再回來時才看得出它還在跑）
  paintDetailTasks();
  // 校對確認後：直接帶到逐字稿（展開、捲過去）
  if (detailUi.toTranscript) {
    detailUi.toTranscript = false;
    const trHead = bodyEl.querySelector('.sec-head[data-sec="tr"]');
    const trSec = bodyEl.querySelector('[data-secbody="tr"]');
    if (trHead && trSec && trSec.hidden) trHead.click();
    if (trHead && trHead.scrollIntoView) trHead.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// 「幾分鐘前」：轉錄器最後一次輪詢時間（秒或毫秒或 ISO 字串都吃）
export function agoText(ts, now = Date.now()) {
  if (ts == null || ts === '') return '從未連線';
  let ms = typeof ts === 'number' ? ts : Date.parse(ts);
  if (!isFinite(ms)) return '不明';
  if (ms < 1e12) ms *= 1000; // 秒 → 毫秒
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分鐘前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小時前`;
  return `${Math.floor(s / 86400)} 天前`;
}

function renderSettings() {
  setHeader('設定', true);
  // 這支 App 的網址（去掉 #hash 與 ?query）——PWA 從主畫面開啟時看不到網址列，
  // 需要換裝置或分享給別人時，這裡是唯一拿得到網址的地方。
  const appUrl = location.origin + location.pathname;
  view.innerHTML = `
    <div class="card">
      <p style="margin-top:0"><b>🖥️ NAS 狀態</b></p>
      <div id="nasStatus" class="usage-list"><div class="meta">讀取中…</div></div>
      <button class="big secondary" id="statusRefresh" style="margin-top:8px">重新整理</button>
    </div>
    <div class="card">
      <p style="margin-top:0"><b>⚙️ 處理設定</b>（存在 NAS，所有裝置共用）</p>
      <p style="margin-bottom:6px"><b>Claude 型號</b>（摘要、加強、筆記、問答、掃詞、翻譯）</p>
      <select id="claudeModel" style="width:100%" disabled>
        <option value="opus">Opus（預設，品質最好，較慢、較吃額度）</option>
        <option value="sonnet">Sonnet（品質與速度平衡）</option>
        <option value="haiku">Haiku（最快，品質較低）</option>
      </select>
      <p style="margin-bottom:6px"><b>轉錄詞庫</b>（一行一個詞：人名、公司、術語…）</p>
      <textarea id="glossary" class="set-ta" rows="8" placeholder="股利&#10;複委託&#10;BVI" disabled></textarea>
      <div class="hint" style="margin-top:4px">轉錄時提示語音辨識這些詞，也用來校正與驗證摘要。改完點框外就會存到 NAS。</div>
      <label class="set-row"><input type="checkbox" id="fallbackEnabled" disabled /> <span>NAS 備援轉錄（電腦沒開時，NAS 自己慢慢轉；不分說話者）</span></label>
      <div class="set-row"><span>等電腦幾分鐘沒接手才啟動備援：</span><input type="number" id="fallbackMinutes" min="1" max="1440" style="width:80px" disabled /> 分鐘</div>
      <label class="set-row"><input type="checkbox" id="tgNotify" disabled /> <span>Telegram 通知（轉好時傳訊息給你）</span></label>
      <div class="hint" id="setHint"></div>
    </div>
    <div class="card">
      <p style="margin-top:0"><b>🔗 這個 App 的網址</b></p>
      <input type="text" id="appUrl" value="${esc(appUrl)}" readonly />
      <button class="big secondary" id="copyUrl" style="margin-top:8px">複製網址</button>
      <div class="hint">在電腦或其他手機（要先連上 Tailscale）用這個網址開啟，看到的是同一份 NAS 上的會議記錄。</div>
    </div>
    <div class="card">
      <p style="margin-top:0"><b>💾 備份 / 還原</b></p>
      <a class="big secondary btn-link" id="exportBtn" href="${esc(api.exportAllUrl())}" download>⬆️ 匯出備份檔</a>
      <button class="big secondary" id="importBtn" style="margin-top:8px">⬇️ 匯入備份檔</button>
      <input type="file" id="importFile" accept="application/json,.json" hidden />
      <div class="hint">
        匯出會把 NAS 上<b>全部會議 + 分類</b>存成一個 JSON 檔（與舊版備份格式相同）。匯入舊版備份會與 NAS 上的資料<b>合併</b>，不會刪掉任何現有會議。
      </div>
    </div>
    <div class="card">
      <p style="margin-top:0"><b>ℹ️ 關於／更新</b></p>
      <div class="meta" style="margin-bottom:10px">目前版本：${APP_VERSION}<span id="srvVer"></span></div>
      <button class="big secondary" id="forceUpdateBtn">🔄 檢查並載入最新版</button>
      <div class="hint">若畫面沒更新到最新，按這顆會清除快取並重新載入最新版。</div>
    </div>`;

  const drawStatus = async () => {
    const box = document.getElementById('nasStatus');
    try {
      const s = await api.getStatus();
      setOffline(false);
      if (!document.body.contains(box)) return;
      const w = s.worker || {};
      const row = (k, v) => `<div class="usage-row"><span class="u-name">${k}</span><span class="u-stat">${v}</span></div>`;
      box.innerHTML =
        row('轉錄器（電腦）', w.online ? '<b style="color:#1f9d55">● 線上</b>' : '<b style="color:var(--danger)">● 離線</b>') +
        row('最後連線', esc(agoText(w.lastSeen))) +
        row('佇列', `${esc(s.queue != null ? (typeof s.queue === 'object' ? JSON.stringify(s.queue) : s.queue) : 0)} 件`) +
        row('Claude 型號', esc(s.claudeModel || '—')) +
        (s.diskFreeGB != null ? row('NAS 剩餘空間', `${esc(Math.round(s.diskFreeGB))} GB`) : '');
      const sv = document.getElementById('srvVer');
      if (sv && s.version) sv.textContent = `（伺服器 ${s.version}）`;
    } catch (e) {
      noteFetchError(e);
      if (document.body.contains(box)) box.innerHTML = `<div class="err">讀不到 NAS 狀態：${esc(errText(e))}</div>`;
    }
  };
  drawStatus();
  document.getElementById('statusRefresh').onclick = drawStatus;

  // 伺服器設定：讀到才開放修改；每次改動立即寫回
  const els = {
    claudeModel: document.getElementById('claudeModel'),
    fallbackEnabled: document.getElementById('fallbackEnabled'),
    fallbackMinutes: document.getElementById('fallbackMinutes'),
    tgNotify: document.getElementById('tgNotify'),
    glossary: document.getElementById('glossary'),
  };
  // 詞庫：一行一詞，去頭尾空白與空行
  const cleanGlossary = (s) =>
    String(s || '')
      .split(/\r?\n/)
      .map((x) => x.trim())
      .filter(Boolean)
      .join('\n');
  const setHint = document.getElementById('setHint');
  let settings = null;
  (async () => {
    try {
      settings = await api.getSettings();
      els.claudeModel.value = settings.claudeModel || 'opus';
      els.glossary.value = Array.isArray(settings.glossary) ? settings.glossary.join('\n') : settings.glossary || '';
      els.fallbackEnabled.checked = !!settings.fallbackEnabled;
      els.fallbackMinutes.value = settings.fallbackMinutes != null ? settings.fallbackMinutes : 20;
      els.tgNotify.checked = !!settings.tgNotify;
      Object.values(els).forEach((x) => (x.disabled = false));
      els.fallbackMinutes.disabled = !els.fallbackEnabled.checked;
    } catch (e) {
      noteFetchError(e);
      setHint.textContent = '讀不到伺服器設定：' + errText(e);
    }
  })();
  const saveSet = async () => {
    if (!settings) return;
    const mins = Math.max(1, Math.min(1440, parseInt(els.fallbackMinutes.value, 10) || 20));
    els.fallbackMinutes.value = mins;
    els.fallbackMinutes.disabled = !els.fallbackEnabled.checked;
    const next = {
      ...settings,
      claudeModel: els.claudeModel.value,
      fallbackEnabled: els.fallbackEnabled.checked,
      fallbackMinutes: mins,
      tgNotify: els.tgNotify.checked,
    };
    // 伺服器沒給詞庫、使用者也沒動過 → 不送（避免用空字串蓋掉伺服器內建詞庫）
    if (settings.glossary != null || glossaryTouched) next.glossary = cleanGlossary(els.glossary.value);
    try {
      const r = await api.saveSettings(next);
      settings = r && typeof r === 'object' ? { ...next, ...r } : next;
      toast('設定已存到 NAS ✓');
    } catch (e) {
      alert('設定儲存失敗：' + errText(e));
    }
  };
  els.claudeModel.onchange = saveSet;
  els.fallbackEnabled.onchange = saveSet;
  els.fallbackMinutes.onchange = saveSet;
  els.tgNotify.onchange = saveSet;
  let glossaryTouched = false;
  els.glossary.onchange = () => {
    // textarea 失焦且內容有變時觸發
    glossaryTouched = true;
    saveSet();
  };

  document.getElementById('copyUrl').onclick = async (e) => {
    const b = e.target;
    try {
      await navigator.clipboard.writeText(appUrl);
      b.textContent = '已複製 ✓';
    } catch (_) {
      // iOS 在非使用者手勢或無 HTTPS 時可能失敗 → 退而求其次幫他選取，讓使用者長按複製
      const f = document.getElementById('appUrl');
      f.focus();
      f.setSelectionRange(0, f.value.length);
      b.textContent = '請長按上方網址複製';
    }
    setTimeout(() => (b.textContent = '複製網址'), 1800);
  };
  document.getElementById('forceUpdateBtn').onclick = () => {
    toast('更新中…');
    forceUpdate();
  };

  // 匯入備份：讀 JSON 交給 NAS 合併（不刪任何現有資料）
  const importInput = document.getElementById('importFile');
  document.getElementById('importBtn').onclick = () => importInput.click();
  importInput.onchange = async (e) => {
    const f = (e.target.files || [])[0];
    if (!f) return;
    try {
      let doc;
      try {
        doc = JSON.parse(await f.text());
      } catch (_) {
        throw new Error('這不是有效的備份檔（JSON 解析失敗）');
      }
      if (!doc || !Array.isArray(doc.meetings)) throw new Error('備份檔格式不符（找不到 meetings）');
      toast('匯入中…');
      const r = (await api.importBackup(doc)) || {};
      await refreshGroups();
      await fetchList();
      const n = (k) => (Array.isArray(r[k]) ? r[k].length : r[k] || 0);
      alert(`匯入完成：新增 ${n('added')} 場、更新 ${n('updated')} 場、保留 ${n('kept')} 場${r.groups != null ? `，分類 ${n('groups')} 個` : ''}。`);
    } catch (err) {
      alert('匯入失敗：' + errText(err));
    } finally {
      importInput.value = ''; // 允許重選同一個檔案
    }
  };
}

function router() {
  renderGen++;
  const h = location.hash || '#/';
  refreshBanner(); // 每次換頁都重算橫幅：上傳中離開上傳頁時才會有回去的入口
  if (h.startsWith('#/m/')) {
    detailUi.id = ''; // 從別頁進來：校對面板收起、編輯模式關閉（頁內重繪不經過這裡，狀態會保留）
    return renderDetail(decodeURIComponent(h.slice(4)));
  }
  if (h.startsWith('#/job/')) return renderProgress(decodeURIComponent(h.slice(6)));
  if (h === '#/new') return renderNew();
  if (h === '#/settings') return renderSettings();
  if (h === '#/groups') return renderGroups();
  if (h.startsWith('#/g/')) return renderList(decodeURIComponent(h.slice(4)));
  return renderList();
}
window.addEventListener('hashchange', router);
router();

// 啟動時向 NAS 拿最新分類；清單類畫面的分類名稱／顏色要跟著更新
refreshGroups().then(() => {
  const h = location.hash || '#/';
  if (h === '#/' || h === '#/groups' || h === '#/new' || h.startsWith('#/g/')) {
    if (!(h === '#/new' && upload)) router();
  }
});

// 強制更新後網址會帶 ?fresh=…，載入完成後把它清掉（保持網址乾淨、離線也好命中快取）
if (location.search.includes('fresh=')) {
  try {
    const u = new URL(location.href);
    u.search = '';
    history.replaceState(null, '', u.toString());
  } catch (_) {}
}

// 啟動畫面：載入後淡出移除
function hideSplash() {
  const sp = document.getElementById('splash');
  if (!sp) return;
  setTimeout(() => {
    sp.classList.add('hide');
    setTimeout(() => sp.remove(), 500);
  }, 550);
}
if (document.readyState === 'complete') hideSplash();
else window.addEventListener('load', hideSplash);

// 「加入主畫面」教學：只在 iPhone 用瀏覽器（非已安裝）且未關閉過時顯示
function isStandalone() {
  return window.navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches;
}
function maybeShowInstallHint() {
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  if (!isIOS || isStandalone() || localStorage.getItem('install_hint_dismissed')) return;
  const b = document.createElement('div');
  b.className = 'install-banner';
  b.innerHTML = `<span>📲 想像 App 一樣用？點下方 <b>分享鈕</b> → <b>加入主畫面</b></span><span class="x" id="closeHint">×</span>`;
  document.body.appendChild(b);
  document.getElementById('closeHint').onclick = () => {
    localStorage.setItem('install_hint_dismissed', '1');
    b.remove();
  };
}
maybeShowInstallHint();


// 註冊 Service Worker（PWA / 離線）+ 自動更新
let refreshing = false;
if ('serviceWorker' in navigator) {
  // 有新版 SW 接手時自動重新載入（僅在已安裝過的情況，避免首次安裝就重整）
  if (navigator.serviceWorker.controller) {
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (refreshing) return;
      refreshing = true;
      // 上傳／建立會議途中重整會中斷上傳 → 延到上傳結束再重整
      if (uploadInProgress(upload)) reloadAfterUpload = true;
      else window.location.reload();
    });
  }
  window.addEventListener('load', () => {
    // updateViaCache:'none' → 不用瀏覽器快取的 sw.js，每次都抓最新版檢查更新
    navigator.serviceWorker
      .register('./sw.js', { updateViaCache: 'none' })
      .then((reg) => {
        reg.update();
        setInterval(() => reg.update(), 60 * 60 * 1000);
      })
      .catch(() => {});
  });
}

// 強制載入最新版：清掉 SW 與所有快取後重整（設定頁按鈕使用）
async function forceUpdate() {
  try {
    if ('serviceWorker' in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
    }
    if (window.caches) {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch (_) {}
  // 用帶時間戳的網址重新載入，強制繞過 iOS 的 HTTP 快取
  const u = new URL(location.href);
  u.searchParams.set('fresh', String(Date.now()));
  location.replace(u.toString());
}
