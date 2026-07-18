import { beforeEach, describe, it, expect } from 'vitest';
import { getApiKey, setApiKey, hasApiKey } from '../js/settings.js';

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
});
