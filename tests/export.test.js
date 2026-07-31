import { describe, it, expect, vi } from 'vitest';
import { meetingToHtmlBody, fullHtmlDoc, safeFileName, splitQA, exportPdf } from '../js/export.js';

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

  it('Q&A 匯出：問答各自一段（問藍/答綠標籤）', () => {
    const html = meetingToHtmlBody({ title: 'x', createdAt: 0, transcript: [], summary: { qa: ['問：A？ 答：B。'] } });
    expect(html).toContain('問：</b>');
    expect(html).toContain('答：</b>');
    expect(html).toContain('#0a58ca'); // 問 藍
    expect(html).toContain('#1a7f37'); // 答 綠
  });

  it('逐字稿語者上色', () => {
    const html = meetingToHtmlBody({ title: 'x', createdAt: 0, transcript: [{ speaker: '說話者1', text: 'hi' }], summary: {} });
    expect(html).toMatch(/<strong style="color:#/);
  });

  it('可選擇不匯出待辦事項（該段標題與內容都不出現）', () => {
    const html = meetingToHtmlBody(meeting, { actionItems: false });
    expect(html).not.toContain('待辦事項');
    expect(html).not.toContain('處理上線 [DRI: 待指派]');
    // 其他段落不受影響
    expect(html).toContain('會議重點');
    expect(html).toContain('會議提問');
    expect(html).toContain('逐字稿');
  });

  it('可同時關閉多段，只留逐字稿', () => {
    const html = meetingToHtmlBody(meeting, { actionItems: false, mainPoints: false, qa: false });
    expect(html).not.toContain('待辦事項');
    expect(html).not.toContain('會議重點');
    expect(html).not.toContain('會議提問');
    expect(html).toContain('逐字稿');
    expect(html).toContain(meeting.title); // 標題與日期一律保留
  });

  it('未指定選項時四段全出（維持原行為）', () => {
    const html = meetingToHtmlBody(meeting);
    expect(html).toContain('待辦事項');
    expect(html).toContain('會議重點');
    expect(html).toContain('會議提問');
    expect(html).toContain('逐字稿');
  });

  it('列印期間把 document.title 換成會議名稱（另存 PDF 的檔名來源）', () => {
    document.title = 'DD會議紀錄';
    window.print = () => {};
    exportPdf(meeting);
    expect(document.title).toBe('產品週會');
  });

  it('afterprint 不可還原標題（iOS 會在真正存檔前就觸發，太早還原檔名就變回 App 標題）', () => {
    document.title = 'DD會議紀錄';
    window.print = () => {};
    exportPdf(meeting);
    window.dispatchEvent(new Event('afterprint'));
    expect(document.title).toBe('產品週會');
  });

  it('使用者回到 App 有操作後才還原標題', async () => {
    document.title = 'DD會議紀錄';
    window.print = () => {};
    exportPdf(meeting);
    document.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 5));
    expect(document.title).toBe('DD會議紀錄');
  });

  // iOS 主畫面 App（standalone）的列印名稱取自 manifest 的 App 名稱，不吃 document.title，
  // 所以改開一般 Safari 分頁列印；桌機維持就地列印（已驗證可用，不要動它）。
  it('iOS standalone：改開新分頁列印，不動原頁面的 document.title', () => {
    document.title = 'DD會議紀錄';
    const written = [];
    const fakeWin = { document: { write: (h) => written.push(h), close: () => {} } };
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(fakeWin);
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)');
    navigator.standalone = true;
    let printed = false;
    window.print = () => (printed = true);

    expect(exportPdf(meeting)).toBe('newtab');
    expect(openSpy).toHaveBeenCalled();
    expect(written.join('')).toContain('<title>產品週會</title>'); // 新分頁的標題＝檔名來源
    // iOS 開的新視窗沒有 Safari 工具列 → 這一頁必須自備列印與關閉，否則使用者會卡住
    expect(written.join('')).toContain('window.print()');
    expect(written.join('')).toContain('window.close()');
    expect(printed).toBe(false); // 不在原頁面列印
    expect(document.title).toBe('DD會議紀錄'); // 原頁面標題不受影響

    delete navigator.standalone;
    vi.restoreAllMocks();
  });

  it('iOS standalone 但新分頁被擋 → 退回原本的就地列印', () => {
    document.title = 'DD會議紀錄';
    vi.spyOn(window, 'open').mockReturnValue(null);
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)');
    navigator.standalone = true;
    let printed = false;
    window.print = () => (printed = true);

    expect(exportPdf(meeting)).not.toBe('newtab');
    expect(printed).toBe(true);
    expect(document.title).toBe('產品週會');

    delete navigator.standalone;
    vi.restoreAllMocks();
  });

  it('safeFileName 去除非法字元', () => {
    expect(safeFileName('客說會/2026:上線?')).toBe('客說會_2026_上線_');
  });

  it('HTML 特殊字元被跳脫', () => {
    const html = meetingToHtmlBody({ title: 'a<b>&c', createdAt: 0, transcript: [], summary: {} });
    expect(html).toContain('a&lt;b&gt;&amp;c');
  });
});
