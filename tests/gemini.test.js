import { describe, it, expect, vi, beforeEach } from 'vitest';
import { transcribeRange, transcribeAndSummarize, pickModel, rankModels, nextModelForKeys, isModelOverloaded, isQuotaStall, isContentBlocked, convertToTraditional, pickModelForKeys, regenerateSummary, isTransientStatus, parseRetryDelayMs, translateMeeting, askMeeting, extractTerms, enhanceSection, uploadForJob, missingKeyEntries, pickUploadKeys, canUseWholeMode, generateNotes, enhanceNotesSection, requestAbort, clearAbort, clearModelCache, resetThinkingFlag, resetKeyRotation, markModelBusy, isModelBusy, clearModelBusy } from '../js/gemini.js';
import { recordCooldown, recordUse } from '../js/usage.js';

beforeEach(() => {
  vi.restoreAllMocks();
  clearModelCache();
  clearModelBusy();
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
      .mockResolvedValueOnce(wrap({ items: [{ term: '宏騰', category: 'org', fix: '宏騰科技' }, { term: 'Mikiya', category: 'person', fix: '' }] }))
      .mockResolvedValueOnce(wrap({ groups: [] })); // 分組階段：兩者無關，不歸組
    vi.stubGlobal('fetch', fetchMock);
    const r = await extractTerms([{ speaker: '說話者1', text: '宏騰跟 Mikiya 討論' }], 'KEY');
    expect(r).toHaveLength(2);
    expect(r[0]).toEqual({ t: '宏騰', cat: 'org', fix: '宏騰科技', alts: [] });
    expect(r[1].t).toBe('Mikiya');
    expect(fetchMock).toHaveBeenCalledTimes(3); // ListModels + 1 批 + 分組
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
    figures: [{ group: '效能', label: '全鋁方案實測解熱', value: '1600 W' }],
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
    expect(n.figures).toEqual([{ group: '效能', label: '全鋁方案實測解熱', value: '1600 W' }]);
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

describe('連線掛住不可無限等待', () => {
  it('fetch 永不回應 → 逾時後換金鑰，最終轉 Groq 而不是卡死', async () => {
    vi.useFakeTimers();
    localStorage.setItem('groq_api_key', 'gsk_rescue');
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes('api.groq.com')) {
        if (String(url).includes('/models')) return Promise.resolve(jsonResponse({ data: [{ id: 'openai/gpt-oss-120b' }] }));
        return Promise.resolve(
          jsonResponse({ choices: [{ message: { content: JSON.stringify({ actionItems: [], mainPoints: ['逾時後救回'], qa: [] }) } }] })
        );
      }
      if (String(url).includes('/models?')) return Promise.resolve(jsonResponse(MODELS_RESPONSE));
      // Gemini：永遠不回應，但要對 abort 有反應（真實 fetch 的行為）
      return new Promise((_, reject) => {
        const sig = init && init.signal;
        if (sig) sig.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY');
    await vi.advanceTimersByTimeAsync(200000);
    const r = await p;
    vi.useRealTimers();
    expect(r.mainPoints).toEqual(['逾時後救回']);
    localStorage.removeItem('groq_api_key');
  });

  it('連線失敗且沒有 Groq 金鑰 → 明確報錯，不無限等待', async () => {
    vi.useFakeTimers();
    localStorage.removeItem('groq_api_key');
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes('/models?')) return Promise.resolve(jsonResponse(MODELS_RESPONSE));
      return new Promise((_, reject) => {
        const sig = init && init.signal;
        if (sig) sig.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY').then(
      () => new Error('不該成功'),
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(900000);
    const err = await p;
    vi.useRealTimers();
    expect(String(err.message)).toMatch(/逾時|網路/);
  });
});

describe('加強／掃詞的大批次與對半重試', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
  const segs300 = Array.from({ length: 300 }, (_, i) => ({ speaker: 's', text: `第 ${i} 句` }));

  it('300 句只分 2 批（240+60），不再是 4 批', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: ['a'] })) // 第 1–240 句
      .mockResolvedValueOnce(wrap({ items: ['b'] })) // 第 241–300 句
      .mockResolvedValueOnce(wrap({ items: [{ text: 'a 潤', src: [1] }, { text: 'b 潤', src: [2] }] })); // 總潤飾
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection(segs300, 'mainPoints', 'KEY');
    expect(fetchMock).toHaveBeenCalledTimes(4); // ListModels + 2 批 + 潤飾
    expect(r.length).toBe(2);
  });

  it('單批輸出壞掉 → 對半重試，不整場失敗', async () => {
    const bad = jsonResponse({ candidates: [{ content: { parts: [{ text: '這不是 JSON' }] } }] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(bad) // 第 1–240 句 → 壞
      .mockResolvedValueOnce(wrap({ items: ['前半'] })) // 1–120
      .mockResolvedValueOnce(wrap({ items: ['後半'] })) // 121–240
      .mockResolvedValueOnce(wrap({ items: ['尾段'] })) // 241–300
      .mockResolvedValueOnce(wrap({ items: [{ text: '合併潤飾', src: [1, 2, 3] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await enhanceSection(segs300, 'mainPoints', 'KEY');
    expect(fetchMock).toHaveBeenCalledTimes(6); // ListModels + 壞批 + 兩個半批 + 尾批 + 潤飾
    expect(r.length).toBeGreaterThan(0);
  });

  it('掃詞：單批壞掉對半撿回，縮到最小仍壞就略過該批不中斷', async () => {
    const bad = jsonResponse({ candidates: [{ content: { parts: [{ text: '亂碼' }] } }] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(bad) // 1–240 壞
      .mockResolvedValueOnce(wrap({ items: [{ term: 'CoWoS', category: 'term', fix: '' }] })) // 1–120 好
      .mockResolvedValueOnce(bad) // 121–240 壞 → 對半
      .mockResolvedValueOnce(bad) // 121–180（=60 句，最小）仍壞 → 略過
      .mockResolvedValueOnce(bad) // 181–240（=60 句，最小）仍壞 → 略過
      .mockResolvedValueOnce(wrap({ items: [{ term: 'TSV', category: 'term', fix: '' }] })) // 241–300 好
      .mockResolvedValueOnce(wrap({ groups: [] })); // 分組
    vi.stubGlobal('fetch', fetchMock);
    const r = await extractTerms(segs300, 'KEY');
    expect(r.map((x) => x.t)).toEqual(['CoWoS', 'TSV']);
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
      .mockResolvedValueOnce(wrap({ tables: [{ title: '比較', headers: ['A'], rows: [['1']] }] }))
      .mockResolvedValueOnce(wrap({ tables: [{ title: '比較', headers: ['A'], rows: [['9']] }] }));
    vi.stubGlobal('fetch', fetchMock);
    const t = await enhanceNotesSection(longSegs, 'tables', 'KEY');
    expect(t).toHaveLength(1);
    clearModelCache();
    const fetchMock2 = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ figures: [{ group: 'g', label: '解熱能力', value: '1600 W' }] }))
      .mockResolvedValueOnce(wrap({ figures: [{ group: 'g', label: '解熱能力', value: '1600 W' }, { group: 'g', label: '成本倍數', value: '5 倍' }] }));
    vi.stubGlobal('fetch', fetchMock2);
    const f = await enhanceNotesSection(longSegs, 'figures', 'KEY');
    expect(f.map((x) => x.label)).toEqual(['解熱能力', '成本倍數']); // 跨批以 label 去重
  });

  it('未知的區塊名稱會丟錯', async () => {
    await expect(enhanceNotesSection(longSegs, 'nope', 'KEY')).rejects.toThrow('區塊');
  });
});

describe('逐字稿時間戳', () => {
  const segResp2 = (segs) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify({ segments: segs }) }] } }] });

  it('提示詞要求每段標秒數（相對於這個音檔開頭），且時間戳為選填', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(segResp2([{ speaker: '講者', text: '哈囉', t: 12 }]));
    vi.stubGlobal('fetch', fetchMock);
    const segs = await transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/mp4', 'gemini-3.5-flash', 0, 600, false);
    expect(segs[0].t).toBe(12);
    const body = fetchMock.mock.calls[0][1].body;
    expect(body).toContain('秒數');
    expect(body).toContain('音檔開頭');
  });

  it('模型沒給時間戳也不會壞掉（欄位就是不存在）', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(segResp2([{ speaker: '講者', text: '哈囉' }]));
    vi.stubGlobal('fetch', fetchMock);
    const segs = await transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/mp4', 'gemini-3.5-flash', 0, 600, false);
    expect(segs[0].text).toBe('哈囉');
    expect(segs[0].t).toBeUndefined();
  });

  it('時間戳為負或非數字會被丟掉，不會顯示成亂碼', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(segResp2([{ speaker: 'a', text: 'x', t: -5 }, { speaker: 'b', text: 'y', t: 'abc' }, { speaker: 'c', text: 'z', t: 30 }]));
    vi.stubGlobal('fetch', fetchMock);
    const segs = await transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/mp4', 'gemini-3.5-flash', 0, 600, false);
    expect(segs[0].t).toBeUndefined();
    expect(segs[1].t).toBeUndefined();
    expect(segs[2].t).toBe(30);
  });
});

