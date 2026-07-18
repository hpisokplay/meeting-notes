import { describe, it, expect } from 'vitest';
import { formatDate, defaultTitle, transcriptToText } from '../js/format.js';

describe('format', () => {
  it('formatDate 回傳 YYYY-MM-DD HH:mm', () => {
    const ts = new Date('2026-07-18T09:05:00').getTime();
    expect(formatDate(ts)).toBe('2026-07-18 09:05');
  });

  it('defaultTitle 用檔名去副檔名', () => {
    expect(defaultTitle('週會 2026.m4a', 0)).toBe('週會 2026');
  });

  it('defaultTitle 檔名為空時用日期', () => {
    const ts = new Date('2026-07-18T09:05:00').getTime();
    expect(defaultTitle('', ts)).toBe('會議 2026-07-18 09:05');
  });

  it('transcriptToText 攤平語者分段', () => {
    const segs = [
      { speaker: '說話者1', text: '你好' },
      { speaker: '說話者2', text: '你也好' },
    ];
    expect(transcriptToText(segs)).toBe('說話者1：你好\n說話者2：你也好');
  });
});
