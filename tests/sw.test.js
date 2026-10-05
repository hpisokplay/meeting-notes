// sw.js：/api 與 Range 不攔；離線且快取沒有時退回 index.html 或最小回應
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const src = readFileSync(resolve(process.cwd(), 'sw.js'), 'utf-8');

function loadSw(cacheEntries) {
  const handlers = {};
  const self = { addEventListener: (t, fn) => (handlers[t] = fn), skipWaiting: () => {}, clients: { claim: () => {} } };
  const store = new Map(Object.entries(cacheEntries));
  const keyOf = (r) => (typeof r === 'string' ? r : new URL(r.url).pathname);
  const caches = {
    match: async (r) => store.get(keyOf(r) === '/' ? './' : keyOf(r)) || store.get(keyOf(r)) || undefined,
    open: async () => ({ put: async () => {}, addAll: async () => {} }),
    keys: async () => [],
    delete: async () => true,
  };
  const location = { origin: 'https://nas.test' };
  const fetch = async () => {
    throw new TypeError('offline');
  };
  new Function('self', 'caches', 'location', 'fetch', 'Response', src)(self, caches, location, fetch, Response);
  return async (url, init = {}) => {
    let responded = null;
    const { mode, ...rest } = init;
    const req = new Request('https://nas.test' + url, rest);
    Object.defineProperty(req, 'mode', { value: mode || 'cors' });
    handlers.fetch({ request: req, respondWith: (p) => (responded = p) });
    return responded ? await responded : null;
  };
}

describe('service worker', () => {
  it('/api/* 與 Range 請求不攔截（只走網路）', async () => {
    const fire = loadSw({});
    expect(await fire('/api/meetings')).toBeNull();
    expect(await fire('/app.m4a', { headers: { Range: 'bytes=0-' } })).toBeNull();
  });

  it('離線導覽且快取沒有該頁 → 回快取的 index.html', async () => {
    const fire = loadSw({ './index.html': new Response('<html>shell</html>') });
    const res = await fire('/?fresh=1', { mode: 'navigate', headers: { Accept: 'text/html' } });
    expect(await res.text()).toContain('shell');
  });

  it('離線且什麼都沒快取 → 最小的 503 回應（不是網路錯誤）', async () => {
    const fire = loadSw({});
    const res = await fire('/js/unknown.js');
    expect(res.status).toBe(503);
    expect(await res.text()).toContain('離線');
  });
});