describe('純文字功能的 Groq 收場備援', () => {
  it('Gemini 全 429 且設了 Groq 金鑰 → 改走 Llama，功能照常成功', async () => {
    vi.useFakeTimers();
    localStorage.setItem('groq_api_key', 'gsk_rescue');
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('api.groq.com')) {
        if (String(url).includes('/models')) return Promise.resolve(jsonResponse({ data: [{ id: 'openai/gpt-oss-120b' }] }));
        return Promise.resolve(
          jsonResponse({ choices: [{ message: { content: JSON.stringify({ actionItems: [], mainPoints: ['Llama 救回'], qa: [] }) } }] })
        );
      }
      if (String(url).includes('/models?')) return Promise.resolve(jsonResponse(MODELS_RESPONSE));
      return Promise.resolve(errResponse(429, { error: { code: 429, message: 'quota' } }));
    });
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY');
    await vi.advanceTimersByTimeAsync(200000);
    const r = await p;
    vi.useRealTimers();
    expect(r.mainPoints).toEqual(['Llama 救回']);
    // 確認真的打到 Groq，且要求 JSON 模式
    const groqCall = fetchMock.mock.calls.find(([u]) => String(u).includes('chat/completions'));
    expect(groqCall).toBeTruthy();
    expect(JSON.parse(groqCall[1].body).response_format).toEqual({ type: 'json_object' });
    localStorage.removeItem('groq_api_key');
  });

  it('帶音檔的請求不可轉給 Llama（它聽不了聲音）→ 照原本報額度錯誤', async () => {
    vi.useFakeTimers();
    localStorage.setItem('groq_api_key', 'gsk_rescue');
    const fetchMock = vi.fn().mockResolvedValue(errResponse(429, { error: { code: 429, message: 'quota' } }));
    vi.stubGlobal('fetch', fetchMock);
    const p = transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.7-flash', 0, 0, true).then(
      () => new Error('不該成功'),
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(200000);
    const err = await p;
    vi.useRealTimers();
    expect(String(err.message)).toMatch(/額度受限/);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('api.groq.com'))).toBe(false);
    localStorage.removeItem('groq_api_key');
  });

  it('沒設 Groq 金鑰 → 行為與從前完全相同', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValue(errResponse(429, { error: { code: 429, message: 'quota' } }));
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY').then(
      () => new Error('不該成功'),
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(200000);
    const err = await p;
    vi.useRealTimers();
    expect(String(err.message)).toMatch(/額度/);
  });
});

