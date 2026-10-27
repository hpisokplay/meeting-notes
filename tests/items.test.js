import { describe, it, expect } from 'vitest';
import {
  itemText,
  itemSrc,
  fmtSec,
  meetingMode,
  summarySections,
  groupItems,
  pendingCount,
  pendingReview,
  segIndexAt,
  exportLockText,
  sha1Hex,
  itemIdOf,
  srcSegIndex,
  itemFlatText,
  listItemIds,
  confirmOnly,
  hasReport,
  reportOutline,
  ALL_SUMMARY_KEYS,
} from '../js/items.js';
import { meetingToHtmlBody, splitQA } from '../js/export.js';
import { buildDocxBytes } from '../js/docx.js';
import { matchMeeting } from '../js/search.js';

const obj = (text, extra = {}) => ({ id: 'abcd1234', text, src: [{ t: 65.2, quote: '原句' }], ...extra });

describe('items：字串與物件條目都吃', () => {
  it('itemText／itemSrc', () => {
    expect(itemText('舊字串')).toBe('舊字串');
    expect(itemText(obj('新條目'))).toBe('新條目');
    expect(itemText(null)).toBe('');
    expect(itemSrc('舊字串')).toEqual([]);
    expect(itemSrc(obj('x'))).toEqual([{ t: 65.2, quote: '原句' }]);
    expect(itemSrc({ text: 'x', src: [{ quote: '沒時間' }, { t: 'abc' }] })).toEqual([]);
  });
  it('fmtSec', () => {
    expect(fmtSec(65.2)).toBe('1:05');
    expect(fmtSec(3725)).toBe('1:02:05');
  });
  it('mode：舊資料視為會議；區塊依模式', () => {
    expect(meetingMode({})).toBe('meeting');
    expect(meetingMode({ mode: 'lecture' })).toBe('lecture');
    expect(summarySections({}).map((s) => s.k)).toEqual(['actionItems', 'mainPoints', 'qa']);
    expect(summarySections({ mode: 'lecture' }).map((s) => s.k)).toEqual([
      'conclusions', 'advice', 'risks', 'cases', 'mainPoints', 'qa',
    ]);
  });
  it('groupItems：依 group 分組保留順序與原索引；沒 group 時一組', () => {
    expect(groupItems(['a', 'b'])).toEqual([{ group: '', items: [['a', 0], ['b', 1]] }]);
    const g = groupItems([obj('1', { group: '稅' }), obj('2', { group: '信託' }), obj('3', { group: '稅' })]);
    expect(g.map((x) => x.group)).toEqual(['稅', '信託']);
    expect(g[0].items.map((x) => x[1])).toEqual([0, 2]);
  });
  it('待確認數量只算 pending', () => {
    const m = { review: { items: [{ id: 'a', state: 'pending' }, { id: 'b', state: 'confirmed' }, { id: 'c', state: 'pending' }] } };
    expect(pendingCount(m)).toBe(2);
    expect(pendingReview(m).map((r) => r.id)).toEqual(['a', 'c']);
    expect(pendingCount({})).toBe(0);
    expect(exportLockText(2)).toBe('還有 2 項待確認，處理完才能匯出');
  });
  it('segIndexAt：找秒數所在段落', () => {
    const tr = [{ t: 0 }, { t: 60 }, { t: 120 }];
    expect(segIndexAt(70, tr)).toBe(1);
    expect(segIndexAt(500, tr)).toBe(2);
    expect(segIndexAt(0, tr)).toBe(0);
    expect(segIndexAt(5, [])).toBe(-1);
  });
  it('sha1Hex 與 itemIdOf（舊字串條目的 id = sha1 前 8 碼，跟伺服器一致）', () => {
    expect(sha1Hex('abc')).toBe('a9993e364706816aba3e25717850c26c9cd0d89d');
    expect(sha1Hex('')).toBe('da39a3ee5e6b4b0d3255bfef95601890afd80709');
    expect(sha1Hex('中文')).toBe('7be2d2d20c106eee0836c9bc2b939890a78e8fb3');
    expect(itemIdOf('abc')).toBe('a9993e36');
    // 不是 8 碼 hex 的 id，伺服器會用文字重算 → 前端也要一樣
    expect(itemIdOf({ id: 'x1', text: 'abc' })).toBe('a9993e36');
    expect(itemIdOf({ id: 'abcdef12', text: 'x' })).toBe('abcdef12');
  });
  it('itemFlatText／listItemIds 與伺服器 items.item_text／ensure_ids 一致（舊筆記物件、重複文字）', () => {
    // 期望值由 server/items.py 算出
    const notes = {
      outline: [{ title: '第一節', points: ['要點甲', '要點乙'] }, { title: '第一節', points: ['要點甲', '要點乙'] }],
      concepts: [{ term: '複委託', plain: '透過國內券商買海外股票', why: '常考' }, { term: 'CRS' }],
      figures: [{ label: '稅率', value: '20%' }],
      quiz: [{ q: '什麼是 CRS？', a: '共同申報準則' }, { q: '只有問題' }],
      tables: [{ title: '比較', headers: ['項目', '數值'], rows: [['A', 1], ['B', null]] }],
    };
    expect(itemFlatText(notes.outline[0])).toBe('第一節\n要點甲\n要點乙');
    expect(itemFlatText(notes.concepts[0])).toBe('複委託：透過國內券商買海外股票（常考）');
    expect(itemFlatText(notes.figures[0])).toBe('稅率：20%');
    expect(itemFlatText(notes.quiz[0])).toBe('問：什麼是 CRS？\n答：共同申報準則');
    expect(itemFlatText(notes.tables[0])).toBe('比較\n項目｜數值\nA｜1\nB｜None');
    expect(listItemIds(notes.outline)).toEqual(['c81c9efc', '11db2b52']); // 同內容第二節重算
    expect(listItemIds(notes.concepts)).toEqual(['42f73d18', '037c298c']);
    expect(listItemIds(notes.figures)).toEqual(['fd1c4d81']);
    expect(listItemIds(notes.quiz)).toEqual(['4ff24c9b', 'bb6134b8']);
    expect(listItemIds(notes.tables)).toEqual(['82ceac83']);
    const summary = ['同一句', '同一句', '同一句', { id: 'x1', text: '非 hex id' }, { id: 'abcdef12', text: '合法' }];
    expect(listItemIds(summary)).toEqual(['42573675', '86dae13c', '96e8824a', '7d2cf1a8', 'abcdef12']);
  });
  it('待確認數量以伺服器的 state 為準：指向的條目不見了也照算（伺服器存檔時會重新驗證）', () => {
    const m = {
      summary: { mainPoints: [{ id: 'k1', text: 'a' }], qa: [] },
      review: {
        items: [
          { id: 'r1', section: 'summary.mainPoints', itemId: 'k1', state: 'pending' },
          { id: 'r2', section: 'summary.mainPoints', itemId: 'gone', state: 'pending' },
          { id: 'r3', section: 'summary.risks', itemId: 'k9', state: 'confirmed' },
          { id: 'r4', kind: 'term', section: 'transcript', state: 'pending' },
        ],
      },
    };
    expect(pendingReview(m).map((r) => r.id)).toEqual(['r1', 'r2', 'r4']);
    expect(pendingCount(m)).toBe(3);
  });
  it('confirmOnly：口誤等沒有對應條目的項目只能「知道了」；名詞與條目項目可改正／刪除', () => {
    expect(confirmOnly({ kind: 'slip', section: 'transcript' })).toBe(true);
    expect(confirmOnly({ kind: 'quote', section: 'transcript' })).toBe(true);
    expect(confirmOnly({ kind: 'term', section: 'transcript' })).toBe(false);
    expect(confirmOnly({ kind: 'number', section: 'summary.mainPoints', itemId: 'k1' })).toBe(false);
    expect(confirmOnly({ kind: 'quote', section: 'notes.outline', itemId: 'o1' })).toBe(false);
  });
  it('srcSegIndex：多支錄音每支 t 從 0 開始 → 用原句在 ±30 秒的候選裡挑；沒原句取檔案順序最前', () => {
    const tr = [
      { speaker: 'A', text: '第一支開場白大家好', t: 10, fi: 0 },
      { speaker: 'B', text: '第一支講稅務', t: 100, fi: 0 },
      { speaker: 'A', text: '第二支講信託安排的細節', t: 12, fi: 1 },
      { speaker: 'B', text: '第二支結尾', t: 100, fi: 1 },
    ];
    expect(srcSegIndex(15, '信託安排', tr)).toBe(2);
    expect(srcSegIndex(15, '開場白', tr)).toBe(0);
    expect(srcSegIndex(15, '', tr)).toBe(0);
    expect(srcSegIndex(15, '完全無關xyz', tr)).toBe(0);
    expect(srcSegIndex(95, '結尾', tr)).toBe(3);
    // 單支：取涵蓋該秒數的段落
    const one = [{ text: 'a', t: 0 }, { text: 'b', t: 20 }, { text: 'c', t: 40 }];
    expect(srcSegIndex(25, '', one)).toBe(1);
    expect(srcSegIndex(500, '', one)).toBe(2); // 沒有候選 → 退回最後一段 t ≤ 秒數
    expect(srcSegIndex(5, 'x', [])).toBe(-1);
  });
  it('splitQA 吃物件', () => {
    expect(splitQA(obj('問：何時？\n答：下週'))).toEqual({ q: '何時？', a: '下週' });
  });
  it('搜尋比對物件條目與講座區塊', () => {
    const m = { title: 't', transcript: [], summary: { conclusions: [obj('境外資金匯回')], mainPoints: ['舊字串重點'] } };
    expect(matchMeeting(m, '匯回')).toBe(true);
    expect(matchMeeting(m, '舊字串')).toBe(true);
    expect(matchMeeting(m, 'object')).toBe(false);
  });
});

