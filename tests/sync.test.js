import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mergeState, stripForCloud, b64encodeUtf8, b64decodeUtf8, pull, setSyncConfig, clearSyncConfig } from '../js/sync.js';

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

  // 專有名詞的草稿是「只存本機、不上雲」的暫存資料。mergeMeeting 原本只欄位級合併
  // chat 與 translations，terms 整包由 editStamp 較新的一邊決定 → 只要雲端那份較新
  // （例如另一台裝置動過），同步就會把本機做到一半的草稿整包蓋掉、按鈕跟著消失。
  it('欄位級合併：terms 草稿不會被另一邊的版本蓋掉', () => {
    const local = {
      meetings: [{ id: 'a', createdAt: 1, updatedAt: 5, editedAt: 5,
        terms: { items: [{ t: '泰昇科技', cat: 'org', draft: '鈦昇科技' }, { t: 'EMIB', cat: 'term' }] } }],
      deleted: [],
    };
    // 雲端那份較新（另一台裝置動過），但沒有本機的草稿
    const remote = {
      meetings: [{ id: 'a', createdAt: 1, updatedAt: 9, editedAt: 9,
        terms: { items: [{ t: '泰昇科技', cat: 'org' }, { t: 'EMIB', cat: 'term' }] } }],
      deleted: [],
    };
    const m = mergeState(local, remote).meetings[0];
    const t = m.terms.items.find((x) => x.t === '泰昇科技');
    expect(t.draft).toBe('鈦昇科技');
  });

  it('欄位級合併：已套用的訂正兩邊互補，不會遺失', () => {
    const local = {
      meetings: [{ id: 'a', createdAt: 1, updatedAt: 9, editedAt: 9,
        terms: { items: [{ t: 'AAA', applied: 'A1' }] } }],
      deleted: [],
    };
    const remote = {
      meetings: [{ id: 'a', createdAt: 1, updatedAt: 5, editedAt: 5,
        terms: { items: [{ t: 'AAA', applied: 'A1' }, { t: 'BBB', applied: 'B1' }] } }],
      deleted: [],
    };
    const items = mergeState(local, remote).meetings[0].terms.items;
    expect(items.map((x) => x.t).sort()).toEqual(['AAA', 'BBB']);
    expect(items.find((x) => x.t === 'BBB').applied).toBe('B1');
  });

  it('欄位級合併：一邊完全沒有 terms 時，另一邊的完整保留', () => {
    const local = { meetings: [{ id: 'a', createdAt: 1, updatedAt: 9, editedAt: 9 }], deleted: [] };
    const remote = {
      meetings: [{ id: 'a', createdAt: 1, updatedAt: 5, editedAt: 5, terms: { items: [{ t: 'X', draft: 'Y' }] } }],
      deleted: [],
    };
    const m = mergeState(local, remote).meetings[0];
    expect(m.terms.items[0].draft).toBe('Y');
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

  it('墓碑 TTL：超過 180 天且有時間戳的墓碑會被清掉，沒時間戳的保留', () => {
    const now = 1_000_000_000_000;
    const old = now - 200 * 24 * 3600 * 1000; // 200 天前
    const recent = now - 10 * 24 * 3600 * 1000; // 10 天前
    const m = mergeState(
      { meetings: [], deleted: ['expired', 'fresh', 'legacy'], deletedAt: { expired: old, fresh: recent } },
      { meetings: [], deleted: [] },
      now
    );
    expect(m.deleted).toContain('fresh'); // 10 天 → 保留
    expect(m.deleted).toContain('legacy'); // 無時間戳 → 保守保留
    expect(m.deleted).not.toContain('expired'); // 200 天 → 清掉
    expect(m.deletedAt.expired).toBeUndefined();
  });

  it('stripForCloud：上雲前移除翻譯（本機物件不受影響）', () => {
    const doc = { meetings: [{ id: '1', createdAt: 1, transcript: [], translations: { en: { transcript: [] } } }], deleted: [] };
    const stripped = stripForCloud(doc);
    expect(stripped.meetings[0].translations).toBeUndefined();
    expect(doc.meetings[0].translations).toBeDefined(); // 原物件保留
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

  // GitHub 的 contents 回應帶 cache-control: max-age=60 但「沒有 Vary: Accept」，
  // 瀏覽器因此把兩次請求視為同一份快取 → raw 重抓會拿到第一次的 metadata（沒有 meetings）
  // → 誤判成「雲端資料格式異常」。兩次請求都必須繞過 HTTP 快取。
  it('兩次請求都要繞過 HTTP 快取，raw 重抓還要有不同的網址（否則會拿到快取的 metadata）', async () => {
    const doc = { meetings: [{ id: '1', createdAt: 1 }], deleted: [] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(resp({ content: '', encoding: 'none', sha: 'SHA1' }))
      .mockResolvedValueOnce({ ok: true, status: 200, text: async () => JSON.stringify(doc) });
    vi.stubGlobal('fetch', fetchMock);
    await pull();
    expect(fetchMock.mock.calls[0][1].cache).toBe('no-store');
    expect(fetchMock.mock.calls[1][1].cache).toBe('no-store');
    // raw 重抓的網址必須與第一次不同，快取才不可能命中
    expect(fetchMock.mock.calls[1][0]).not.toBe(fetchMock.mock.calls[0][0]);
  });

  it('raw 重抓拿到 metadata（快取污染的徵狀）→ 丟出看得懂的錯誤，不是含糊的格式異常', async () => {
    const meta = { name: 'meetings.json', path: 'meetings.json', sha: 'S', size: 1234567, content: '', encoding: 'none' };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(resp({ content: '', encoding: 'none', sha: 'S' }))
      .mockResolvedValueOnce({ ok: true, status: 200, text: async () => JSON.stringify(meta) });
    vi.stubGlobal('fetch', fetchMock);
    await expect(pull()).rejects.toThrow(/快取/);
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

// 學習筆記與 terms 一樣，必須做欄位級合併：
// 若整包由 editStamp 較新的一邊決定，剛產生好的筆記會被另一邊的舊版本蓋掉。
describe('mergeState：學習筆記', () => {
  const withNotes = (id, stamp, notes) => ({ id, createdAt: 1, updatedAt: stamp, editedAt: stamp, notes });
  const N = (title) => ({ outline: [{ title, anchor: '', points: ['p'] }], concepts: [], tables: [], figures: [], quiz: [] });

  it('一邊有筆記、另一邊沒有 → 筆記保留（不論哪邊較新）', () => {
    const local = { meetings: [withNotes('a', 5, N('本機筆記'))], deleted: [] };
    const remote = { meetings: [{ id: 'a', createdAt: 1, updatedAt: 9, editedAt: 9 }], deleted: [] };
    const m = mergeState(local, remote).meetings[0];
    expect(m.notes.outline[0].title).toBe('本機筆記');
  });

  it('兩邊都有筆記 → 取 editStamp 較新的那份（內容整份一致，不半新半舊）', () => {
    const local = { meetings: [withNotes('a', 5, N('舊'))], deleted: [] };
    const remote = { meetings: [withNotes('a', 9, N('新'))], deleted: [] };
    const m = mergeState(local, remote).meetings[0];
    expect(m.notes.outline[0].title).toBe('新');
  });

  it('兩邊都沒有筆記 → 不會憑空生出 notes 欄位', () => {
    const local = { meetings: [{ id: 'a', createdAt: 1, updatedAt: 5 }], deleted: [] };
    const remote = { meetings: [{ id: 'a', createdAt: 1, updatedAt: 9 }], deleted: [] };
    expect(mergeState(local, remote).meetings[0].notes).toBeUndefined();
  });
});
