import 'fake-indexeddb/auto';
import { describe, it, expect } from 'vitest';
import { cacheList, cachedList, cacheMeeting, cachedMeeting, uncacheMeeting } from '../js/store.js';

describe('store（本機快取）', () => {
  it('清單快取：存取並依建立時間新到舊排序', async () => {
    await cacheList([
      { id: 'a', createdAt: 1000, title: '一' },
      { id: 'b', createdAt: 3000, title: '二' },
      { id: 'c', createdAt: 2000, title: '三' },
    ]);
    expect((await cachedList()).map((m) => m.id)).toEqual(['b', 'c', 'a']);
  });

  it('完整會議快取：存取，沒有時回 null', async () => {
    await cacheMeeting({ id: 'x', title: 'X', transcript: [{ speaker: '說話者 1', text: '大家好' }] });
    expect((await cachedMeeting('x')).transcript[0].text).toBe('大家好');
    expect(await cachedMeeting('none')).toBeNull();
  });

  it('移除快取：會議與清單中的那一筆都拿掉', async () => {
    await cacheList([{ id: 'y', createdAt: 1 }, { id: 'z', createdAt: 2 }]);
    await cacheMeeting({ id: 'y', title: 'Y' });
    await uncacheMeeting('y');
    expect(await cachedMeeting('y')).toBeNull();
    expect((await cachedList()).map((m) => m.id)).toEqual(['z']);
  });
});
