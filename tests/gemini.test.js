import { describe, it, expect, vi, beforeEach } from 'vitest';
import { transcribeAndSummarize, pickModel, regenerateSummary, isTransientStatus, parseRetryDelayMs, translateMeeting, askMeeting, extractTerms, enhanceSection, uploadForJob, missingKeyEntries, generateNotes, enhanceNotesSection, requestAbort, clearAbort, clearModelCache, resetThinkingFlag, resetKeyRotation } from '../js/gemini.js';
import { recordCooldown } from '../js/usage.js';

beforeEach(() => {
  vi.restoreAllMocks();
  clearModelCache();
  resetThinkingFlag();
  clearAbort();
  resetKeyRotation();
  localStorage.clear();
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

describe('中斷（停止辨識）', () => {
  it('請求前已要求中斷 → 立刻丟出中斷錯誤，不再打 API', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(MODELS_RESPONSE));
    vi.stubGlobal('fetch', fetchMock);
    requestAbort();
    await expect(regenerateSummary([{ speaker: 's', text: 't' }], 'KEY')).rejects.toThrow('已停止');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('等待重試期間被中斷 → 不等完就結束（不再繼續重試）', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValue(errResponse(429, { error: { code: 429 } })); // 之後一律 429 → 進入等待
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY');
    await new Promise((r) => setTimeout(r, 10));
    requestAbort();
    await expect(p).rejects.toThrow('已停止');
    const calls = fetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 60));
    expect(fetchMock.mock.calls.length).toBe(calls); // 中斷後沒有再打任何請求
  });

  it('clearAbort 後可以重新開始', async () => {
    requestAbort();
    clearAbort();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify({ actionItems: [], mainPoints: ['ok'], qa: [] }) }] } }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await regenerateSummary([{ speaker: 's', text: 't' }], 'KEY');
    expect(r.mainPoints).toEqual(['ok']);
  });
});

