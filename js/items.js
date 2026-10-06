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

// ---- SHA-1（同步、UTF-8）：舊字串條目的 id = sha1(text) 前 8 碼，與伺服器 items.normalize_item 一致 ----
export function sha1Hex(str) {
  const bytes = new TextEncoder().encode(String(str));
  const ml = bytes.length;
  const withPad = ((ml + 9 + 63) >> 6) << 6;
  const buf = new Uint8Array(withPad);
  buf.set(bytes);
  buf[ml] = 0x80;
  const bits = ml * 8;
  const dv = new DataView(buf.buffer);
  dv.setUint32(withPad - 4, bits >>> 0);
  dv.setUint32(withPad - 8, Math.floor(bits / 0x100000000));
  let h0 = 0x67452301, h1 = 0xefcdab89, h2 = 0x98badcfe, h3 = 0x10325476, h4 = 0xc3d2e1f0;
  const w = new Uint32Array(80);
  const rotl = (x, n) => (x << n) | (x >>> (32 - n));
  for (let off = 0; off < withPad; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
    for (let i = 16; i < 80; i++) w[i] = rotl(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    let a = h0, b = h1, c = h2, d = h3, e = h4;
    for (let i = 0; i < 80; i++) {
      const f = i < 20 ? (b & c) | (~b & d) : i < 40 ? b ^ c ^ d : i < 60 ? (b & c) | (b & d) | (c & d) : b ^ c ^ d;
      const k = i < 20 ? 0x5a827999 : i < 40 ? 0x6ed9eba1 : i < 60 ? 0x8f1bbcdc : 0xca62c1d6;
      const tmp = (rotl(a, 5) + f + e + k + w[i]) >>> 0;
      e = d;
      d = c;
      c = rotl(b, 30) >>> 0;
      b = a;
      a = tmp;
    }
    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
  }
  return [h0, h1, h2, h3, h4].map((x) => x.toString(16).padStart(8, '0')).join('');
}
// 條目的 id：物件用自己的 id；舊字串（或沒有 id 的物件）用 sha1(text) 前 8 碼
export const itemIdOf = (x) =>
  x && typeof x === 'object' && x.id ? String(x.id) : sha1Hex(itemText(x)).slice(0, 8);

// 待確認清單
export const reviewItems = (m) => (m && m.review && Array.isArray(m.review.items) ? m.review.items : []);
// 待確認項目指向的條目還在不在（手動刪除／換版／合併後可能已不存在 → 視為已處理）
function targetExists(m, r) {
  if (!r.itemId) return true; // 沒指向特定條目（名詞、逐字稿）
  const sec = String(r.section || '');
  const [box, key] = sec.split('.');
  if ((box !== 'summary' && box !== 'notes') || !key) return true;
  const arr = ((m && m[box]) || {})[key];
  if (!Array.isArray(arr)) return false;
  const want = String(r.itemId);
  return arr.some((x) => itemIdOf(x) === want);
}
export const pendingReview = (m) => reviewItems(m).filter((r) => r && r.state === 'pending' && targetExists(m, r));
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

// 出處（秒數＋原句）→ 逐字稿第幾段。多支錄音時每支的 t 都從 0 開始（seg.fi 標第幾支），
// 光靠秒數會撞到別支同秒數的段落 → 先找 t 在 ±30 秒內的候選段（任何一支），再用原句比對選最像的；
// 沒原句或比不出來就取檔案順序最前面的候選；完全沒候選才退回「最後一段 t ≤ 秒數」。
const bigrams = (s) => {
  const t = String(s || '').replace(/[\s，。、！？,.!?；;：:「」『』（）()]/g, '').toLowerCase();
  const g = new Set();
  for (let i = 0; i < t.length - 1; i++) g.add(t.slice(i, i + 2));
  return g;
};
export function srcSegIndex(t, quote, transcript, windowSec = 30) {
  const segs = transcript || [];
  if (!segs.length) return -1;
  const sec = Number(t) || 0;
  const cands = [];
  segs.forEach((s, i) => {
    const st = typeof s.t === 'number' ? s.t : Number(s.t);
    if (isFinite(st) && Math.abs(st - sec) <= windowSec) cands.push(i);
  });
  if (!cands.length) return segIndexAt(t, segs);
  const q = bigrams(quote);
  if (q.size) {
    let best = -1;
    let bestScore = 0;
    for (const i of cands) {
      const st = bigrams(segs[i].text);
      let score = 0;
      q.forEach((g) => st.has(g) && score++);
      if (score > bestScore) {
        bestScore = score;
        best = i;
      }
    }
    if (best >= 0 && bestScore >= Math.min(2, q.size)) return best;
  }
  // 比不出來：取檔案順序最前的那一支（fi 最小），在那一支裡取涵蓋這個秒數的段落（最後一段 t ≤ 秒數）
  const fiOf = (i) => Number(segs[i].fi) || 0;
  const firstFi = Math.min(...cands.map(fiOf));
  const inFile = cands.filter((i) => fiOf(i) === firstFi);
  const covering = inFile.filter((i) => Number(segs[i].t) <= sec + 0.5);
  return covering.length ? covering[covering.length - 1] : inFile[0];
}

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
