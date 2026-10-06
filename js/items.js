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
// ---- 與伺服器 server/items.py 一致的條目純文字與 id（待確認項目的 itemId 用這個算）----
// Python str() 的寫法：None → 'None'、True → 'True'
const pyStr = (v) => (v == null ? 'None' : v === true ? 'True' : v === false ? 'False' : String(v));
const orEmpty = (v) => (v ? String(v) : ''); // Python 的 str(x or "")
// = items.item_text：舊字串、Item、筆記物件（大綱／名詞／圖表數字／表格／測驗）都攤成一段文字
export function itemFlatText(x) {
  if (x == null) return '';
  if (typeof x === 'string') return x;
  if (typeof x !== 'object' || Array.isArray(x)) return String(x);
  if (typeof x.text === 'string') return x.text;
  if ('q' in x || 'a' in x) {
    const q = orEmpty(x.q).trim();
    const a = orEmpty(x.a).trim();
    return a ? `問：${q}\n答：${a}` : `問：${q}`;
  }
  if ('term' in x) {
    const parts = [orEmpty(x.term), orEmpty(x.plain)].filter(Boolean);
    const why = orEmpty(x.why);
    return parts.join('：') + (why ? `（${why}）` : '');
  }
  if ('label' in x || 'value' in x) return `${orEmpty(x.label)}：${orEmpty(x.value)}`;
  if ('headers' in x || 'rows' in x) {
    const lines = [orEmpty(x.title), (x.headers || []).map(pyStr).join('｜')];
    for (const r of x.rows || []) lines.push((Array.isArray(r) ? r : [r]).map(pyStr).join('｜'));
    return lines.filter(Boolean).join('\n');
  }
  if ('title' in x || 'points' in x) {
    const pts = (x.points || []).map(itemFlatText);
    return [orEmpty(x.title)].concat(pts).join('\n').trim();
  }
  return '';
}
const HEX8 = /^[0-9a-f]{8}$/;
// 條目的 id（= items.normalize_item／normalize_entry）：合法的 8 碼 hex id 用自己的，否則 sha1(純文字) 前 8 碼
export const itemIdOf = (x) =>
  x && typeof x === 'object' && typeof x.id === 'string' && HEX8.test(x.id) ? x.id : sha1Hex(itemFlatText(x)).slice(0, 8);
// 一整區的 id（= items.ensure_ids）：重複的 id 用「純文字#索引#k」重算，跟伺服器一樣
export function listItemIds(arr) {
  const seen = new Set();
  return (Array.isArray(arr) ? arr : []).map((x, i) => {
    let id = itemIdOf(x);
    if (seen.has(id)) {
      const text = itemFlatText(x);
      for (let k = 1; ; k++) {
        id = sha1Hex(`${text}#${i}#${k}`).slice(0, 8);
        if (!seen.has(id)) break;
      }
    }
    seen.add(id);
    return id;
  });
}

// 待確認清單：伺服器是唯一依據（換版／合併／刪除後，存檔時伺服器會重新驗證並回傳新的清單）
export const reviewItems = (m) => (m && m.review && Array.isArray(m.review.items) ? m.review.items : []);
export const pendingReview = (m) => reviewItems(m).filter((r) => r && r.state === 'pending');
export const pendingCount = (m) => pendingReview(m).length;
// 沒有對應條目可改／刪的項目（講者口誤等；名詞校正除外）→ 只能按「知道了」
export const confirmOnly = (r) => !!r && r.kind !== 'term' && (!r.itemId || !/^(summary|notes)\./.test(String(r.section || '')));
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

// ---- 單字訂正卡（ReviewItem v2：有 span 的項目）----
export const isWordReview = (r) => !!r && typeof r.span === 'string' && r.span.length > 0;
// 會改到哪些地方：逐字稿幾處、摘要／筆記幾條
export function reviewScope(r) {
  let transcript = 0;
  let items = 0;
  (Array.isArray(r && r.targets) ? r.targets : []).forEach((x) => {
    if (!x) return;
    const n = Math.max(0, Number(x.count) || 0);
    if (x.where === 'transcript') transcript += n;
    else if (/^(summary|notes)\./.test(String(x.where || ''))) items += 1; // 條數 = 條目數（count 是條目內出現次數），與伺服器 applied.items 一致
  });
  return { transcript, items };
}
// 「全部照建議」資格（與伺服器同規則；伺服器為準）：待確認、高信心、不是程式標的人名、span／建議不像人名
const NAME_SURNAMES = '王李張劉陳楊黃趙吳周徐孫朱胡郭何林羅高梁鄭謝宋唐許韓馮鄧曹彭曾蕭蔡潘杜戴夏鍾汪田任姜范方石姚譚廖鄒熊陸郝孔崔康毛邱秦史顧侯邵孟龍萬段雷錢湯尹黎易常武喬賀賴龔文盧簡游柯魏翁施詹余葉洪呂蘇沈莊江涂連童傅歐卓溫邢藍嚴董薛';
const looksLikeName = (s) => {
  s = String(s || '').trim();
  return s.length >= 2 && s.length <= 4 && /^[一-鿿]+$/.test(s) && NAME_SURNAMES.includes(s[0]);
};
export const isAcceptHighEligible = (r) =>
  isWordReview(r) && r.state === 'pending' && r.confidence === 'high' && !(Array.isArray(r.sources) && r.sources.includes('name')) &&
  !looksLikeName(r.span) && !looksLikeName(r.suggestion);
