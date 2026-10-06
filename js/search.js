import { itemText, ALL_SUMMARY_KEYS } from './items.js';

// 會議搜尋：比對標題、逐字稿、摘要文字（不分大小寫）。條目可能是字串或 {text} 物件。
export function meetingSearchText(m) {
  const s = m.summary || {};
  const segs = (m.transcript || []).map((x) => `${x.speaker} ${x.text}`).join(' ');
  const items = [];
  for (const k of ALL_SUMMARY_KEYS) if (Array.isArray(s[k])) items.push(...s[k].map(itemText));
  return [m.title || '', segs, ...items].join(' ').toLowerCase();
}

export function matchMeeting(m, query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return true;
  return meetingSearchText(m).includes(q);
}
