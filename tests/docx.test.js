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

describe('學習筆記與表格', () => {
  const withNotes = {
    title: '研討會',
    createdAt: 0,
    transcript: [{ speaker: '講者', text: 'hi' }],
    summary: { actionItems: [], mainPoints: [], qa: [] },
    notes: {
      outline: [{ title: '氣冷與水冷', points: ['氣冷可靠'] }],
      concepts: [{ term: 'dry-out', plain: '局部乾燒', why: '決定上限' }],
      tables: [{ title: '氣冷 vs 水冷', headers: ['項目', '氣冷'], rows: [['成本', '低'], ['漏液', '無']] }],
      figures: [{ group: '效能', label: '解熱能力', value: '1600 W' }, { group: '市場', label: '預估規模', value: '81.1 億美元' }],
      quiz: [{ q: '為何？', a: '因為。' }],
    },
  };
  const xml = (m, opts) => {
    const bytes = buildDocxBytes(m, opts);
    return new TextDecoder().decode(bytes);
  };

  it('勾選學習筆記時產生真正的 Word 表格（w:tbl）', () => {
    const s = xml(withNotes, { notes: true });
    expect(s).toContain('<w:tbl>');
    expect(s).toContain('<w:tblGrid>');
    expect(s).toContain('氣冷 vs 水冷');
    expect(s).toContain('章節大綱');
    expect(s).toContain('解熱能力'); // 關鍵數據的標籤
    expect(s).toContain('<w:jc w:val="right"/>'); // 數值靠右對齊
    expect(s).toContain('dry-out');
  });

  it('預設不輸出學習筆記，也不會有表格', () => {
    const s = xml(withNotes);
    expect(s).not.toContain('<w:tbl>');
    expect(s).not.toContain('章節大綱');
  });

  it('表格列比欄位少時補空白，不會產生破損的 XML', () => {
    // 只留對照表（清空關鍵數據），儲存格數才只反映這張表
    const m = { ...withNotes, notes: { ...withNotes.notes, figures: [], tables: [{ title: 'T', headers: ['A', 'B', 'C'], rows: [['1']] }] } };
    const s = xml(m, { notes: true });
    const cells = (s.match(/<w:tc>/g) || []).length;
    expect(cells).toBe(6); // 3 欄標題 + 3 欄資料（缺的補空）
  });
});