// ===================================================================
// 【2026-08-24】使用者實測回報：「看起來沒有轉 Groq」。
// 查下去是三件事疊在一起，這一組測試各釘一件。
// ===================================================================
describe('備援沒生效時，要說得出為什麼（不得靜靜吞掉）', () => {
  it('沒設 Groq 金鑰 → 錯誤訊息要指名「尚未設定 Groq 金鑰」', async () => {
    vi.useFakeTimers();
    localStorage.removeItem('groq_api_key');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValue(errResponse(429, { error: { code: 429, message: 'quota' } }));
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY').then(
      () => new Error('不該成功'),
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(200000);
    const err = await p;
    vi.useRealTimers();
    // 舊版是 `catch (_) {}`：四種失敗長成同一句 Gemini 錯誤，使用者結構上分不出來。
    expect(String(err.message)).toMatch(/尚未設定 Groq 金鑰/);
  });

  it('Groq 自己也失敗 → 要講出 Groq 的錯，不可以只報 Gemini 的錯', async () => {
    vi.useFakeTimers();
    localStorage.setItem('groq_api_key', 'gsk_rescue');
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('api.groq.com')) return Promise.resolve(errResponse(401, { error: { message: 'bad key' } }));
      if (String(url).includes('/models?')) return Promise.resolve(jsonResponse(MODELS_RESPONSE));
      return Promise.resolve(errResponse(429, { error: { code: 429, message: 'quota' } }));
    });
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY').then(
      () => new Error('不該成功'),
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(200000);
    const err = await p;
    vi.useRealTimers();
    expect(String(err.message)).toMatch(/Groq 備援未生效/);
    localStorage.removeItem('groq_api_key');
  });

  it('帶音檔轉不了時，理由要說是音檔，不是金鑰', async () => {
    vi.useFakeTimers();
    localStorage.setItem('groq_api_key', 'gsk_rescue');
    const fetchMock = vi.fn().mockResolvedValue(errResponse(429, { error: { code: 429, message: 'quota' } }));
    vi.stubGlobal('fetch', fetchMock);
    const p = transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.7-flash', 0, 0, true).then(
      () => new Error('不該成功'),
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(200000);
    const err = await p;
    vi.useRealTimers();
    expect(String(err.message)).toMatch(/帶了音檔/);
    expect(String(err.message)).not.toMatch(/尚未設定 Groq 金鑰/);
    localStorage.removeItem('groq_api_key');
  });
});

describe('純文字請求不必等滿 2.5 分鐘才轉 Groq', () => {
  it('第一輪金鑰就全部受限 → 立刻打 Groq（不等待）', async () => {
    vi.useFakeTimers();
    localStorage.setItem('groq_api_key', 'gsk_rescue');
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('api.groq.com')) {
        if (String(url).includes('/models')) return Promise.resolve(jsonResponse({ data: [{ id: 'openai/gpt-oss-120b' }] }));
        return Promise.resolve(
          jsonResponse({ choices: [{ message: { content: JSON.stringify({ actionItems: [], mainPoints: ['早救'], qa: [] }) } }] })
        );
      }
      if (String(url).includes('/models?')) return Promise.resolve(jsonResponse(MODELS_RESPONSE));
      return Promise.resolve(errResponse(429, { error: { code: 429, message: 'quota' } }));
    });
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY');
    // **只推進 1 秒**。舊版要等滿 5 輪／~150 秒才會走到 Groq，這一步會 timeout。
    await vi.advanceTimersByTimeAsync(1000);
    const r = await p;
    vi.useRealTimers();
    expect(r.mainPoints).toEqual(['早救']);
    localStorage.removeItem('groq_api_key');
  });
});

describe('503 是型號忙線，不是金鑰問題', () => {
  it('錯誤訊息不得叫使用者去換金鑰或加額度', async () => {
    vi.useFakeTimers();
    localStorage.removeItem('groq_api_key');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValue(errResponse(503, { error: { code: 503, message: 'This model is currently experiencing high demand.' } }));
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY').then(
      () => new Error('不該成功'),
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(200000);
    const err = await p;
    vi.useRealTimers();
    const m = String(err.message);
    expect(m).toMatch(/忙不過來|忙線/);
    expect(m).toMatch(/不.*是.*你的金鑰|不是你的金鑰/);
    // 反方向：不得再叫人去 AI Studio 加額度——那對 503 沒有用
    expect(m).not.toMatch(/開通 API 付費/);
  });
});