describe('多金鑰輪替', () => {
  const okResp = () => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify({ actionItems: [], mainPoints: ['x'], qa: [] }) }] } }] });
  const usedKeys = (fetchMock) =>
    fetchMock.mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u.includes(':generateContent'))
      .map((u) => (u.match(/key=([^&]+)/) || [])[1]);

  it('連續請求輪流從不同金鑰起頭（否則第三把永遠用不到）', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)).mockResolvedValue(okResp());
    vi.stubGlobal('fetch', fetchMock);
    const keys = ['K1', 'K2', 'K3'];
    await regenerateSummary([{ speaker: 's', text: 't' }], keys);
    await regenerateSummary([{ speaker: 's', text: 't' }], keys);
    await regenerateSummary([{ speaker: 's', text: 't' }], keys);
    // 三次都成功 → 每次只打一把；三次應涵蓋三把不同金鑰
    expect(new Set(usedKeys(fetchMock)).size).toBe(3);
  });

  it('冷卻中的金鑰排到最後，不會明知會 429 還先打它', async () => {
    recordCooldown('K1', 30000); // K1 剛撞到限速
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)).mockResolvedValue(okResp());
    vi.stubGlobal('fetch', fetchMock);
    await regenerateSummary([{ speaker: 's', text: 't' }], ['K1', 'K2']);
    expect(usedKeys(fetchMock)[0]).toBe('K2');
  });
});

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

  it('會議重點：抓取與潤飾都要求「標題：說明」點列格式', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: ['發熱量達 800W 至 2000W'] }))
      .mockResolvedValueOnce(wrap({ items: [{ text: '散熱需求：發熱量達 800W 至 2000W', src: [1] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'mainPoints', 'KEY');
    expect(r).toEqual(['散熱需求：發熱量達 800W 至 2000W']);
    expect(fetchMock.mock.calls[1][1].body).toContain('標題：說明'); // 第一階段抓取
    expect(fetchMock.mock.calls[2][1].body).toContain('標題：說明'); // 第二階段潤飾
  });

  it('會議重點：合併上限比 Q&A 嚴（超過 2 條就拆回，避免多議題壓成一條論述）', async () => {
    const raw = ['重點一：A', '重點二：B', '重點三：C'];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: raw }))
      .mockResolvedValueOnce(wrap({ items: [{ text: '技術總覽：A、B、C', src: [1, 2, 3] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'mainPoints', 'KEY');
    expect(r).toEqual(raw);
  });

  it('Q&A：被標記略過的議程性問答不補回，且不影響其他條目', async () => {
    const raw = [
      '問：後續簡報有沒有要快速帶過的？ 答：建議快速帶過',
      '問：貴司有沒有 PSU 開發經驗？ 答：伺服器 PSU 為首次開發',
    ];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: raw }))
      .mockResolvedValueOnce(
        wrap({
          items: [
            { text: '', src: [1], drop: true },
            { text: '問：貴司是否具備 PSU 開發經驗？ 答：伺服器 PSU 為首次開發', src: [2] },
          ],
        })
      );
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'qa', 'KEY');
    expect(r).toEqual(['問：貴司是否具備 PSU 開發經驗？ 答：伺服器 PSU 為首次開發']);
  });

  it('Q&A：略過比例過高（超過四成）→ 判定過度篩選，全部保留', async () => {
    const raw = ['問：1 答：1', '問：2 答：2', '問：3 答：3', '問：4 答：4'];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: raw }))
      .mockResolvedValueOnce(
        wrap({
          items: [
            { text: '', src: [1], drop: true },
            { text: '', src: [2], drop: true },
            { text: '', src: [3], drop: true },
            { text: '問：4 答：4', src: [4] },
          ],
        })
      );
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'qa', 'KEY');
    expect(r).toEqual(raw); // 3/4 被丟 → 不接受，全部退回原文
  });

  it('Q&A：抓取階段要求問題自足（代名詞展開），潤飾階段要求價值篩選', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: ['問：A？ 答：a'] }))
      .mockResolvedValueOnce(wrap({ items: [{ text: '問：A？ 答：a', src: [1] }] }));
    vi.stubGlobal('fetch', fetchMock);
    await enhanceSection([{ speaker: 's', text: 't' }], 'qa', 'KEY');
    expect(fetchMock.mock.calls[1][1].body).toContain('代名詞'); // 第一階段：自足性
    expect(fetchMock.mock.calls[2][1].body).toContain('議程'); // 第二階段：排除議程性問答
    // 指涉推不準時要標「（推測）」，而不是留著看不懂或直接丟掉
    expect(fetchMock.mock.calls[1][1].body).toContain('推測');
    expect(fetchMock.mock.calls[2][1].body).toContain('推測');
  });

  it('回傳的清單帶有 dropped 筆數，供畫面提示使用者略過了幾則', async () => {
    const raw = ['問：要不要先簡介？ 答：好', '問：PSU 經驗？ 答：首次開發'];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: raw }))
      .mockResolvedValueOnce(wrap({ items: [{ text: '', src: [1], drop: true }, { text: '問：PSU 經驗？ 答：首次開發', src: [2] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'qa', 'KEY');
    expect(r.dropped).toBe(1);
    expect(JSON.parse(JSON.stringify(r))).toEqual(['問：PSU 經驗？ 答：首次開發']); // 存檔時不會帶著 dropped
  });

  it('待辦事項不做價值篩選：標了 drop 也照樣保留原文', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: ['交付報價 [DRI: 小明]'] }))
      .mockResolvedValueOnce(wrap({ items: [{ text: '', src: [1], drop: true }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection([{ speaker: 's', text: 't' }], 'actionItems', 'KEY');
    expect(r).toEqual(['交付報價 [DRI: 小明]']);
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

describe('uploadForJob 多金鑰上傳', () => {
  it('某把金鑰上傳暫時失敗 → 自動重試後成功，兩把都進輪替名單', async () => {
    // start(K1) ok → start(K2) 503 → 重試 start(K2) ok
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up/1' })) // K1 start
      .mockResolvedValueOnce(errResponse(503, { error: { code: 503 } })) // K2 start 失敗
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up/2' })); // K2 重試成功
    vi.stubGlobal('fetch', fetchMock);
    stubXHR();
    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'm.m4a';
    const r = await uploadForJob(file, [{ name: 'SY', key: 'K1' }, { name: 'DD', key: 'K2' }]);
    expect(r.uploads.map((u) => u.name)).toEqual(['SY', 'DD']);
  });

  it('某把金鑰重試後仍失敗 → 回報警告讓使用者知道只剩一把可輪替', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up/1' })) // K1 start ok
      .mockResolvedValue(errResponse(400, { error: { code: 400 } })); // K2 一直失敗
    vi.stubGlobal('fetch', fetchMock);
    stubXHR();
    const msgs = [];
    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'm.m4a';
    const r = await uploadForJob(file, [{ name: 'SY', key: 'K1' }, { name: 'DD', key: 'K2' }], (info) => {
      if (info && info.message) msgs.push(info.message);
    });
    expect(r.uploads.map((u) => u.name)).toEqual(['SY']);
    expect(msgs.some((m) => m.includes('DD') && m.includes('上傳失敗'))).toBe(true);
  });
});

describe('missingKeyEntries', () => {
  it('找出尚未上傳過這個檔案的金鑰（續傳時補傳用）', () => {
    const uploads = [{ key: 'K1', name: 'SY', fileUri: 'u1' }];
    const r = missingKeyEntries(uploads, [{ name: 'SY', key: 'K1' }, { name: 'DD', key: 'K2' }]);
    expect(r.map((k) => k.name)).toEqual(['DD']);
  });
  it('全部都已上傳時回空陣列', () => {
    const uploads = [{ key: 'K1' }, { key: 'K2' }];
    expect(missingKeyEntries(uploads, ['K1', 'K2'])).toEqual([]);
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

describe('generateNotes（學習筆記）', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
  const full = {
    outline: [{ title: '氣冷與水冷的取捨', anchor: '我們先看氣冷的部分', points: ['氣冷成熟可靠', '水冷單位體積解熱大'] }],
    concepts: [{ term: 'dry-out', plain: '毛細結構供液不足導致局部乾燒', why: '決定解熱上限' }],
    tables: [{ title: '氣冷 vs 水冷', headers: ['項目', '氣冷', '水冷'], rows: [['成本', '低', '約 5 倍'], ['漏液風險', '無', '千分之一']] }],
    figures: ['全鋁方案實測解熱 1600W'],
    quiz: [{ q: '為何 1300W 會出現溫度跳動？', a: '毛細回流阻力過大造成局部 dry-out。' }],
  };

  it('一次生成五區，ListModels + 一次 generate', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)).mockResolvedValueOnce(wrap(full));
    vi.stubGlobal('fetch', fetchMock);
    const n = await generateNotes([{ speaker: '說話者1', text: '我們先看氣冷的部分' }], 'KEY');
    expect(n.outline[0].title).toBe('氣冷與水冷的取捨');
    expect(n.outline[0].anchor).toBe('我們先看氣冷的部分');
    expect(n.concepts[0].term).toBe('dry-out');
    expect(n.tables[0].headers).toEqual(['項目', '氣冷', '水冷']);
    expect(n.tables[0].rows[1]).toEqual(['漏液風險', '無', '千分之一']);
    expect(n.figures).toEqual(['全鋁方案實測解熱 1600W']);
    expect(n.quiz[0].q).toContain('1300W');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('提示詞要求忠於逐字稿、表格列長與欄位一致、錨點用原話', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)).mockResolvedValueOnce(wrap(full));
    vi.stubGlobal('fetch', fetchMock);
    await generateNotes([{ speaker: 's', text: 't' }], 'KEY');
    const body = fetchMock.mock.calls[1][1].body;
    expect(body).toContain('原話');
    expect(body).toContain('相同');
    expect(body).toContain('不可自行');
  });

  it('模型把表格列回成字串時自動拆成欄位（用 | 分隔）', async () => {
    const odd = { ...full, tables: [{ title: 'T', headers: ['A', 'B'], rows: ['x | y', 'z | w'] }] };
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)).mockResolvedValueOnce(wrap(odd));
    vi.stubGlobal('fetch', fetchMock);
    const n = await generateNotes([{ speaker: 's', text: 't' }], 'KEY');
    expect(n.tables[0].rows).toEqual([['x', 'y'], ['z', 'w']]);
  });

  it('缺欄位或空表格不會壞掉，一律正規化成陣列', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ outline: [{ title: '只有標題' }], tables: [{ title: '沒有列', headers: ['A'], rows: [] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const n = await generateNotes([{ speaker: 's', text: 't' }], 'KEY');
    expect(n.outline[0].points).toEqual([]);
    expect(n.tables).toEqual([]); // 沒有資料列的表格直接丟掉，不顯示空殼
    expect(n.concepts).toEqual([]);
    expect(n.figures).toEqual([]);
    expect(n.quiz).toEqual([]);
  });
});

describe('翻譯涵蓋學習筆記', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
  const notes = {
    outline: [{ title: '氣冷與水冷', anchor: '先看氣冷', points: ['氣冷可靠'] }],
    concepts: [{ term: 'dry-out', plain: '局部乾燒', why: '決定上限' }],
    tables: [{ title: '比較', headers: ['項目', '氣冷'], rows: [['成本', '低']] }],
    figures: ['1600W'],
    quiz: [{ q: '為何？', a: '因為。' }],
  };

  it('有筆記時一併翻譯，結構與陣列長度不變', async () => {
    const en = {
      outline: [{ title: 'Air vs Liquid Cooling', anchor: '先看氣冷', points: ['Air cooling is reliable'] }],
      concepts: [{ term: 'dry-out', plain: 'Local dry burning', why: 'Sets the ceiling' }],
      tables: [{ title: 'Comparison', headers: ['Item', 'Air'], rows: [['Cost', 'Low']] }],
      figures: ['1600W'],
      quiz: [{ q: 'Why?', a: 'Because.' }],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ actionItems: [], mainPoints: ['point'], qa: [] })) // 摘要
      .mockResolvedValueOnce(wrap(en)) // 學習筆記
      .mockResolvedValueOnce(wrap({ segments: [{ speaker: 'Speaker 1', text: 'Hello' }] })); // 逐字稿
    vi.stubGlobal('fetch', fetchMock);
    const r = await translateMeeting([{ speaker: '說話者1', text: '哈囉' }], { mainPoints: ['重點'] }, 'en', 'KEY', { notes });
    expect(r.notes.outline[0].title).toBe('Air vs Liquid Cooling');
    expect(r.notes.tables[0].rows[0]).toEqual(['Cost', 'Low']);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('沒有筆記時不會多打一次（維持原本次數）', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ actionItems: [], mainPoints: ['point'], qa: [] }))
      .mockResolvedValueOnce(wrap({ segments: [{ speaker: 'Speaker 1', text: 'Hello' }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await translateMeeting([{ speaker: '說話者1', text: '哈囉' }], { mainPoints: ['重點'] }, 'en', 'KEY');
    expect(r.notes).toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe('enhanceNotesSection（學習筆記分區加強）', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
  // 160 段 → 每 80 段一批 → 兩批
  const longSegs = Array.from({ length: 160 }, (_, i) => ({ speaker: '講者', text: `第 ${i} 句` }));

  it('分批掃過整份逐字稿，概念以 term 跨批去重', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ concepts: [{ term: 'dry-out', plain: '局部乾燒' }, { term: 'TSV', plain: '矽穿孔' }] }))
      .mockResolvedValueOnce(wrap({ concepts: [{ term: 'dry-out', plain: '重複的' }, { term: 'CoWoS', plain: '封裝' }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceNotesSection(longSegs, 'concepts', 'KEY');
    expect(r.map((x) => x.term)).toEqual(['dry-out', 'TSV', 'CoWoS']);
    expect(r[0].plain).toBe('局部乾燒'); // 先出現的保留
    expect(fetchMock).toHaveBeenCalledTimes(3); // ListModels + 2 批
  });

  it('章節大綱依講述順序串接，相鄰同名章節合併', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ outline: [{ title: '氣冷', anchor: 'a1', points: ['p1'] }, { title: '水冷', anchor: 'a2', points: ['p2'] }] }))
      .mockResolvedValueOnce(wrap({ outline: [{ title: '水冷', anchor: 'a3', points: ['p3'] }, { title: '成本', anchor: 'a4', points: ['p4'] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceNotesSection(longSegs, 'outline', 'KEY');
    expect(r.map((x) => x.title)).toEqual(['氣冷', '水冷', '成本']);
    expect(r[1].points).toEqual(['p2', 'p3']); // 跨批的同一章節合併重點
    expect(r[1].anchor).toBe('a2'); // 錨點取最先出現的
  });

  it('表格以標題去重、關鍵數據以字串去重', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ tables: [{ title: '比較', headers: ['A'], rows: [['1']] }], figures: ['1600W'] }))
      .mockResolvedValueOnce(wrap({ tables: [{ title: '比較', headers: ['A'], rows: [['9']] }], figures: ['1600W', '5 倍'] }));
    vi.stubGlobal('fetch', fetchMock);
    const t = await enhanceNotesSection(longSegs, 'tables', 'KEY');
    expect(t).toHaveLength(1);
    clearModelCache();
    const fetchMock2 = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ figures: ['1600W'] }))
      .mockResolvedValueOnce(wrap({ figures: ['1600W', '5 倍'] }));
    vi.stubGlobal('fetch', fetchMock2);
    const f = await enhanceNotesSection(longSegs, 'figures', 'KEY');
    expect(f).toEqual(['1600W', '5 倍']);
  });

  it('未知的區塊名稱會丟錯', async () => {
    await expect(enhanceNotesSection(longSegs, 'nope', 'KEY')).rejects.toThrow('區塊');
  });
});