export const scopeText = (s, prefix) => `${prefix}逐字稿 ${Number(s.transcript) || 0} 處、摘要 ${Number(s.items) || 0} 條`;
// "[01:02:03]" / "[02:03]" → 秒數
export function hmsToSec(s) {
  const p = String(s).split(':').map(Number);
  if (p.some((x) => !isFinite(x))) return null;
  return p.reduce((a, x) => a * 60 + x, 0);
}
// 把字串切成 [{text}|{t, label}]：[HH:MM:SS] 或 [MM:SS] 變成時間片段
export function splitTimes(s) {
  const out = [];
  const re = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g;
  const str = String(s || '');
  let last = 0;
  let mm;
  while ((mm = re.exec(str))) {
    if (mm.index > last) out.push({ text: str.slice(last, mm.index) });
    out.push({ t: hmsToSec(mm[1]), label: mm[1] });
    last = mm.index + mm[0].length;
  }
  if (last < str.length) out.push({ text: str.slice(last) });
  return out;
}
// 上下文：【…】之間的字標示起來；其他照原樣（呼叫端負責跳脫）→ [{text, hit}]
export function splitContext(ctx) {
  const out = [];
  const re = /【([^【】]*)】/g;
  const str = String(ctx || '');
  let last = 0;
  let mm;
  while ((mm = re.exec(str))) {
    if (mm.index > last) out.push({ text: str.slice(last, mm.index), hit: false });
    out.push({ text: mm[1], hit: true });
    last = mm.index + mm[0].length;
  }
  if (last < str.length) out.push({ text: str.slice(last), hit: false });
  return out;
}
// 未辨識段落總分鐘數（四捨五入，至少 1）
export function gapMinutes(gaps) {
  const sec = (Array.isArray(gaps) ? gaps : []).reduce((a, g) => a + Math.max(0, (Number(g && g.end) || 0) - (Number(g && g.start) || 0)), 0);
  return sec > 0 ? Math.max(1, Math.round(sec / 60)) : 0;
}
// ---- 待確認勾選清單（v107）：一列一組「錯字 → 正確」，同一組字的多張卡合成一列 ----
// 與伺服器 wordreview.word_pair 同規則（伺服器已存 from／to，這裡只是舊資料的備援）
const PAIR_MAX = 12;
const clipPair = (s) => (s.length <= PAIR_MAX ? s : s.slice(0, PAIR_MAX - 1) + '…');
const WORDISH = /[\p{L}\p{N}_]/u;
export function wordPair(r) {
  const span = String((r && r.span) || '');
  const sug = String((r && r.suggestion) || '');
  if (r && typeof r.from === 'string' && typeof r.to === 'string') return { from: r.from, to: r.to };
  if (r && r.action === 'delete') return { from: clipPair(span), to: '（刪除）' };
  if (!sug) return { from: clipPair(span), to: clipPair(span) };
  if (span.length <= 4 && sug.length <= 4) return { from: span, to: sug };
  const a = [...span];
  const b = [...sug];
  const n = Math.min(a.length, b.length);
  let p = 0;
  while (p < n && a[p] === b[p]) p++;
  let s = 0;
  while (s < n - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
  let i = p;
  let ja = a.length - s;
  let jb = b.length - s;
  while (ja - i < 2 || jb - i < 2) {
    if (i > 0 && WORDISH.test(a[i - 1])) i--;
    else if (s > 0 && WORDISH.test(a[ja])) {
      ja++;
      jb++;
      s--;
    } else break;
  }
  return { from: clipPair(a.slice(i, ja).join('')), to: clipPair(b.slice(i, jb).join('')) };
}
const RANK = { high: 3, medium: 2, low: 1 };
const cardCount = (r) => (Array.isArray(r.targets) ? r.targets : []).reduce((a, x) => a + Math.max(0, Number(x && x.count) || 0), 0);
// 待確認的 replace／delete 卡 → 列 [{id, from, to, action, cards:[卡], count, confidence}]（AI 判定「不是錯誤」的 keep 卡不列）
export function reviewRows(m) {
  const rows = new Map();
  pendingReview(m)
    .filter((r) => isWordReview(r) && (r.action === 'replace' || r.action === 'delete'))
    .forEach((r) => {
      const { from, to } = wordPair(r);
      const key = from + '\u0000' + to;
      let row = rows.get(key);
      if (!row) {
        row = { id: r.id, from, to, action: r.action, cards: [], count: 0, confidence: r.confidence || 'low' };
        rows.set(key, row);
      }
      row.cards.push(r);
      row.count += cardCount(r);
      if ((RANK[r.confidence] || 1) < (RANK[row.confidence] || 1)) row.confidence = r.confidence || 'low';
    });
  return [...rows.values()];
}
// 舊格式（沒有 span）的待確認項目：維持舊卡片
export const legacyPending = (m) => pendingReview(m).filter((r) => !isWordReview(r));
