import { describe, it, expect } from 'vitest';
import { mergeState, b64encodeUtf8, b64decodeUtf8 } from '../js/sync.js';

describe('mergeState', () => {
  it('聯集兩邊會議、以 updatedAt 較新者為準', () => {
    const a = { meetings: [{ id: '1', createdAt: 1, updatedAt: 10, title: '舊' }], deleted: [] };
    const b = { meetings: [{ id: '1', createdAt: 1, updatedAt: 20, title: '新' }, { id: '2', createdAt: 2 }], deleted: [] };
    const m = mergeState(a, b);
    expect(m.meetings.find((x) => x.id === '1').title).toBe('新');
    expect(m.meetings.map((x) => x.id).sort()).toEqual(['1', '2']);
  });

  it('墓碑聯集，且被刪除的記錄不出現', () => {
    const a = { meetings: [{ id: '1', createdAt: 1 }], deleted: ['1'] };
    const b = { meetings: [{ id: '1', createdAt: 1 }, { id: '2', createdAt: 2 }], deleted: [] };
    const m = mergeState(a, b);
    expect(m.deleted).toContain('1');
    expect(m.meetings.map((x) => x.id)).toEqual(['2']);
  });

  it('依 createdAt 由新到舊排序', () => {
    const a = { meetings: [{ id: 'x', createdAt: 100 }, { id: 'y', createdAt: 300 }, { id: 'z', createdAt: 200 }], deleted: [] };
    expect(mergeState(a, null).meetings.map((m) => m.id)).toEqual(['y', 'z', 'x']);
  });

  it('分類群組：聯集＋updatedAt 較新者勝＋墓碑刪除', () => {
    const a = {
      meetings: [], deleted: [],
      groups: [{ id: 'g1', name: '舊名', createdAt: 1, updatedAt: 10 }, { id: 'g2', name: '要刪', createdAt: 2, updatedAt: 5 }],
      groupsDeleted: [],
    };
    const b = {
      meetings: [], deleted: [],
      groups: [{ id: 'g1', name: '新名', createdAt: 1, updatedAt: 20 }, { id: 'g3', name: '另一組', createdAt: 3, updatedAt: 5 }],
      groupsDeleted: ['g2'],
    };
    const m = mergeState(a, b);
    expect(m.groups.find((g) => g.id === 'g1').name).toBe('新名');
    expect(m.groups.map((g) => g.id).sort()).toEqual(['g1', 'g3']);
    expect(m.groupsDeleted).toContain('g2');
  });

  it('舊格式文件（沒有 groups 欄位）也能合併', () => {
    const m = mergeState({ meetings: [], deleted: [] }, { meetings: [{ id: '1', createdAt: 1 }], deleted: [] });
    expect(m.groups).toEqual([]);
    expect(m.groupsDeleted).toEqual([]);
    expect(m.meetings).toHaveLength(1);
  });
});

describe('base64 UTF-8', () => {
  it('中文往返正確', () => {
    const s = JSON.stringify({ 逐字稿: '說話者1：大家好，這是測試 😀 English mix' });
    expect(b64decodeUtf8(b64encodeUtf8(s))).toBe(s);
  });

  it('可容忍含換行的 base64（GitHub 回傳格式）', () => {
    const enc = b64encodeUtf8('哈囉世界');
    const withNewlines = enc.replace(/(.{4})/g, '$1\n');
    expect(b64decodeUtf8(withNewlines)).toBe('哈囉世界');
  });
});
