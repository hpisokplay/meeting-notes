import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { start, stop, onVisible, isHeld, isSupported } from '../js/wakelock.js';

// 假的 Screen Wake Lock：可模擬「系統自動釋放」
function installFakeWakeLock() {
  const state = { requests: 0, sentinels: [] };
  navigator.wakeLock = {
    request: async () => {
      state.requests++;
      const listeners = [];
      const s = {
        released: false,
        addEventListener: (t, fn) => t === 'release' && listeners.push(fn),
        release: async () => {
          s.released = true;
          listeners.forEach((f) => f());
        },
        systemRelease: () => {
          // 系統在頁面轉背景時自動釋放（規範行為，不是我們呼叫的）
          s.released = true;
          listeners.forEach((f) => f());
        },
      };
      state.sentinels.push(s);
      return s;
    },
  };
  return state;
}

describe('螢幕恆亮', () => {
  beforeEach(async () => {
    await stop();
  });
  afterEach(() => {
    delete navigator.wakeLock;
  });

  it('start 會取得螢幕鎖', async () => {
    const st = installFakeWakeLock();
    await start();
    expect(st.requests).toBe(1);
    expect(isHeld()).toBe(true);
  });

  it('系統自動釋放後，回到前景要重新取得（否則螢幕從此不再恆亮）', async () => {
    const st = installFakeWakeLock();
    await start();
    st.sentinels[0].systemRelease(); // 模擬切到背景被系統收走
    expect(isHeld()).toBe(false);
    await onVisible(); // 使用者回到 App
    expect(st.requests).toBe(2);
    expect(isHeld()).toBe(true);
  });

  it('stop 之後回到前景不再申請（任務已結束就別佔著螢幕）', async () => {
    const st = installFakeWakeLock();
    await start();
    await stop();
    expect(isHeld()).toBe(false);
    await onVisible();
    expect(st.requests).toBe(1);
  });

  it('重複 start 不會疊加多把鎖', async () => {
    const st = installFakeWakeLock();
    await start();
    await start();
    expect(st.requests).toBe(1);
  });

  it('裝置不支援時不丟錯，isSupported 回 false', async () => {
    delete navigator.wakeLock;
    expect(isSupported()).toBe(false);
    await expect(start()).resolves.toBeUndefined();
    expect(isHeld()).toBe(false);
  });

  it('申請被拒絕（例如低電量模式）時不丟錯', async () => {
    navigator.wakeLock = { request: async () => { throw new Error('NotAllowedError'); } };
    await expect(start()).resolves.toBeUndefined();
    expect(isHeld()).toBe(false);
  });
});
