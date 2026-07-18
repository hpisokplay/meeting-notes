import { describe, it, expect, vi, beforeEach } from 'vitest';
import { transcribeAndSummarize, pickModel, regenerateSummary, isTransientStatus } from '../js/gemini.js';

beforeEach(() => {
  vi.restoreAllMocks();
});

const MODELS_RESPONSE = {
  models: [
    { name: 'models/embedding-001', supportedGenerationMethods: ['embedContent'] },
    { name: 'models/gemini-2.5-flash', supportedGenerationMethods: ['generateContent'] },
    { name: 'models/gemini-3.5-flash', supportedGenerationMethods: ['generateContent'] },
    { name: 'models/gemini-3.1-pro', supportedGenerationMethods: ['generateContent'] },
    { name: 'models/gemini-3.5-flash-image', supportedGenerationMethods: ['generateContent'] },
  ],
};

describe('isTransientStatus', () => {
  it('5xx / 429 視為暫時性可重試', () => {
    expect(isTransientStatus(503)).toBe(true);
    expect(isTransientStatus(429)).toBe(true);
    expect(isTransientStatus(400)).toBe(false);
    expect(isTransientStatus(404)).toBe(false);
  });
});

describe('regenerateSummary', () => {
  it('只打文字（ListModels + generate 共 2 次），回傳三類', async () => {
    const modelJson = {
      candidates: [
        {
          content: {
            parts: [{ text: JSON.stringify({ actionItems: ['x [DRI: 待指派]'], mainPoints: ['重點'], qa: [] }) }],
          },
        },
      ],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(jsonResponse(modelJson));
    vi.stubGlobal('fetch', fetchMock);
    const r = await regenerateSummary([{ speaker: '說話者1', text: '哈囉' }], 'KEY');
    expect(r.actionItems).toEqual(['x [DRI: 待指派]']);
    expect(r.mainPoints).toEqual(['重點']);
    expect(r.qa).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('pickModel', () => {
  it('挑最新的 flash（非 lite、非 image）', () => {
    expect(pickModel(MODELS_RESPONSE.models)).toBe('gemini-3.5-flash');
  });
  it('沒有可用型號時回傳 null', () => {
    expect(pickModel([{ name: 'models/embedding-001', supportedGenerationMethods: ['embedContent'] }])).toBeNull();
  });
});

function jsonResponse(obj, headers = {}) {
  return {
    ok: true,
    status: 200,
    headers: { get: (h) => headers[h] || null },
    json: async () => obj,
    text: async () => JSON.stringify(obj),
  };
}

// 上傳位元組改用 XHR，測試時以假 XHR 模擬（立即回傳 ACTIVE 檔案）
function stubXHR(fileObj) {
  const file = fileObj || { uri: 'https://files/abc', name: 'files/abc', state: 'ACTIVE', mimeType: 'audio/mp4' };
  class MockXHR {
    constructor() {
      this.upload = {};
      this.status = 200;
      this.responseText = JSON.stringify({ file });
    }
    open() {}
    setRequestHeader() {}
    send() {
      if (this.upload.onprogress) this.upload.onprogress({ lengthComputable: true, loaded: 3, total: 3 });
      if (this.onload) this.onload();
    }
  }
  vi.stubGlobal('XMLHttpRequest', MockXHR);
}

describe('gemini', () => {
  it('沒有金鑰時丟錯', async () => {
    await expect(transcribeAndSummarize(new Blob(['x']), '')).rejects.toThrow('金鑰');
  });

  it('happy path：上傳→ACTIVE→產生語者分段 JSON', async () => {
    const modelJson = {
      candidates: [
        {
          finishReason: 'STOP',
          content: {
            parts: [
              {
                text: JSON.stringify({
                  segments: [
                    { speaker: '說話者1', text: '大家好' },
                    { speaker: '說話者2', text: '開始吧' },
                  ],
                  actionItems: ['處理上線 [DRI: 待指派]'],
                  mainPoints: ['重點A'],
                  qa: ['問：何時上線 答：下週三'],
                }),
              },
            ],
          },
        },
      ],
    };
    const fetchMock = vi
      .fn()
      // 0) ListModels → 挑到 gemini-3.5-flash
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      // 1) start resumable → 回傳 upload url header
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up.example/put' }))
      // 2) generateContent → 回傳結構化 JSON（上傳位元組走 XHR）
      .mockResolvedValueOnce(jsonResponse(modelJson));
    vi.stubGlobal('fetch', fetchMock);
    stubXHR();

    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'meeting.m4a';

    const result = await transcribeAndSummarize(file, 'KEY');
    expect(result.transcript).toHaveLength(2);
    expect(result.transcript[0]).toEqual({ speaker: '說話者1', text: '大家好' });
    expect(result.transcript[1].speaker).toBe('說話者2');
    expect(result.summary.actionItems).toEqual(['處理上線 [DRI: 待指派]']);
    expect(result.summary.mainPoints).toEqual(['重點A']);
    expect(result.summary.qa).toEqual(['問：何時上線 答：下週三']);
    // ListModels + start + generate = 3 次 fetch（上傳位元組走 XHR 不計）
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2][0]).toContain('models/gemini-3.5-flash:generateContent');
  });

  it('逐字稿被截斷（MAX_TOKENS）時給出可理解的錯誤', async () => {
    const truncated = {
      candidates: [{ finishReason: 'MAX_TOKENS', content: { parts: [{ text: '{"segments":[{"speaker":"說話者1' }] } }],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up.example/put' }))
      .mockResolvedValueOnce(jsonResponse(truncated));
    vi.stubGlobal('fetch', fetchMock);
    stubXHR();

    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'long.m4a';
    await expect(transcribeAndSummarize(file, 'KEY')).rejects.toThrow('太長');
  });
});
