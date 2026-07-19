import { beforeEach, describe, it, expect } from 'vitest';
import { getApiKey, getApiKeys, getApiKeyEntries, setApiKeyEntries, setApiKey, hasApiKey } from '../js/settings.js';

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

  it('具名金鑰：存取 entries、忽略空 key、去空白', () => {
    setApiKeyEntries([
      { name: '私人', key: ' k1 ' },
      { name: '', key: '' },
      { name: '公司', key: 'k2' },
    ]);
    expect(getApiKeyEntries()).toEqual([
      { name: '私人', key: 'k1' },
      { name: '公司', key: 'k2' },
    ]);
    expect(getApiKeys()).toEqual(['k1', 'k2']);
  });
});
