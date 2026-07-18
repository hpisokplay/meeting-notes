const KEY = 'gemini_api_key';

export function getApiKey() {
  return localStorage.getItem(KEY) || '';
}

export function setApiKey(key) {
  localStorage.setItem(KEY, (key || '').trim());
}

export function hasApiKey() {
  return getApiKey().length > 0;
}