describe('Groq 備援的判斷與繁體轉換', () => {
  it('isQuotaStall 只認 429 收場訊息', () => {
    expect(isQuotaStall(new Error('額度受限，暫時無法完成。稍等 1–2 分鐘…'))).toBe(true);
    expect(isQuotaStall(new Error('辨識失敗 (503)：UNAVAILABLE'))).toBe(false);
    expect(isQuotaStall(null)).toBe(false);
  });

  it('isContentBlocked 認得安全過濾器的三種標記，不誤吃其他錯誤', () => {
    expect(isContentBlocked(new Error('辨識結果解析失敗，請重試一次。（型號 gemini-3.7-flash／finishReason: PROHIBITED_CONTENT）這次回應沒有任何文字內容。'))).toBe(true);
    expect(isContentBlocked(new Error('（finishReason: SAFETY）這次回應沒有任何文字內容。'))).toBe(true);
    expect(isContentBlocked(new Error('blockReason: OTHER'))).toBe(true);
    expect(isContentBlocked(new Error('額度受限，暫時無法完成。'))).toBe(false);
    expect(isContentBlocked(new Error('辨識失敗 (503)：UNAVAILABLE'))).toBe(false);
    expect(isContentBlocked(null)).toBe(false);
  });

  it('convertToTraditional：批次轉換，長度相符才採用', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(
        jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify({ texts: ['大家好', '今天討論預算'] }) }] } }] })
      );
    vi.stubGlobal('fetch', fetchMock);
    const out = await convertToTraditional(['大家好', '今天讨论预算'], ['K1']);
    expect(out).toEqual(['大家好', '今天討論預算']);
  });

  it('convertToTraditional：長度不符 → 該批保留原文，不丟錯', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(
        jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify({ texts: ['只回了一句'] }) }] } }] })
      );
    vi.stubGlobal('fetch', fetchMock);
    const out = await convertToTraditional(['句一', '句二'], ['K1']);
    expect(out).toEqual(['句一', '句二']);
  });

  it('convertToTraditional：請求失敗 → 保留原文，不丟錯', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValue(errResponse(400, { error: { code: 400 } }));
    vi.stubGlobal('fetch', fetchMock);
    const out = await convertToTraditional(['句一'], ['K1']);
    expect(out).toEqual(['句一']);
  });
});

describe('忙線記憶：撞過 503 的型號十分鐘內自動跳過', () => {
  it('markModelBusy 之後，選型與換型都跳過它；過期自動解除', async () => {
    markModelBusy('m-busy', 30);
    expect(isModelBusy('m-busy')).toBe(true);
    await new Promise((r) => setTimeout(r, 40));
    expect(isModelBusy('m-busy')).toBe(false);
  });

  it('首選忙線 → pickModelForKeys 直接給下一名，不必先撞一次', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE));
    vi.stubGlobal('fetch', fetchMock);
    expect(await pickModelForKeys(['K1'])).toBe('gemini-3.5-flash'); // 建立快取
    markModelBusy('gemini-3.5-flash');
    expect(await pickModelForKeys(['K1'])).toBe('gemini-3.1-pro'); // 跳過忙線的首選
  });

  it('nextModelForKeys 跳過忙線的候選', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE));
    vi.stubGlobal('fetch', fetchMock);
    await pickModelForKeys(['K1']);
    markModelBusy('gemini-3.1-pro');
    // 3.5-flash 的下一名本是 3.1-pro（忙線）→ 跳到再下一名
    expect(await nextModelForKeys(['K1'], 'gemini-3.5-flash')).toBe('gemini-2.5-flash');
  });

  it('503 打到第 2 輪就放棄並標記忙線，不再空等滿五輪', async () => {
    vi.useFakeTimers();
    localStorage.removeItem('groq_api_key');
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('/models?')) return Promise.resolve(jsonResponse(MODELS_RESPONSE));
      return Promise.resolve(errResponse(503, { error: { code: 503, message: 'This model is currently experiencing high demand.' } }));
    });
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY').then(
      () => new Error('不該成功'),
      (e) => e
    );
    // 只需撐過第一輪的 8 秒等待；打滿五輪要 80 秒
    await vi.advanceTimersByTimeAsync(15000);
    const err = await p;
    vi.useRealTimers();
    expect(String(err.message)).toMatch(/忙不過來|忙線/);
    const gen = fetchMock.mock.calls.filter(([u]) => String(u).includes('generateContent'));
    expect(gen.length).toBe(2); // 第 1 輪 + 第 2 輪，各一次
    expect(isModelBusy('gemini-3.5-flash')).toBe(true); // 已被標記，之後的選型會跳過
  });
});