const lecture = {
  title: '會計師講座',
  createdAt: 0,
  mode: 'lecture',
  transcript: [{ speaker: '講者', text: '大家好', t: 0 }],
  summary: {
    conclusions: [obj('結論一')],
    advice: [obj('不要自行換算')],
    risks: [obj('小心 CRS')],
    cases: [],
    mainPoints: [obj('重點甲', { group: '稅務' }), obj('重點乙', { group: '信託' })],
    qa: [obj('問：可以嗎？\n答：不建議')],
  },
};

function docXml(m, opts) {
  // store 無壓縮 → document.xml 原文就在位元組裡
  return new TextDecoder().decode(buildDocxBytes(m, opts));
}

describe('匯出：物件條目與講座區塊', () => {
  it('PDF HTML：物件條目只輸出文字；講座區塊與分組標題', () => {
    const html = meetingToHtmlBody(lecture);
    for (const t of ['核心結論', '講者建議', '風險提醒', '案例', '重點', 'Q&amp;A', '結論一', '不要自行換算', '稅務', '信託', '不建議'])
      expect(html).toContain(t);
    expect(html).not.toContain('[object Object]');
    expect(html).not.toContain('待辦事項');
    expect(html).toContain('start="2"'); // 第二組接續編號
  });
  it('Word：講座區塊標題與條目文字', () => {
    const xml = docXml(lecture);
    for (const t of ['🎯 核心結論', '💡 講者建議', '⚠️ 風險提醒', '📂 案例', '📌 重點', 'Q&amp;A', '1. 重點甲', '2. 重點乙', '稅務'])
      expect(xml).toContain(t);
    expect(xml).not.toContain('[object Object]');
    expect(xml).not.toContain('待辦事項');
    // 可關掉個別講座區塊
    expect(docXml(lecture, { advice: false })).not.toContain('講者建議');
  });
  it('會議模式的物件條目：舊標題不變、只輸出文字', () => {
    const m = { title: 'x', createdAt: 0, transcript: [], summary: { actionItems: [obj('交報告')], mainPoints: [obj('重點')], qa: [] } };
    const xml = docXml(m);
    expect(xml).toContain('待辦事項 Action Item');
    expect(xml).toContain('1. 交報告');
    expect(meetingToHtmlBody(m)).toContain('<li>交報告</li>');
  });
});

