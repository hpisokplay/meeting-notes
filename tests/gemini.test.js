import { describe, it, expect, vi, beforeEach } from 'vitest';
import { transcribeAndSummarize } from '../js/gemini.js';

beforeEach(() => {
  vi.restoreAllMocks();
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
                  keyPoints: ['重點A'],
                  actionItems: ['待辦B'],
                  decisions: ['決議C'],
                }),
              },
            ],
          },
        },
      ],
    };
    const fetchMock = vi
      .fn()
      // 1) start resumable → 回傳 upload url header
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up.example/put' }))
      // 2) upload bytes finalize → 回傳 file ACTIVE
      .mockResolvedValueOnce(
        jsonResponse({ file: { uri: 'https://files/abc', name: 'files/abc', state: 'ACTIVE', mimeType: 'audio/mp4' } })
      )
      // 3) generateContent → 回傳結構化 JSON
      .mockResolvedValueOnce(jsonResponse(modelJson));
    vi.stubGlobal('fetch', fetchMock);

    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'meeting.m4a';

    const result = await transcribeAndSummarize(file, 'KEY');
    expect(result.transcript).toHaveLength(2);
    expect(result.transcript[0]).toEqual({ speaker: '說話者1', text: '大家好' });
    expect(result.transcript[1].speaker).toBe('說話者2');
    expect(result.summary.keyPoints).toEqual(['重點A']);
    expect(result.summary.actionItems).toEqual(['待辦B']);
    expect(result.summary.decisions).toEqual(['決議C']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('逐字稿被截斷（MAX_TOKENS）時給出可理解的錯誤', async () => {
    const truncated = {
      candidates: [{ finishReason: 'MAX_TOKENS', content: { parts: [{ text: '{"segments":[{"speaker":"說話者1' }] } }],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({}, { 'X-Goog-Upload-URL': 'https://up.example/put' }))
      .mockResolvedValueOnce(
        jsonResponse({ file: { uri: 'https://files/abc', name: 'files/abc', state: 'ACTIVE', mimeType: 'audio/mp4' } })
      )
      .mockResolvedValueOnce(jsonResponse(truncated));
    vi.stubGlobal('fetch', fetchMock);

    const file = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mp4' });
    file.name = 'long.m4a';
    await expect(transcribeAndSummarize(file, 'KEY')).rejects.toThrow('太長');
  });
});
