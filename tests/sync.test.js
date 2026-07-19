import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mergeState, b64encodeUtf8, b64decodeUtf8, pull, setSyncConfig, clearSyncConfig } from '../js/sync.js';

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

  it('欄位級合併：翻譯/問答（動 updatedAt 但沒動 editedAt）不會蓋掉別台的真實編輯', () => {
    // A：改了逐字稿（editedAt=T2，較新），transcript 是「新版」
    const a = { meetings: [{ id: '1', createdAt: 1, editedAt: 200, updatedAt: 200, transcript: [{ speaker: 's', text: '修正後' }] }], deleted: [] };
    // B：在舊版上做了翻譯（updatedAt=300 更新，但 editedAt 舊=100），transcript 是「舊版」
    const b = { meetings: [{ id: '1', createdAt: 1, editedAt: 100, updatedAt: 300, transcript: [{ speaker: 's', text: '舊的' }], translations: { en: {} } }], deleted: [] };
    const m = mergeState(a, b);
    // 真實編輯（A 的新逐字稿）必須勝出，即使 B 的 updatedAt 較新
    expect(m.meetings[0].transcript[0].text).toBe('修正後');
  });

  it('欄位級合併：兩台各問一個問題 → chat 以 at 聯集，都保留', () => {
    const a = { meetings: [{ id: '1', createdAt: 1, editedAt: 100, chat: [{ at: 10, q: 'Q1', a: 'A1' }] }], deleted: [] };
    const b = { meetings: [{ id: '1', createdAt: 1, editedAt: 100, chat: [{ at: 20, q: 'Q2', a: 'A2' }] }], deleted: [] };
    const chat = mergeState(a, b).meetings[0].chat;
    expect(chat.map((c) => c.q)).toEqual(['Q1', 'Q2']);
  });

  it('id 白名單：雲端注入的惡意 id（含引號/空白）會被丟棄', () => {
    const evil = '" onerror="x';
    const m = mergeState(
      { meetings: [{ id: 'ok1', createdAt: 1 }], deleted: [] },
      { meetings: [{ id: evil, createdAt: 2 }], deleted: [] }
    );
    expect(m.meetings.map((x) => x.id)).toEqual(['ok1']);
  });
});

describe('pull（防資料清空）', () => {
  beforeEach(() => {
    global.localStorage = {
      _s: {},
      getItem(k) { return this._s[k] ?? null; },
      setItem(k, v) { this._s[k] = String(v); },
      removeItem(k) { delete this._s[k]; },
    };
    setSyncConfig({ token: 't', owner: 'o', repo: 'r', path: 'meetings.json' });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    clearSyncConfig();
  });
  const resp = (obj) => ({ ok: true, status: 200, json: async () => obj, text: async () => JSON.stringify(obj) });

  it('大檔（content 空、encoding=none）改用 raw media type 取內容，不會誤判成空', async () => {
    const doc = { meetings: [{ id: '1', createdAt: 1 }], deleted: [] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(resp({ content: '', encoding: 'none', sha: 'SHA1' })) // 第一次：大檔空 content
      .mockResolvedValueOnce({ ok: true, status: 200, text: async () => JSON.stringify(doc) }); // raw 重抓
    vi.stubGlobal('fetch', fetchMock);
    const r = await pull();
    expect(r.meetings ? r.meetings : r.doc.meetings).toBeDefined();
    expect(r.doc.meetings).toHaveLength(1);
    expect(r.sha).toBe('SHA1');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('內容壞掉（JSON 解析失敗）→ 丟錯中止，絕不 fallback 成空文件', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{壞掉的', json: async () => ({ content: '', encoding: 'none', sha: 'S' }) });
    vi.stubGlobal('fetch', fetchMock);
    await expect(pull()).rejects.toThrow(/解析失敗|中止/);
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
