import { beforeEach, describe, it, expect } from 'vitest';
import { recordUse, recordCooldown, getKeyStatus } from '../js/usage.js';

beforeEach(() => localStorage.clear());

describe('usage', () => {
  it('累計今日使用次數', () => {
    recordUse('k1');
    recordUse('k1');
    recordUse('k2');
    expect(getKeyStatus('k1').count).toBe(2);
    expect(getKeyStatus('k2').count).toBe(1);
    expect(getKeyStatus('unknown').count).toBe(0);
  });

  it('冷卻：記錄後回傳剩餘秒數', () => {
    recordCooldown('k1', 5000);
    const st = getKeyStatus('k1');
    expect(st.cooling).toBeGreaterThan(0);
    expect(st.cooling).toBeLessThanOrEqual(5);
    expect(getKeyStatus('k2').cooling).toBe(0);
  });
});