describe('型號忙線時換型號', () => {
  it('rankModels 依偏好排名，pickModel 取第一名', () => {
    const models = [
      { name: 'models/gemini-3.7-flash', supportedGenerationMethods: ['generateContent'] },
      { name: 'models/gemini-3.0-flash', supportedGenerationMethods: ['generateContent'] },
      { name: 'models/gemini-3.7-pro', supportedGenerationMethods: ['generateContent'] },
    ];
    const ranked = rankModels(models, { preferLite: false });
    expect(ranked[0]).toBe('gemini-3.7-flash');
    expect(pickModel(models, { preferLite: false })).toBe(ranked[0]);
    expect(ranked).toContain('gemini-3.0-flash');
    expect(ranked.length).toBe(3);
  });

  it('nextModelForKeys 回傳排名中的下一個', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse({
        models: [
          { name: 'models/gemini-3.7-flash', supportedGenerationMethods: ['generateContent'] },
          { name: 'models/gemini-3.0-flash', supportedGenerationMethods: ['generateContent'] },
        ],
      })
    );
    vi.stubGlobal('fetch', fetchMock);
    const first = await pickModelForKeys(['K1']);
    expect(first).toBe('gemini-3.7-flash');
    expect(await nextModelForKeys(['K1'], 'gemini-3.7-flash')).toBe('gemini-3.0-flash');
    // 已經是最後一名 → 沒有下一個
    expect(await nextModelForKeys(['K1'], 'gemini-3.0-flash')).toBe(null);
  });

  it('isModelOverloaded 只認忙線，不把額度或格式問題誤判成忙線', () => {
    expect(isModelOverloaded(new Error('辨識失敗 (503)：{"status":"UNAVAILABLE"}'))).toBe(true);
    expect(isModelOverloaded(new Error('This model is currently experiencing high demand.'))).toBe(true);
    expect(isModelOverloaded(new Error('額度受限，暫時無法完成。'))).toBe(false);
    expect(isModelOverloaded(new Error('辨識失敗 (400)：格式不支援'))).toBe(false);
    expect(isModelOverloaded(null)).toBe(false);
  });
});

describe('思考型模型的多段回應（gemini 3.x）', () => {
  const segsJson = (segs) => JSON.stringify({ segments: segs });

  it('回應夾帶思考摘要時，要跳過思考段落讀真正的答案', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse({
        candidates: [
          {
            finishReason: 'STOP',
            content: {
              parts: [
                { thought: true, text: '我先聽一遍這段錄音，判斷有幾個人在說話…' },
                { text: segsJson([{ speaker: '說話者1', text: '今天開會討論預算' }]) },
              ],
            },
          },
        ],
      })
    );
    vi.stubGlobal('fetch', fetchMock);
    const segs = await transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.7-flash', 1800, 3600, true);
    expect(segs).toEqual([{ speaker: '說話者1', text: '今天開會討論預算' }]);
    expect(fetchMock).toHaveBeenCalledTimes(1); // 不該觸發對半重問
  });

  it('長答案被拆成多段 text 時要接起來，不能只讀第一段', async () => {
    const full = segsJson([{ speaker: 'a', text: '前半句' }, { speaker: 'b', text: '後半句' }]);
    const cut = Math.floor(full.length / 2);
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse({
        candidates: [{ finishReason: 'STOP', content: { parts: [{ text: full.slice(0, cut) }, { text: full.slice(cut) }] } }],
      })
    );
    vi.stubGlobal('fetch', fetchMock);
    const segs = await transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.7-flash', 0, 0, true);
    expect(segs.map((s) => s.text)).toEqual(['前半句', '後半句']);
  });

  it('摘要也要吃得下思考型模型的回應', async () => {
    const body = { actionItems: [], mainPoints: ['重點一'], qa: [] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(
        jsonResponse({
          candidates: [{ content: { parts: [{ thought: true, text: '思考中…' }, { text: JSON.stringify(body) }] } }],
        })
      );
    vi.stubGlobal('fetch', fetchMock);
    const r = await regenerateSummary([{ speaker: 's', text: 't' }], ['K1']);
    expect(r.mainPoints).toEqual(['重點一']);
  });

  it('整份回應都只有思考、沒有答案 → 仍要明確報錯，不可當成空結果吞掉', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ candidates: [{ finishReason: 'STOP', content: { parts: [{ thought: true, text: '想了很久' }] } }] })
    );
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.7-flash', 0, 0, true)
    ).rejects.toThrow(/解析失敗/);
  });
});

describe('辨識結果解析失敗時的補救', () => {
  const segOk = (segs) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify({ segments: segs }) }] } }] });
  const badJson = (finishReason = 'STOP') =>
    jsonResponse({ candidates: [{ finishReason, content: { parts: [{ text: '這不是 JSON' }] } }] });

  it('切段模式解析失敗 → 在這個音檔內對半再問，不再整段報廢', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(badJson()) // 整段 → 解析不出來
      .mockResolvedValueOnce(segOk([{ speaker: 'a', text: '前半' }]))
      .mockResolvedValueOnce(segOk([{ speaker: 'a', text: '後半' }]));
    vi.stubGlobal('fetch', fetchMock);
    // 切段模式：whole=true，start/end 是這段在整場裡的絕對時間（第 2 段：30–60 分）
    const segs = await transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.5-flash', 1800, 3600, true);
    expect(segs.map((s) => s.text)).toEqual(['前半', '後半']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // 對半的時間範圍要用「檔案內的相對時間」，因為上傳的就是這一段的音檔
    expect(fetchMock.mock.calls[1][1].body).toContain('0:00 到 15:00');
    expect(fetchMock.mock.calls[2][1].body).toContain('15:00 到 30:00');
  });

  it('切不動時（長度不明）要把模型實際回了什麼帶進錯誤訊息', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ candidates: [{ finishReason: 'SAFETY' }] }));
    vi.stubGlobal('fetch', fetchMock);
    // 多檔模式：沒有時間範圍（start=end=0）→ 無從對半，只能報錯
    await expect(
      transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.5-flash', 0, 0, true)
    ).rejects.toThrow(/SAFETY/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('回應有內容但格式跑掉時，錯誤訊息要附上回應開頭', async () => {
    const fetchMock = vi.fn().mockResolvedValue(badJson('STOP'));
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.5-flash', 0, 0, true)
    ).rejects.toThrow(/這不是 JSON/);
  });

  it('被截斷（MAX_TOKENS）仍走原本的訊息，不與解析失敗混為一談', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ candidates: [{ finishReason: 'MAX_TOKENS', content: { parts: [{ text: '{"segments":[' }] } }] }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      transcribeRange([{ key: 'K1', fileUri: 'u' }], 'audio/wav', 'gemini-3.5-flash', 0, 0, true)
    ).rejects.toThrow(/內容太密集/);
  });
});

