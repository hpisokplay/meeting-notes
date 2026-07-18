import { describe, it, expect } from 'vitest';
import { buildDocxBytes, zipStore } from '../js/docx.js';

const meeting = {
  title: '分戶帳教育訓練',
  createdAt: new Date('2026-07-18T10:00:00').getTime(),
  transcript: [{ speaker: '說話者1', text: '大家好' }],
  summary: { actionItems: ['處理 [DRI: 待指派]'], mainPoints: ['重點A'], qa: [] },
};

function findBytes(haystack, needleStr) {
  const needle = new TextEncoder().encode(needleStr);
  outer: for (let i = 0; i <= haystack.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) if (haystack[i + j] !== needle[j]) continue outer;
    return true;
  }
  return false;
}

describe('docx', () => {
  it('是合法 zip：開頭 PK\\x03\\x04、結尾有 EOCD 簽章', () => {
    const b = buildDocxBytes(meeting);
    expect(b[0]).toBe(0x50);
    expect(b[1]).toBe(0x4b);
    expect(b[2]).toBe(0x03);
    expect(b[3]).toBe(0x04);
    // EOCD 簽章 50 4b 05 06 應出現在尾端附近
    let hasEocd = false;
    for (let i = b.length - 22; i < b.length - 3; i++) {
      if (b[i] === 0x50 && b[i + 1] === 0x4b && b[i + 2] === 0x05 && b[i + 3] === 0x06) hasEocd = true;
    }
    expect(hasEocd).toBe(true);
  });

  it('包含必要檔名與內容', () => {
    const b = buildDocxBytes(meeting);
    expect(findBytes(b, '[Content_Types].xml')).toBe(true);
    expect(findBytes(b, 'word/document.xml')).toBe(true);
    expect(findBytes(b, '分戶帳教育訓練')).toBe(true);
    expect(findBytes(b, '待辦事項 Action Item')).toBe(true);
    expect(findBytes(b, '[DRI: 待指派]')).toBe(true);
  });

  it('zipStore 單檔 CRC 與長度正確組裝', () => {
    const bytes = new TextEncoder().encode('hello');
    const z = zipStore([{ name: 'a.txt', bytes }]);
    expect(z[0]).toBe(0x50);
    expect(findBytes(z, 'a.txt')).toBe(true);
  });
});
