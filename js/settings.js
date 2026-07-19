const KEY = 'gemini_api_key';

// 原始文字（可含多把金鑰，一行一把）
export function getApiKeysRaw() {
  return localStorage.getItem(KEY) || '';
}
// 解析成金鑰陣列（依換行或逗號分隔、去空白、去重複）
export function getApiKeys() {
  const arr = getApiKeysRaw()
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  return Array.from(new Set(arr));
}
// 第一把（向後相容用）
export function getApiKey() {
  return getApiKeys()[0] || '';
}
export function setApiKey(value) {
  localStorage.setItem(KEY, (value || '').trim());
}
export function hasApiKey() {
  return getApiKeys().length > 0;
}
