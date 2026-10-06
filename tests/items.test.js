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