describe('待確認勾選清單（v107）', () => {
  it('wordPair：短詞整個、長句只取改到的詞、單字擴成兩字、刪除最多 12 字；伺服器有 from／to 就用', async () => {
    const { wordPair } = await import('../js/items.js');
    expect(wordPair({ span: '討論稅', suggestion: '逃漏稅', action: 'replace' })).toEqual({ from: '討論稅', to: '逃漏稅' });
    expect(wordPair({ span: '李專', suggestion: '理專', action: 'replace' })).toEqual({ from: '李專', to: '理專' });
    expect(wordPair({ span: '所以選對專案你就自打嘴巴了', suggestion: '所以選對專案你就打自己嘴巴了', action: 'replace' })).toEqual({ from: '自打', to: '打自己' });
    expect(wordPair({ span: '黃任宗當初賣掉公司拿到', suggestion: '黃任中當初賣掉公司拿到', action: 'replace' })).toEqual({ from: '任宗', to: '任中' });
    expect(wordPair({ span: '選對專案你就自打嘴巴了真的', action: 'delete' })).toEqual({ from: '選對專案你就自打嘴巴了…', to: '（刪除）' });
    expect(wordPair({ span: 'x', from: 'A', to: 'B' })).toEqual({ from: 'A', to: 'B' });
  });
  it('reviewRows：同一組字合併（卡、處數加總、最低信心），keep／非待確認不列', async () => {
    const { reviewRows } = await import('../js/items.js');
    const m = { review: { items: [
      { id: 'a', span: '王曉明', action: 'replace', suggestion: '王小明', confidence: 'high', targets: [{ where: 'transcript', count: 2 }], state: 'pending' },
      { id: 'b', span: '王曉明說', action: 'replace', suggestion: '王小明說', from: '王曉明', to: '王小明', confidence: 'low', targets: [{ where: 'summary.x', count: 1 }], state: 'pending' },
      { id: 'c', span: '48%', action: 'keep', suggestion: '', confidence: 'high', targets: [], state: 'pending' },
      { id: 'd', span: '嗯', action: 'delete', confidence: 'high', targets: [], state: 'confirmed' },
    ] } };
    const rows = reviewRows(m);
    expect(rows.length).toBe(1);
    expect(rows[0]).toMatchObject({ from: '王曉明', to: '王小明', count: 3, confidence: 'low' });
    expect(rows[0].cards.map((x) => x.id)).toEqual(['a', 'b']);
  });
});