describe('重試時仍看得出跑到第幾段', () => {
  const segResp3 = (segs) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify({ segments: segs }) }] } }] });

  it('換金鑰重試的訊息要帶著段號，否則使用者無法判斷是不是做到一半', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(errResponse(429, { error: { code: 429 } })) // 第一把 → 429
      .mockResolvedValueOnce(segResp3([{ speaker: 'a', text: 'x' }])); // 第二把 → 成功
    vi.stubGlobal('fetch', fetchMock);
    const seen = [];
    const onProgress = (p) => seen.push(p && p.message);
    await transcribeRange(
      [{ key: 'K1', name: 'DD2', fileUri: 'u1' }, { key: 'K2', name: 'DD3', fileUri: 'u2' }],
      'audio/wav',
      'gemini-3.5-flash',
      1800,
      3600,
      false,
      onProgress,
      '辨識第 2/3 段（30:00–60:00）…'
    );
    const retry = seen.filter((s) => s && s.includes('切換金鑰重試中'));
    expect(retry.length).toBeGreaterThan(0);
    expect(retry[0]).toContain('第 2/3 段');
  });

  it('只有一把金鑰時，重試訊息一樣要帶段號', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(errResponse(503, { error: { code: 503 } }))
      .mockResolvedValueOnce(segResp3([{ speaker: 'a', text: 'x' }]));
    vi.stubGlobal('fetch', fetchMock);
    const seen = [];
    await transcribeRange(
      [{ key: 'K1', fileUri: 'u1' }],
      'audio/wav',
      'gemini-3.5-flash',
      0,
      1800,
      false,
      (p) => seen.push(p && p.message),
      '辨識第 1/3 段（00:00–30:00）…'
    );
    expect(seen.some((s) => s && s.includes('第 1/3 段') && s.includes('重試中'))).toBe(true);
    // 等待訊息也要帶段號。
    // 【2026-08-24】原本比對「暫時受限」四個字，而這個情境是 **503**，
    // 措辭已改成「型號忙線中」（503 不是金鑰的問題，見下面那支測試）。
    // 這支測試真正保護的是「**等待訊息要帶段號**」，不是那四個字，
    // 所以判準改成「等待訊息（兩種措辭都算）必須帶段號」——保護的東西一個字沒少。
    expect(
      seen.some((s) => s && s.includes('第 1/3 段') && (s.includes('暫時受限') || s.includes('忙線')))
    ).toBe(true);
    // 單把金鑰要先等一輪才會重試，這裡會真的睡 8 秒
  }, 20000);

  it('沒有段號的動作（例如摘要）不會多出奇怪的前綴', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(errResponse(429, { error: { code: 429 } }))
      .mockResolvedValueOnce(segResp3([{ speaker: 'a', text: 'x' }]));
    vi.stubGlobal('fetch', fetchMock);
    const seen = [];
    await transcribeRange(
      [{ key: 'K1', name: 'A', fileUri: 'u1' }, { key: 'K2', name: 'B', fileUri: 'u2' }],
      'audio/wav',
      'gemini-3.5-flash',
      0,
      600,
      true,
      (p) => seen.push(p && p.message),
      '辨識語者與逐字稿中…'
    );
    const retry = seen.filter((s) => s && s.includes('切換金鑰重試中'));
    // 這支測試保護的是「不得多出段號前綴」。後面的「（金鑰 2/2）」是刻意加的
    // 進度資訊（連線掛住時用來判斷是否還在動），不屬於前綴。
    expect(retry[0].startsWith('切換金鑰重試中…')).toBe(true);
    expect(retry[0]).not.toMatch(/第\s*\d+\s*\/\s*\d+\s*[段支]/);
  });
});

describe('學習筆記的指令品質', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
  const segs = Array.from({ length: 20 }, (_, i) => ({ speaker: '講者', text: `第 ${i} 句` }));

  it('關鍵數據要有排除清單：議程性數量與型號／世代名稱不算數據', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)).mockResolvedValueOnce(wrap({ figures: [] }));
    vi.stubGlobal('fetch', fetchMock);
    await enhanceNotesSection(segs, 'figures', 'KEY');
    const body = fetchMock.mock.calls[1][1].body;
    expect(body).toContain('不算'); // 明確的排除規則
    expect(body).toContain('講者'); // 幾位講者這類議程性數量
    expect(body).toContain('名稱的一部分'); // 5G、3D、B200 這類
  });

  it('一次生成的關鍵數據也套用同一套排除規則', async () => {
    clearModelCache();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ outline: [], concepts: [], tables: [], figures: [], quiz: [] }));
    vi.stubGlobal('fetch', fetchMock);
    await generateNotes(segs, 'KEY');
    const body = fetchMock.mock.calls[1][1].body;
    expect(body).toContain('不算');
    expect(body).toContain('名稱的一部分');
  });

  it('加強指令要強調「全部、不要精簡」，否則跟一次生成沒兩樣', async () => {
    for (const sec of ['outline', 'concepts', 'tables', 'quiz']) {
      clearModelCache();
      const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)).mockResolvedValueOnce(wrap({ [sec]: [] }));
      vi.stubGlobal('fetch', fetchMock);
      await enhanceNotesSection(segs, sec, 'KEY');
      const body = fetchMock.mock.calls[1][1].body;
      expect(body, `${sec} 的加強指令`).toContain('全部');
      expect(body, `${sec} 的加強指令`).toContain('不要精簡');
    }
  });
});

