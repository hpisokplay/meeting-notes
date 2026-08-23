import { describe, it, expect, vi, beforeEach } from 'vitest';
import { groqTranscribeBlob, groqTranscribeRange, planGroqSlices, getGroqKey, setGroqKey, hasGroqKey } from '../js/groq.js';

const jsonResponse = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('Groq 金鑰儲存', () => {
  it('存取與清除', () => {
    expect(hasGroqKey()).toBe(false);
    setGroqKey('  gsk_abc  ');
    expect(getGroqKey()).toBe('gsk_abc');
    expect(hasGroqKey()).toBe(true);
    setGroqKey('');
    expect(hasGroqKey()).toBe(false);
  });
});

describe('planGroqSlices：切片不可超過 25MB 上限', () => {
  // 假索引：frameCount 個音框，每框 sizeB bytes、1024 取樣 @16k（0.064 秒/框）
  const fakeIndex = (frameCount, sizeB) => {
    const sizes = new Uint32Array(frameCount).fill(sizeB);
    const offsets = new Float64Array(frameCount);
    const times = new Float64Array(frameCount);
    for (let i = 0; i < frameCount; i++) {
      offsets[i] = 100 + i * sizeB;
      times[i] = i * 1024;
    }
    return { timescale: 16000, sampleRate: 16000, frameCount, sizes, offsets, times, durationSec: (frameCount * 1024) / 16000 };
  };

  it('小範圍 → 一片', () => {
    const idx = fakeIndex(1000, 500);
    const slices = planGroqSlices(idx, 0, idx.durationSec);
    expect(slices.length).toBe(1);
    expect(slices[0]).toEqual({ from: 0, to: 1000 });
  });

  it('超過 20 分鐘 → 依時間切開', () => {
    // 0.064 秒/框 → 30 分鐘約 28,125 框，每框 100B（bytes 不是瓶頸）
    const idx = fakeIndex(28125, 100);
    const slices = planGroqSlices(idx, 0, idx.durationSec);
    expect(slices.length).toBe(2); // 20 分 + 10 分
    // 每片都涵蓋、無縫、無重疊
    expect(slices[0].from).toBe(0);
    expect(slices[slices.length - 1].to).toBe(28125);
    for (let i = 1; i < slices.length; i++) expect(slices[i].from).toBe(slices[i - 1].to);
  });

  it('位元組超限 → 依大小切開，每片含 ADTS 標頭後 ≤ 23MB', () => {
    // 每框 8KB → 3000 框 ≈ 24MB，必須切
    const idx = fakeIndex(3000, 8 * 1024);
    const slices = planGroqSlices(idx, 0, idx.durationSec);
    expect(slices.length).toBeGreaterThan(1);
    for (const s of slices) {
      let bytes = 0;
      for (let i = s.from; i < s.to; i++) bytes += 7 + idx.sizes[i];
      expect(bytes).toBeLessThanOrEqual(23 * 1024 * 1024);
    }
  });

  it('指定中段範圍 → 只涵蓋該範圍', () => {
    const idx = fakeIndex(10000, 100);
    const slices = planGroqSlices(idx, 60, 120);
    const from = slices[0].from;
    const to = slices[slices.length - 1].to;
    expect(idx.times[from] / idx.timescale).toBeCloseTo(60, 0);
    expect(idx.times[to - 1] / idx.timescale).toBeLessThan(120);
  });
});

describe('groqTranscribeBlob', () => {
  it('verbose_json 的 segments 轉成 App 的格式（統一標「說話者」）', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse({ segments: [{ start: 0.4, text: ' 大家好 ' }, { start: 3.9, text: '今天讨论预算' }, { start: 8, text: '   ' }] })
    );
    vi.stubGlobal('fetch', fetchMock);
    const segs = await groqTranscribeBlob(new Blob(['x']), 'gsk_test');
    expect(segs).toEqual([
      { speaker: '說話者', text: '大家好', t: 0 },
      { speaker: '說話者', text: '今天讨论预算', t: 4 },
    ]);
    // 請求要件：multipart、模型、中文、verbose_json
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('api.groq.com');
    expect(init.headers.Authorization).toBe('Bearer gsk_test');
    const form = init.body;
    expect(form.get('model')).toBe('whisper-large-v3-turbo');
    expect(form.get('language')).toBe('zh');
    expect(form.get('response_format')).toBe('verbose_json');
  });

  it('401 → 明確講金鑰無效', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"error":{}}', { status: 401 })));
    await expect(groqTranscribeBlob(new Blob(['x']), 'bad')).rejects.toThrow(/金鑰無效/);
  });

  it('429 → 依 retry-after 等待後重試成功', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('rate limit', { status: 429, headers: { 'retry-after': '1' } }))
      .mockResolvedValueOnce(jsonResponse({ segments: [{ start: 0, text: 'ok' }] }));
    vi.stubGlobal('fetch', fetchMock);
    const p = groqTranscribeBlob(new Blob(['x']), 'gsk');
    await vi.advanceTimersByTimeAsync(2500);
    const segs = await p;
    expect(segs[0].text).toBe('ok');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });
});

describe('groqTranscribeRange', () => {
  it('多片時每片的 t 要加上片與段的時間差', async () => {
    // 兩片：0–20 分、20–30 分。第二片回的 t=5 → 相對整段是 20*60+5
    const frameCount = 28125;
    const sizes = new Uint32Array(frameCount).fill(100);
    const offsets = new Float64Array(frameCount);
    const times = new Float64Array(frameCount);
    for (let i = 0; i < frameCount; i++) {
      offsets[i] = i * 100;
      times[i] = i * 1024;
    }
    const index = {
      timescale: 16000, sampleRate: 16000, frameCount, sizes, offsets, times,
      durationSec: (frameCount * 1024) / 16000,
      cfg: { objType: 2, freqIdx: 8, chCfg: 1 },
    };
    const bytes = new Uint8Array(frameCount * 100);
    const file = {
      size: bytes.length,
      slice(a, b) {
        const part = bytes.slice(a, b);
        return { arrayBuffer: async () => part.buffer };
      },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ segments: [{ start: 2, text: '第一片' }] }))
      .mockResolvedValueOnce(jsonResponse({ segments: [{ start: 5, text: '第二片' }] }));
    vi.stubGlobal('fetch', fetchMock);
    const segs = await groqTranscribeRange(file, index, 0, index.durationSec, 'gsk');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(segs[0]).toEqual({ speaker: '說話者', text: '第一片', t: 2 });
    expect(segs[1].text).toBe('第二片');
    expect(segs[1].t).toBeGreaterThanOrEqual(20 * 60); // 加上了第二片的起始偏移
  });
});
