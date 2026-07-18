import { describe, it, expect } from 'vitest';
import { meetingToHtmlBody, fullHtmlDoc, safeFileName } from '../js/export.js';

const meeting = {
  id: '1',
  title: '產品週會',
  createdAt: new Date('2026-07-18T10:00:00').getTime(),
  transcript: [
    { speaker: '說話者1', text: '討論上線時程' },
    { speaker: '說話者2', text: '下週三' },
  ],
  summary: {
    keyPoints: ['重點A'],
    actionItems: ['待辦B'],
    decisions: ['決議C'],
  },
};

describe('export', () => {
  it('meetingToHtmlBody 含標題、語者、摘要三區', () => {
    const html = meetingToHtmlBody(meeting);
    expect(html).toContain('產品週會');
    expect(html).toContain('說話者1');
    expect(html).toContain('說話者2');
    expect(html).toContain('重點條列');
    expect(html).toContain('待辦事項');
    expect(html).toContain('決議事項');
    expect(html).toContain('重點A');
  });

  it('空摘要顯示（無）', () => {
    const html = meetingToHtmlBody({ title: 'x', createdAt: 0, transcript: [], summary: {} });
    expect(html).toContain('（無）');
    expect(html).toContain('（無逐字稿）');
  });

  it('fullHtmlDoc 是完整 HTML 文件', () => {
    const doc = fullHtmlDoc(meeting);
    expect(doc.startsWith('<!doctype html>')).toBe(true);
    expect(doc).toContain('<title>產品週會</title>');
  });

  it('safeFileName 去除非法字元', () => {
    expect(safeFileName('客說會/2026:上線?')).toBe('客說會_2026_上線_');
  });

  it('HTML 特殊字元被跳脫', () => {
    const html = meetingToHtmlBody({ title: 'a<b>&c', createdAt: 0, transcript: [], summary: {} });
    expect(html).toContain('a&lt;b&gt;&amp;c');
  });
});
