// 摘要／筆記條目的共用工具。
// 新格式條目是物件 {id, text, src:[{t, quote}], group?}；舊資料是純字串。所有顯示、複製、匯出都要兩種都吃。

export function itemText(x) {
  if (x == null) return '';
  if (typeof x === 'string') return x;
  if (typeof x === 'object' && typeof x.text === 'string') return x.text;
  return String(x);
}

// 出處：[{t: 秒, quote}]（只收 t 是有效數字的）
export function itemSrc(x) {
  if (!x || typeof x !== 'object' || !Array.isArray(x.src)) return [];
  return x.src.filter((s) => s && s.t != null && isFinite(Number(s.t))).map((s) => ({ t: Number(s.t), quote: s.quote || '' }));
}

// 秒數 → m:ss／h:mm:ss
export function fmtSec(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

// 講座／會議模式（舊資料沒有 mode → 會議）
export const meetingMode = (m) => (m && m.mode === 'lecture' ? 'lecture' : 'meeting');

// 各模式的摘要區塊：k = summary 的欄位，code = 畫面上的區塊代碼，label = 標題
export const MEETING_SECTIONS = [
  { k: 'actionItems', code: 'ai', label: '✅ 待辦事項 Action Item', short: '待辦事項' },
  { k: 'mainPoints', code: 'mp', label: '📌 會議重點 Main Point', short: '會議重點' },
  { k: 'qa', code: 'qa', label: '❓ 會議提問 Q&A', short: '會議提問 Q&A' },
];
export const LECTURE_SECTIONS = [
  { k: 'conclusions', code: 'lc', label: '🎯 核心結論', short: '核心結論' },
  { k: 'advice', code: 'la', label: '💡 講者建議', short: '講者建議' },
  { k: 'risks', code: 'lr', label: '⚠️ 風險提醒', short: '風險提醒' },
  { k: 'cases', code: 'lk', label: '📂 案例', short: '案例' },
  { k: 'mainPoints', code: 'mp', label: '📌 重點', short: '重點' },
  { k: 'qa', code: 'qa', label: '❓ Q&A', short: 'Q&A' },
];
export const summarySections = (m) => (meetingMode(m) === 'lecture' ? LECTURE_SECTIONS : MEETING_SECTIONS);
// 所有可能存在於 summary 的條目欄位（專有名詞訂正時要全部套用）
export const ALL_SUMMARY_KEYS = ['actionItems', 'mainPoints', 'keyPoints', 'qa', 'conclusions', 'advice', 'risks', 'cases'];

// 依 group 分組（保留第一次出現的順序）；回傳 [{group, items:[[item, 原索引]]}]。沒有任何 group 時回傳一組 group=''
export function groupItems(arr) {
  const list = arr || [];
  if (!list.some((x) => x && typeof x === 'object' && x.group)) return [{ group: '', items: list.map((x, i) => [x, i]) }];
  const order = [];
  const by = new Map();
  list.forEach((x, i) => {
    const g = (x && typeof x === 'object' && x.group) || '其他';
    if (!by.has(g)) {
      by.set(g, []);
      order.push(g);
    }
    by.get(g).push([x, i]);
  });
  return order.map((g) => ({ group: g, items: by.get(g) }));
}

// 待確認清單
export const reviewItems = (m) => (m && m.review && Array.isArray(m.review.items) ? m.review.items : []);
export const pendingReview = (m) => reviewItems(m).filter((r) => r && r.state === 'pending');
export const pendingCount = (m) => pendingReview(m).length;
export const exportLockText = (n) => `還有 ${n} 項待確認，處理完才能匯出`;

export const REVIEW_KIND = {
  term: '名詞',
  quote: '找不到原句',
  number: '數字無依據',
  name: '名稱無依據',
  calc: '計算不符',
  slip: '講者口誤',
};

// 某秒數落在哪一段逐字稿（最後一段 t ≤ 秒數者）
export function segIndexAt(t, transcript) {
  const segs = transcript || [];
  if (!segs.length) return -1;
  const sec = Number(t) || 0;
  let best = 0;
  segs.forEach((s, i) => {
    const st = typeof s.t === 'number' ? s.t : Number(s.t);
    if (isFinite(st) && st <= sec + 0.5) best = i;
  });
  return best;
}
