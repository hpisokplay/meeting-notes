import { describe, it, expect } from 'vitest';
import { meetingToHtmlBody, fullHtmlDoc, safeFileName, splitQA } from '../js/export.js';

const meeting = {
  id: '1',
  title: '產品週會',
  createdAt: new Date('2026-07-18T10:00:00').getTime(),
  transcript: [
    { speaker: '說話者1', text: '討論上線時程' },
    { speaker: '說話者2', text: '下週三' },
  ],
  summary: {
    actionItems: ['處理上線 [DRI: 待指派]'],
    mainPoints: ['重點A'],
    qa: ['問：何時上線 答：下週三'],
  },
};

describe('export', () => {
  it('meetingToHtmlBody 含標題、語者、四段（待辦/重點/Q&A/逐字稿）', () => {
    const html = meetingToHtmlBody(meeting);
    expect(html).toContain('產品週會');
    expect(html).toContain('說話者1');
    expect(html).toContain('說話者2');
    expect(html).toContain('待辦事項 Action Item');
    expect(html).toContain('會議重點 Main Point');
    expect(html).toContain('會議提問');
    expect(html).toContain('逐字稿 Transcribe');
    expect(html).toContain('[DRI: 待指派]');
    expect(html).toContain('<ol>'); // 編號清單
  });

  it('空摘要：待辦/重點顯示（無）、Q&A 顯示無', () => {
    const html = meetingToHtmlBody({ title: 'x', createdAt: 0, transcript: [], summary: {} });
    expect(html).toContain('（無）');
    expect(html).toContain('（無逐字稿）');
  });

  it('fullHtmlDoc 是完整 HTML 文件', () => {
    const doc = fullHtmlDoc(meeting);
    expect(doc.startsWith('<!doctype html>')).toBe(true);
    expect(doc).toContain('<title>產品週會</title>');
  });

  it('splitQA 把問答拆成兩段', () => {
    expect(splitQA('問：銅為什麼不能一次成型？ 答：因為要二次焊接。')).toEqual({
      q: '銅為什麼不能一次成型？',
      a: '因為要二次焊接。',
    });
    expect(splitQA('沒有答案的句子')).toEqual({ q: '沒有答案的句子', a: '' });
  });

  it('Q&A 匯出：問答各自一段（含 答：）', () => {
    const html = meetingToHtmlBody({ title: 'x', createdAt: 0, transcript: [], summary: { qa: ['問：A？ 答：B。'] } });
    expect(html).toContain('<b>問：</b>');
    expect(html).toContain('<b>答：</b>');
  });

  it('逐字稿語者上色', () => {
    const html = meetingToHtmlBody({ title: 'x', createdAt: 0, transcript: [{ speaker: '說話者1', text: 'hi' }], summary: {} });
    expect(html).toMatch(/<strong style="color:#/);
  });

  it('safeFileName 去除非法字元', () => {
    expect(safeFileName('客說會/2026:上線?')).toBe('客說會_2026_上線_');
  });

  it('HTML 特殊字元被跳脫', () => {
    const html = meetingToHtmlBody({ title: 'a<b>&c', createdAt: 0, transcript: [], summary: {} });
    expect(html).toContain('a&lt;b&gt;&amp;c');
  });
});