describe('錯誤訊息與 400→429 的處理', () => {
  it('400(thinking) 重試後變 429 → 視為暫時性換金鑰重試，不可報成「檔案格式不支援」', async () => {
    const ok = { candidates: [{ content: { parts: [{ text: JSON.stringify({ actionItems: [], mainPoints: ['ok'], qa: [] }) }] } }] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE)) // ListModels
      .mockResolvedValueOnce(errResponse(400, { error: { code: 400, status: 'INVALID_ARGUMENT' } })) // K1 帶 thinking → 400
      .mockResolvedValueOnce(errResponse(429, { error: { code: 429, message: 'You exceeded your current quota' } })) // K1 去掉 thinking → 429
      .mockResolvedValueOnce(jsonResponse(ok)); // K2 → 成功
    vi.stubGlobal('fetch', fetchMock);
    const r = await regenerateSummary([{ speaker: 's', text: 't' }], ['K1', 'K2']);
    expect(r.mainPoints).toEqual(['ok']);
  });

  it('純文字請求（沒有音檔）的 400，錯誤訊息不可叫使用者換音檔', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValue(errResponse(400, { error: { code: 400, message: 'bad request' } }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(regenerateSummary([{ speaker: 's', text: 't' }], 'KEY')).rejects.toThrow(/失敗 \(400\)/);
    await expect(regenerateSummary([{ speaker: 's', text: 't' }], 'KEY')).rejects.not.toThrow(/音檔/);
  });

  it('額度用盡（429）最終仍失敗時，訊息要講額度而不是檔案格式', async () => {
    // 重試退避總共會等 80 秒 → 用假時鐘快轉，否則測試會逾時
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValue(errResponse(429, { error: { code: 429, message: 'You exceeded your current quota' } }));
    vi.stubGlobal('fetch', fetchMock);
    const p = regenerateSummary([{ speaker: 's', text: 't' }], 'KEY').then(
      () => new Error('不該成功'),
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(200000);
    const err = await p;
    vi.useRealTimers();
    expect(String(err.message)).toMatch(/額度/);
    expect(String(err.message)).not.toMatch(/音檔.*格式/);
  });
});

describe('專有名詞：同一實體的多種寫法歸組', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
  // 300 段 → 兩批（240+60）；同一家公司在不同批被聽成不同寫法
  const segs = Array.from({ length: 300 }, (_, i) => ({ speaker: '講者', text: `第 ${i} 句` }));

  it('跨批次的不同寫法歸成一組：只留一筆，其餘放進 alts', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: [{ term: '合訊', category: 'org', fix: '' }, { term: 'TSV', category: 'term', fix: '' }] }))
      .mockResolvedValueOnce(wrap({ items: [{ term: '和迅', category: 'org', fix: '' }, { term: '禾訊', category: 'org', fix: '' }] }))
      .mockResolvedValueOnce(wrap({ groups: [{ terms: ['合訊', '和迅', '禾訊'], best: '禾迅' }] })); // 分組階段
    vi.stubGlobal('fetch', fetchMock);
    const r = await extractTerms(segs, 'KEY');
    const org = r.find((x) => x.cat === 'org');
    expect(org.t).toBe('合訊'); // 主寫法＝最先出現的
    expect(org.alts.sort()).toEqual(['和迅', '禾訊']);
    expect(org.fix).toBe('禾迅'); // 分組時順便給的建議寫法
    expect(r.map((x) => x.t)).toEqual(['合訊', 'TSV']); // 變體不再各自佔一列
    expect(fetchMock).toHaveBeenCalledTimes(4); // ListModels + 2 批 + 1 次分組
  });

  it('分組階段失敗不影響主流程，退回未分組的清單', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: [{ term: '合訊', category: 'org', fix: '' }, { term: '和迅', category: 'org', fix: '' }] }))
      .mockResolvedValueOnce(wrap({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ candidates: [{ content: { parts: [{ text: '不是JSON' }] } }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await extractTerms(segs, 'KEY');
    expect(r.map((x) => x.t)).toEqual(['合訊', '和迅']);
  });

  it('只有一個詞時不做分組（省一次呼叫）', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: [{ term: '合訊', category: 'org', fix: '' }] }))
      .mockResolvedValueOnce(wrap({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await extractTerms(segs, 'KEY');
    expect(r).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(3); // 沒有第 4 次
  });

  it('分組回傳不存在的詞會被忽略，不會憑空生出項目', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ items: [{ term: '合訊', category: 'org', fix: '' }, { term: 'TSV', category: 'term', fix: '' }] }))
      .mockResolvedValueOnce(wrap({ items: [] }))
      .mockResolvedValueOnce(wrap({ groups: [{ terms: ['合訊', '不存在的詞'], best: '禾迅' }] }));
    vi.stubGlobal('fetch', fetchMock);
    const r = await extractTerms(segs, 'KEY');
    expect(r.find((x) => x.t === '合訊').alts).toEqual([]);
    expect(r.map((x) => x.t)).toEqual(['合訊', 'TSV']);
  });
});

