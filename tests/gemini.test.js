import { describe, it, expect, vi, beforeEach } from 'vitest';
import { transcribeAndSummarize, pickModel, regenerateSummary, isTransientStatus, parseRetryDelayMs, translateMeeting, askMeeting, extractTerms, enhanceSection, clearModelCache, resetThinkingFlag } from '../js/gemini.js';

beforeEach(() => {
  vi.restoreAllMocks();
  clearModelCache();
  resetThinkingFlag();
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

describe('askMeeting', () => {
  it('把逐字稿+問題丟給模型，回傳純文字回答', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels（品質模型）
      .mockResolvedValueOnce(jsonResponse({ candidates: [{ content: { parts: [{ text: '結論是下週三上線。' }] } }] }));
    vi.stubGlobal('fetch', fetchMock);
    const a = await askMeeting(
      [{ speaker: '說話者1', text: '下週三上線' }],
      { actionItems: [], mainPoints: ['上線時程'], qa: [] },
      '結論是什麼？',
      'KEY'
    );
    expect(a).toBe('結論是下週三上線。');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // 提示詞內含逐字稿與問題
    const body = fetchMock.mock.calls[1][1].body;
    expect(body).toContain('下週三上線');
    expect(body).toContain('結論是什麼');
  });
});

describe('400 thinkingBudget 退避', () => {
  it('模型回 400（thinking）→ 自動移除 thinkingConfig 重試成功', async () => {
    const okJson = { candidates: [{ content: { parts: [{ text: JSON.stringify({ actionItems: [], mainPoints: ['ok'], qa: [] }) }] } }] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(errResponse(400, { error: { code: 400, status: 'INVALID_ARGUMENT' } })) // 首次帶 thinkingConfig → 400
      .mockResolvedValueOnce(jsonResponse(okJson)); // 移除 thinkingConfig 後重試成功
    vi.stubGlobal('fetch', fetchMock);
    const r = await regenerateSummary([{ speaker: 's', text: 't' }], 'KEY');
    expect(r.mainPoints).toEqual(['ok']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // 第 3 次（重試）的 body 不含 thinkingConfig
    expect(fetchMock.mock.calls[2][1].body).not.toContain('thinkingConfig');
  });
});

describe('extractTerms', () => {
  it('挑出專有名詞，跨批次去重並帶分類與建議', async () => {
    const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(wrap({ items: [{ term: '宏騰', category: 'org', fix: '宏騰科技' }, { term: 'Mikiya', category: 'person', fix: '' }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await extractTerms([{ speaker: '說話者1', text: '宏騰跟 Mikiya 討論' }], 'KEY');
    expect(r).toHaveLength(2);
    expect(r[0]).toEqual({ t: '宏騰', cat: 'org', fix: '宏騰科技' });
    expect(r[1].t).toBe('Mikiya');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('enhanceSection 兩階段（抓全→整理潤飾）', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });

  it('Q&A：同一個問題的追問可合併（附 src 編號），回傳潤飾後結果', async () => {
    const raw = [
      '問：那個上線是什麼時候？ 答：嗯就是下週三啦',
      '問：所以上線確定是下週三嗎？ 答：對啦確定',
    ];
    const polished = [{ text: '問：上線時程為何？ 答：確定於下週三上線', src: [1, 2] }];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(wrap({ items: raw })) // 批次抓取
      .mockResolvedValueOnce(wrap({ items: polished })); // 整理潤飾
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: '說話者1', text: '上線下週三' }], 'qa', 'KEY');
    expect(r).toEqual(['問：上線時程為何？ 答：確定於下週三上線']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // 潤飾請求要帶上編號的原始清單，且限定「同一個問題」才可合併、改寫成書面語
    const body = fetchMock.mock.calls[2][1].body;
    expect(body).toContain('上線是什麼時候');
    expect(body).toContain('同一個問題');
    expect(body).toContain('書面');
  });

  it('沒被涵蓋的原始條目會自動補回（不會漏）', async () => {
    const raw = ['問：A？ 答：a', '問：A 確定嗎？ 答：確定', '問：C？ 答：c'];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: raw }))
      // 模型只回 1、2 的合併，漏了 3
      .mockResolvedValueOnce(wrap({ items: [{ text: '問：A？ 答：確定為 a', src: [1, 2] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'qa', 'KEY');
    expect(r).toEqual(['問：A？ 答：確定為 a', '問：C？ 答：c']);
  });

  it('合併過頭（一條涵蓋超過 3 條原始）→ 拆回原始條目', async () => {
    const raw = ['問：1 答：1', '問：2 答：2', '問：3 答：3', '問：4 答：4'];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: raw }))
      // 模型把 4 個不同問題濃縮成 1 條 → 不接受，拆回
      .mockResolvedValueOnce(wrap({ items: [{ text: '本會議 Q&A 總結', src: [1, 2, 3, 4] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'qa', 'KEY');
    expect(r).toEqual(raw);
  });

  it('待辦事項：潤飾指示要求保留 [DRI: …] 標註', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: ['嗯那個小明要去弄一下伺服器 [DRI: 小明]'] }))
      .mockResolvedValueOnce(wrap({ items: [{ text: '調整伺服器設定 [DRI: 小明]', src: [1] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'actionItems', 'KEY');
    expect(r).toEqual(['調整伺服器設定 [DRI: 小明]']);
    const body = fetchMock.mock.calls[2][1].body;
    expect(body).toContain('DRI');
  });

  it('抓不到任何內容時不做潤飾（不多打一次 API），回傳空陣列', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'qa', 'KEY');
    expect(r).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('潤飾階段解析失敗時丟錯（不靜默退回口語版）', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: ['問：A？ 答：B'] }))
      .mockResolvedValueOnce(jsonResponse({ candidates: [{ content: { parts: [{ text: '不是JSON' }] } }] }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(enhanceSection([{ speaker: 's', text: 't' }], 'qa', 'KEY')).rejects.toThrow('整理');
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
