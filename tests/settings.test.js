import { beforeEach, describe, it, expect } from 'vitest';
import { getApiKey, getApiKeys, setApiKey, hasApiKey } from '../js/settings.js';

beforeEach(() => localStorage.clear());

describe('settings', () => {
  it('預設沒有金鑰', () => {
    expect(getApiKey()).toBe('');
    expect(hasApiKey()).toBe(false);
  });

  it('設定後可讀回並去除空白', () => {
    setApiKey('  abc123  ');
    expect(getApiKey()).toBe('abc123');
    expect(hasApiKey()).toBe(true);
  });

  it('多把金鑰：一行一把、去重、getApiKey 取第一把', () => {
    setApiKey('key1\nkey2\n key2 \n\nkey3');
    expect(getApiKeys()).toEqual(['key1', 'key2', 'key3']);
    expect(getApiKey()).toBe('key1');
  });
});
