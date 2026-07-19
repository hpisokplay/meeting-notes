import { describe, it, expect, vi, beforeEach } from 'vitest';
import { transcribeAndSummarize, pickModel, regenerateSummary, isTransientStatus, parseRetryDelayMs, translateMeeting, clearModelCache } from '../js/gemini.js';

beforeEach(() => {
  vi.restoreAllMocks();
  clearModelCache();
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

describe('parseRetryDelayMs', () => {
  it('解析 429 的 retryDelay（秒→毫秒，+1 秒緩衝）', () => {
    const body = '{"error":{"code":429,"details":[{"@type":"...RetryInfo","retryDelay":"27s"}]}}';
    expect(parseRetryDelayMs(body)).toBe(28000);
  });
  it('沒有 retryDelay 回 0', () => {
    expect(parseRetryDelayMs('{"error":{"code":429}}')).toBe(0);
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

  it('多金鑰：第一把 429 → 立刻換第二把成功', async () => {
    const okJson = { candidates: [{ content: { parts: [{ text: JSON.stringify({ actionItems: [], mainPoints: ['ok'], qa: [] }) }] } }] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels（用第一把）
      .mockResolvedValueOnce(errResponse(429, { error: { code: 429 } })) // 摘要：第一把 → 429
      .mockResolvedValueOnce(jsonResponse(okJson)); // 摘要：第二把 → 成功
    vi.stubGlobal('fetch', fetchMock);
    const r = await regenerateSummary([{ speaker: 's', text: 't' }], ['K1', 'K2']);
    expect(r.mainPoints).toEqual(['ok']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // 第二次 generate 用了第二把金鑰
    expect(fetchMock.mock.calls[2][0]).toContain('key=K2');
  });
});

describe('translateMeeting', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
  it('分批翻譯：ListModels + 摘要 + 逐字稿批次', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels（品質模型）
      .mockResolvedValueOnce(wrap({ actionItems: ['do X'], mainPoints: ['point'], qa: ['Q: ? A: yes'] })) // 摘要
      .mockResolvedValueOnce(wrap({ segments: [{ speaker: 'Speaker 1', text: 'Hello' }] })); // 逐字稿批次
    vi.stubGlobal('fetch', fetchMock);
    const r = await translateMeeting(
      [{ speaker: '說話者1', text: '哈囉' }],
      { actionItems: ['做X'], mainPoints: ['重點'], qa: ['問：？ 答：好'] },
      'en',
      'KEY'
    );
    expect(r.transcript[0]).toEqual({ speaker: 'Speaker 1', text: 'Hello' });
    expect(r.summary.mainPoints).toEqual(['point']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe('pickModel', () => {
  it('挑最新的 flash（非 lite、非 image）', () => {
    expect(pickModel(MODELS_RESPONSE.models)).toBe('gemini-3.5-flash');
  });
  it('沒有可用型號時回傳 null', () => {
    expect(pickModel([{ name: 'models/embedding-001', supportedGenerationMethods: ['embedContent'] }])).toBeNull();
  });
  it('省額度模式（preferLite）挑 flash-lite，否則挑品質優先的 flash', () => {
    const models = [
      { name: 'models/gemini-3.5-flash', supportedGenerationMethods: ['generateContent'] },
      { name: 'models/gemini-2.5-flash-lite', supportedGenerationMethods: ['generateContent'] },
    ];
    expect(pickModel(models)).toBe('gemini-3.5-flash');
    expect(pickModel(models, { preferLite: true })).toBe('gemini-2.5-flash-lite');
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
function errResponse(status, obj) {
  return {
    ok: false,
    status,
    headers: { get: () => null },
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

  const segResp = (segs) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify({ segments: segs }) }] } }] });
  const sumResp = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });

  it('happy path：上傳→ACTIVE→逐字稿+摘要（兩次 generate）', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up.example/put' })) // start
      .mockResolvedValueOnce(segResp([{ speaker: '說話者1', text: '大家好' }, { speaker: '說話者2', text: '開始吧' }])) // 逐字稿
      .mockResolvedValueOnce(sumResp({ actionItems: ['處理上線 [DRI: 待指派]'], mainPoints: ['重點A'], qa: ['問：何時上線 答：下週三'] })); // 摘要
    vi.stubGlobal('fetch', fetchMock);
    stubXHR();

    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'meeting.m4a';

    const result = await transcribeAndSummarize(file, 'KEY'); // 無 durationSec → 整檔一次
    expect(result.transcript).toHaveLength(2);
    expect(result.transcript[1].speaker).toBe('說話者2');
    expect(result.summary.actionItems).toEqual(['處理上線 [DRI: 待指派]']);
    expect(result.summary.mainPoints).toEqual(['重點A']);
    expect(result.summary.qa).toEqual(['問：何時上線 答：下週三']);
    // ListModels + start + 逐字稿 + 摘要 = 4 次 fetch
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('長錄音自動分段：80 分鐘 → 切成 2 段逐字稿再合併', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up.example/put' })) // start
      .mockResolvedValueOnce(segResp([{ speaker: '說話者1', text: '第一段' }])) // window 1
      .mockResolvedValueOnce(segResp([{ speaker: '說話者1', text: '第二段' }])) // window 2
      .mockResolvedValueOnce(sumResp({ actionItems: [], mainPoints: ['整體重點'], qa: [] })); // 摘要
    vi.stubGlobal('fetch', fetchMock);
    stubXHR();

    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'long.m4a';
    const result = await transcribeAndSummarize(file, 'KEY', { durationSec: 80 * 60 }); // 80 分鐘 → 40 分鐘一段 → 2 段
    expect(result.transcript.map((s) => s.text)).toEqual(['第一段', '第二段']);
    expect(result.summary.mainPoints).toEqual(['整體重點']);
    // ListModels + start + 2 段逐字稿 + 摘要 = 5 次 fetch
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it('單段內容太密被截斷 → 自動對半再切', async () => {
    const truncated = jsonResponse({ candidates: [{ finishReason: 'MAX_TOKENS', content: { parts: [{ text: '{"segments":[' }] } }] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up.example/put' })) // start
      .mockResolvedValueOnce(truncated) // 整段截斷
      .mockResolvedValueOnce(segResp([{ speaker: '說話者1', text: '前半' }])) // 前半
      .mockResolvedValueOnce(segResp([{ speaker: '說話者1', text: '後半' }])) // 後半
      .mockResolvedValueOnce(sumResp({ actionItems: [], mainPoints: [], qa: [] })); // 摘要
    vi.stubGlobal('fetch', fetchMock);
    stubXHR();

    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'dense.m4a';
    const result = await transcribeAndSummarize(file, 'KEY', { durationSec: 18 * 60 }); // 1 段但截斷 → 對半
    expect(result.transcript.map((s) => s.text)).toEqual(['前半', '後半']);
  });
});
