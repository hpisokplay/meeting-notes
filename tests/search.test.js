import { describe, it, expect } from 'vitest';
import { matchMeeting, meetingSearchText } from '../js/search.js';

const m = {
  title: '分戶帳教育訓練',
  transcript: [{ speaker: '說話者1', text: '電子存摺可交割' }],
  summary: { actionItems: ['整理教材 [DRI: 逸翔]'], mainPoints: ['開戶流程'], qa: [] },
};

describe('search', () => {
  it('比對標題', () => expect(matchMeeting(m, '分戶帳')).toBe(true));
  it('比對逐字稿內容', () => expect(matchMeeting(m, '存摺')).toBe(true));
  it('比對摘要（待辦/重點）', () => {
    expect(matchMeeting(m, '教材')).toBe(true);
    expect(matchMeeting(m, '流程')).toBe(true);
  });
  it('不分大小寫', () => {
    const e = { title: 'Meeting ABC', transcript: [], summary: {} };
    expect(matchMeeting(e, 'abc')).toBe(true);
  });
  it('不符合回傳 false', () => expect(matchMeeting(m, '完全不存在xyz')).toBe(false));
  it('空查詢視為全部符合', () => expect(matchMeeting(m, '')).toBe(true));
  it('meetingSearchText 串起所有欄位', () => {
    expect(meetingSearchText(m)).toContain('分戶帳');
    expect(meetingSearchText(m)).toContain('存摺');
    expect(meetingSearchText(m)).toContain('教材');
  });
});
