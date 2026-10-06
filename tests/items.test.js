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
    expect(itemIdOf({ id: 'x1', text: 'abc' })).toBe('x1');
  });
  it('待確認項目指向的條目已不存在（手動刪除／換版）→ 不算待確認', () => {
    const m = {
      summary: { mainPoints: [{ id: 'k1', text: 'a' }, 'abc'], qa: [] },
      notes: { outline: [{ id: 'o1', title: 't' }] },
      review: {
        items: [
          { id: 'r1', section: 'summary.mainPoints', itemId: 'k1', state: 'pending' },
          { id: 'r2', section: 'summary.mainPoints', itemId: 'gone', state: 'pending' },
          { id: 'r3', section: 'summary.mainPoints', itemId: 'a9993e36', state: 'pending' }, // 舊字串條目
          { id: 'r4', section: 'summary.risks', itemId: 'k9', state: 'pending' }, // 整區都沒了
          { id: 'r5', section: 'notes.outline', itemId: 'o1', state: 'pending' },
          { id: 'r6', section: 'notes.outline', itemId: 'o2', state: 'pending' },
          { id: 'r7', kind: 'term', section: 'transcript', state: 'pending' }, // 不指向條目
        ],
      },
    };
    expect(pendingReview(m).map((r) => r.id)).toEqual(['r1', 'r3', 'r5', 'r7']);
    expect(pendingCount(m)).toBe(4);
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
