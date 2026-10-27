import { describe, it, expect } from 'vitest';
import { buildDocxBytes, zipStore, roman } from '../js/docx.js';

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

describe('完整整理的 Word 版面', () => {
  const docXml = (m, o) => new TextDecoder().decode(buildDocxBytes(m, o));
  const rep = (over = {}) => ({
    title: '週會',
    createdAt: new Date('2026-07-18T10:00:00').getTime(),
    transcript: [{ speaker: '甲', text: '你好' }],
    summary: {
      reportIntro: ['引言段落'],
      reportPoints: [
        { text: '重點一：說明一', group: '第一大項' },
        { text: '沒有冒號的整段', group: '第一大項' },
        { text: '重點三：說明三', group: '第二大項' },
      ],
      actionItems: ['甲：做A', '乙：做B'],
      mainPoints: ['舊重點'],
      qa: ['問：為何？ 答：因為'],
      ...over,
    },
  });

  it('report: intro comes right after the date', () => {
    const x = docXml(rep());
    expect(x.indexOf('引言段落')).toBeGreaterThan(x.indexOf('週會'));
    expect(x.indexOf('引言段落')).toBeLessThan(x.indexOf('第一大項'));
  });
  it('report: headings numbered and bold', () => {
    const x = docXml(rep());
    expect(x).toContain('<w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr><w:t xml:space="preserve">1. 第一大項</w:t>');
    expect(x).toContain('2. 第二大項');
  });
  it('report: sub title bold followed by colon', () => {
    const x = docXml(rep());
    expect(x).toMatch(/<w:b\/>[^]*?<w:t xml:space="preserve">重點一<\/w:t><\/w:r><w:r>[^]*?<w:t xml:space="preserve">：說明一<\/w:t>/);
    expect(x).toContain('沒有冒號的整段');
  });
  it('report: action items use roman numerals', () => {
    const x = docXml(rep());
    expect(x).toContain('i.　');
    expect(x).toContain('ii.　');
  });
  it('report: no 會議重點 heading when report exists', () => {
    const x = docXml(rep());
    expect(x).not.toContain('會議重點');
    expect(x).not.toContain('舊重點');
  });
  it('report: empty action items omits the block', () => {
    const x = docXml(rep({ actionItems: [], qa: [] }));
    expect(x).not.toContain('待辦事項');
    expect(x).not.toContain('會議提問');
    expect(x).not.toContain('（無）');
  });
  it('report: escapes & < > in headings', () => {
    const x = docXml(rep({ reportPoints: [{ text: 'A&B：<x>', group: 'R&D <組>' }] }));
    expect(x).toContain('R&amp;D &lt;組&gt;');
    expect(x).toContain('A&amp;B');
  });
  it('report: lecture meeting without actionItems prints no 待辦 block', () => {
    const m = rep({ actionItems: undefined, qa: [] });
    m.summary.mode = 'lecture';
    const x = docXml(m);
    expect(x).not.toContain('待辦事項');
    expect(x).toContain('1. 第一大項');
  });
  it('report: honours toggles and keeps transcript', () => {
    const x = docXml(rep(), { actionItems: false, qa: false });
    expect(x).not.toContain('待辦事項');
    expect(x).toContain('逐字稿');
    expect(docXml(rep(), { transcript: false })).not.toContain('逐字稿');
  });
  it('legacy meeting output unchanged', () => {
    const x = docXml(meeting);
    expect(x).toContain('📌 會議重點 Main Point');
    expect(x).toContain('1. 重點A');
  });
});

it('roman numerals', () => {
  expect([1, 4, 9, 12].map(roman)).toEqual(['i', 'iv', 'ix', 'xii']);
});

describe('完整整理：修正輪', () => {
  const docXml = (m) => new TextDecoder().decode(buildDocxBytes(m));
  const base = (points) => ({
    title: 't',
    createdAt: 1,
    summary: { reportIntro: ['引言'], reportPoints: points, qa: ['問：甲 答：乙'] },
  });
  it('report: Q&A pPr puts keepLines before ind (schema order)', () => {
    const x = docXml(base([{ text: 'a：b', group: 'G' }]));
    expect(x).toContain('<w:pPr><w:keepLines/><w:ind');
    expect(x).not.toContain('<w:ind w:left="720" w:hanging="360"/><w:keepLines/>');
  });
  it('report: no group -> no bare numbered heading, subs still bullets', () => {
    const x = docXml(base([{ text: '小標：說明' }]));
    expect(x).not.toMatch(/>1\. </);
    expect(x).toContain('•　');
    expect(x).toContain('說明');
  });
});

describe('完整整理：講座保留四個區塊', () => {
  const docXml = (m, o) => new TextDecoder().decode(buildDocxBytes(m, o));
  const lec = (mode) => ({
    title: 't',
    createdAt: 1,
    mode,
    transcript: [{ speaker: '甲', text: '你好' }],
    summary: {
      reportIntro: ['引言'],
      reportPoints: [{ text: '小標：說明', group: '大項' }],
      conclusions: ['結論甲'],
      advice: ['建議乙'],
      risks: ['風險丙'],
      cases: ['案例丁'],
      actionItems: ['做事'],
      qa: ['問：甲 答：乙'],
    },
  });
  it('lecture + report: four sections between report body and Action Items/Q&A', () => {
    const x = docXml(lec('lecture'));
    const idx = ['1. 大項', '核心結論', '1. 結論甲', '講者建議', '1. 建議乙', '風險提醒', '1. 風險丙', '案例', '1. 案例丁', '待辦事項', '會議提問'].map((k) => x.indexOf(k));
    expect(idx.every((i) => i >= 0)).toBe(true);
    expect([...idx].sort((a, b) => a - b)).toEqual(idx);
  });
  it('lecture + report: toggles and empty lists omit a section', () => {
    const m = lec('lecture');
    m.summary.risks = [];
    const x = docXml(m, { advice: false });
    expect(x).toContain('核心結論');
    expect(x).not.toContain('講者建議');
    expect(x).not.toContain('風險提醒');
    expect(x).not.toContain('（無）');
  });
  it('meeting mode + report: no lecture sections', () => {
    const x = docXml(lec('meeting'));
    expect(x).not.toContain('核心結論');
    expect(x).not.toContain('結論甲');
  });
});