describe('完整整理 reportOutline／hasReport', () => {
  const P = (group, text) => ({ id: 'x', text, src: [], group });
  it('reportOutline splits title at first full-width colon', () => {
    const o = reportOutline({ reportIntro: [{ text: '開場' }], reportPoints: [P('壹', '標題：說明：更多')] });
    expect(o.intro).toBe('開場');
    expect(o.sections[0].heading).toBe('壹');
    expect(o.sections[0].subs[0].title).toBe('標題');
    expect(o.sections[0].subs[0].text).toBe('說明：更多');
    expect(o.sections[0].subs[0].item.group).toBe('壹');
  });
  it('reportOutline without colon gives empty title', () => {
    const o = reportOutline({ reportPoints: ['沒有冒號 a:b'] });
    expect(o.intro).toBe('');
    expect(o.sections[0].subs[0]).toMatchObject({ title: '', text: '沒有冒號 a:b' });
  });
  it('reportOutline keeps group first-seen order', () => {
    const o = reportOutline({ reportPoints: [P('B', 'a：1'), P('A', 'b：2'), P('B', 'c：3')] });
    expect(o.sections.map((s) => s.heading)).toEqual(['B', 'A']);
    expect(o.sections[0].subs.map((s) => s.title)).toEqual(['a', 'c']);
  });
  it('reportOutline tolerates null summary', () => {
    expect(reportOutline(null)).toEqual({ intro: '', sections: [] });
  });
  it('hasReport false for legacy meeting', () => {
    expect(hasReport({})).toBe(false);
    expect(hasReport({ summary: null })).toBe(false);
  });
  it('hasReport false when only intro', () => {
    expect(hasReport({ summary: { reportIntro: [{ text: 'x' }] } })).toBe(false);
    expect(hasReport({ summary: { reportIntro: [{ text: 'x' }], reportPoints: [P('A', 'a：b')] } })).toBe(true);
  });
  it('ALL_SUMMARY_KEYS includes report keys', () => {
    expect(ALL_SUMMARY_KEYS).toContain('reportIntro');
    expect(ALL_SUMMARY_KEYS).toContain('reportPoints');
  });
});
