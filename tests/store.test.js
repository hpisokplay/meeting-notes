import 'fake-indexeddb/auto';
import { describe, it, expect } from 'vitest';
import { save, get, list, remove, exportAll, saveJob, getActiveJob, clearJob, applyMerged } from '../js/store.js';
import { mergeMeeting } from '../js/sync.js';

function make(id, createdAt, title) {
  return {
    id,
    createdAt,
    title,
    transcript: [{ speaker: '說話者1', text: '大家好' }],
    summary: { keyPoints: ['重點一'], actionItems: [], decisions: ['決議一'] },
  };
}

describe('store', () => {
  it('存取與清單依時間排序（新到舊）', async () => {
    await save(make('a', 1000, '第一場'));
    await save(make('b', 3000, '第二場'));
    await save(make('c', 2000, '第三場'));

    const one = await get('a');
    expect(one.title).toBe('第一場');
    expect(one.transcript[0].speaker).toBe('說話者1');

    const all = await list();
    expect(all.map((m) => m.id)).toEqual(['b', 'c', 'a']);
  });

  it('刪除', async () => {
    await save(make('x', 1, 'X'));
    await remove('x');
    expect(await get('x')).toBeNull();
  });

  it('匯出為 JSON 字串含 meetings 陣列', async () => {
    await save(make('e', 5, 'E'));
    const json = await exportAll();
    const parsed = JSON.parse(json);
    expect(Array.isArray(parsed.meetings)).toBe(true);
    expect(parsed.meetings.some((m) => m.id === 'e')).toBe(true);
  });

  it('匯出含分類群組與刪除墓碑（完整備份可還原）', async () => {
    const json = await exportAll({ groups: [{ id: 'g1', name: '客戶' }], groupsDeleted: ['gx'] });
    const parsed = JSON.parse(json);
    expect(parsed.groups).toEqual([{ id: 'g1', name: '客戶' }]);
    expect(Array.isArray(parsed.deleted)).toBe(true);
    expect(parsed.groupsDeleted).toEqual(['gx']);
  });

  it('續傳任務：存取未完成任務、完成後清除', async () => {
    await saveJob({ id: 'active', done: false, windows: [{ segments: null }], fileUri: 'u' });
    const job = await getActiveJob();
    expect(job.id).toBe('active');
    expect(job.fileUri).toBe('u');
    await clearJob('active');
    expect(await getActiveJob()).toBeNull();
  });
});

// 同步是「讀快照 → 合併 → 整批寫回」，寫回時若直接覆蓋，
// 同步期間才存進來的本機變更（例如專有名詞草稿）就會被舊快照吃掉。
describe('applyMerged 不吃掉同步期間的本機變更', () => {
  it('寫回時與當下的本機版本合併，草稿得以保留', async () => {
    await save({
      id: 'race1',
      createdAt: 1,
      updatedAt: 100,
      editedAt: 50,
      terms: { items: [{ t: '泰昇科技', cat: 'org', draft: '鈦昇科技' }] },
    });
    // 同步快照＝草稿存進來「之前」的版本
    const snapshot = {
      meetings: [{ id: 'race1', createdAt: 1, updatedAt: 90, editedAt: 50, terms: { items: [{ t: '泰昇科技', cat: 'org' }] } }],
      deleted: [],
    };
    await applyMerged(snapshot, mergeMeeting);
    const got = await get('race1');
    expect(got.terms.items[0].draft).toBe('鈦昇科技');
  });

  it('沒有給合併函式時維持原本的覆寫行為', async () => {
    await save({ id: 'race2', createdAt: 1, updatedAt: 100, title: '本機' });
    await applyMerged({ meetings: [{ id: 'race2', createdAt: 1, updatedAt: 90, title: '快照' }], deleted: [] });
    expect((await get('race2')).title).toBe('快照');
  });
});