describe('關鍵數據改為「分組＋標籤／數值」結構', () => {
  const wrap = (obj) => jsonResponse({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });
  const segs = Array.from({ length: 20 }, (_, i) => ({ speaker: '講者', text: `第 ${i} 句` }));

  it('新結構：每筆有 group／label／value', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(
        wrap({
          outline: [], concepts: [], tables: [], quiz: [],
          figures: [
            { group: '市場規模', label: '2024 年 FOPLP／GCS', value: '6.51 億美元' },
            { group: '市場規模', label: '2030 年（預估）', value: '81.1 億美元' },
            { group: '公司與團隊', label: '東捷科技成立', value: '1998 年' },
          ],
        })
      );
    vi.stubGlobal('fetch', fetchMock);
    const n = await generateNotes(segs, 'KEY');
    expect(n.figures[0]).toEqual({ group: '市場規模', label: '2024 年 FOPLP／GCS', value: '6.51 億美元' });
    expect(n.figures.map((f) => f.group)).toEqual(['市場規模', '市場規模', '公司與團隊']);
  });

  it('舊資料（純字串）自動轉成新結構，不會壞掉', async () => {
    clearModelCache();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ outline: [], concepts: [], tables: [], quiz: [], figures: ['10.8%：志聖投資東捷科技之持股比例', '沒有冒號的舊資料'] }));
    vi.stubGlobal('fetch', fetchMock);
    const n = await generateNotes(segs, 'KEY');
    // 舊格式是「數值：說明」→ 轉成標籤在前、數值在後
    expect(n.figures[0]).toEqual({ group: '', label: '志聖投資東捷科技之持股比例', value: '10.8%' });
    expect(n.figures[1]).toEqual({ group: '', label: '沒有冒號的舊資料', value: '' });
  });

  it('缺 label 或 value 的項目會被丟掉，不留半截資料', async () => {
    clearModelCache();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ outline: [], concepts: [], tables: [], quiz: [], figures: [{ group: 'g', label: '', value: '5%' }, { group: 'g', label: 'ok', value: '1 年' }] }));
    vi.stubGlobal('fetch', fetchMock);
    const n = await generateNotes(segs, 'KEY');
    expect(n.figures).toHaveLength(1);
    expect(n.figures[0].label).toBe('ok');
  });

  it('提示詞要求分組，並把「兩者比較」的數據改放對照表', async () => {
    clearModelCache();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(MODELS_RESPONSE))
      .mockResolvedValueOnce(wrap({ outline: [], concepts: [], tables: [], figures: [], quiz: [] }));
    vi.stubGlobal('fetch', fetchMock);
    await generateNotes(segs, 'KEY');
    const body = fetchMock.mock.calls[1][1].body;
    expect(body).toContain('group');
    expect(body).toContain('對照表'); // 比較型改放表格
  });
});

describe('上傳金鑰數上限（避免金鑰一多就上傳爆炸）', () => {
  it('金鑰超過上限時只挑幾把上傳，不是每把都傳', () => {
    const keys = ['K1', 'K2', 'K3', 'K4', 'K5', 'K6'].map((k) => ({ name: k, key: k }));
    expect(pickUploadKeys(keys, 3)).toHaveLength(3);
    expect(pickUploadKeys(keys, 3).every((k) => keys.some((x) => x.key === k.key))).toBe(true);
  });

  it('金鑰數少於上限時全部都用', () => {
    const keys = [{ name: 'A', key: 'A' }, { name: 'B', key: 'B' }];
    expect(pickUploadKeys(keys, 3).map((k) => k.key)).toEqual(['A', 'B']);
  });

  it('優先挑「今日用量少」的，冷卻中的排最後', () => {
    recordCooldown('BUSY', 60000);
    for (let i = 0; i < 5; i++) recordUse('USED');
    const keys = [
      { name: 'busy', key: 'BUSY' },
      { name: 'used', key: 'USED' },
      { name: 'fresh', key: 'FRESH' },
    ];
    expect(pickUploadKeys(keys, 2).map((k) => k.key)).toEqual(['FRESH', 'USED']);
  });
});

describe('長錄音無法切割時的處置', () => {
  it('切割失敗且錄音超過單一時間窗 → 明確拒絕，不可退回整檔模式', () => {
    // 整檔模式下每次請求都要送完整音檔，180 分鐘 = 34.5 萬 token × 多次，免費層必定卡死
    expect(canUseWholeMode(180 * 60)).toBe(false);
    expect(canUseWholeMode(60 * 60)).toBe(false);
  });

  it('錄音在單一時間窗以內 → 整檔模式可用（只送一次）', () => {
    expect(canUseWholeMode(30 * 60)).toBe(true);
    expect(canUseWholeMode(40 * 60)).toBe(true);
  });

  it('長度未知（0）時保守允許，避免擋掉本來能跑的短檔', () => {
    expect(canUseWholeMode(0)).toBe(true);
  });
});
