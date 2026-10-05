// api.js：前端與 NAS 伺服器之間唯一的通道（fetch 全部用假的）
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import * as api from '../js/api.js';

const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

// 依「方法 路徑」路由的假 fetch；handler 回傳 Response
function fakeFetch(handler) {
  const calls = [];
  const fn = vi.fn(async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const path = String(url).replace(/^https?:\/\/[^/]+/, '');
    calls.push({ method, path, body: opts.body });
    return handler(method, path, opts);
  });
  fn.calls = calls;
  return fn;
}

// 9 bytes、chunkSize 4 → 3 塊（4+4+1）
const makeFile = () => new File([new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9])], 'm.m4a', { type: 'audio/mp4', lastModified: 123 });

beforeEach(() => {
  localStorage.clear();
  api.config.backoffMs = 1;
  api.config.pollMs = 1;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('upload', () => {
  it('resumes upload from server-reported chunks', async () => {
    const file = makeFile();
    localStorage.setItem('upload:m.m4a:9:123', 'u1');
    const f = fakeFetch((method, path) => {
      if (method === 'GET' && path === '/api/uploads/u1')
        return json(200, { uploadId: 'u1', chunkSize: 4, chunks: 3, received: [0, 1] });
      if (method === 'PUT' && path.startsWith('/api/uploads/u1/')) return json(200, { ok: true });
      return json(500, { detail: 'unexpected ' + method + ' ' + path });
    });
    vi.stubGlobal('fetch', f);
    const progress = [];
    const uid = await api.uploadFile(file, (p) => progress.push(p));
    expect(uid).toBe('u1');
    const puts = f.calls.filter((c) => c.method === 'PUT');
    expect(puts.map((c) => c.path)).toEqual(['/api/uploads/u1/2']);
    // 最後一塊只有 1 byte
    expect(puts[0].body.size).toBe(1);
    expect(f.calls.some((c) => c.method === 'POST')).toBe(false);
    expect(progress[progress.length - 1].sent).toBe(9);
  });

  it('starts a new upload when the stored one is gone (404)', async () => {
    const file = makeFile();
    localStorage.setItem('upload:m.m4a:9:123', 'old');
    const f = fakeFetch((method, path, opts) => {
      if (method === 'GET' && path === '/api/uploads/old') return json(404, { detail: 'gone' });
      if (method === 'POST' && path === '/api/uploads') {
        expect(JSON.parse(opts.body)).toEqual({ name: 'm.m4a', size: 9, mime: 'audio/mp4' });
        return json(200, { uploadId: 'u2', chunkSize: 4, chunks: 3, received: [] });
      }
      if (method === 'PUT') return json(200, {});
      return json(500, { detail: 'unexpected' });
    });
    vi.stubGlobal('fetch', f);
    const uid = await api.uploadFile(file);
    expect(uid).toBe('u2');
    expect(localStorage.getItem('upload:m.m4a:9:123')).toBe('u2');
    expect(f.calls.filter((c) => c.method === 'PUT').map((c) => c.path)).toEqual([
      '/api/uploads/u2/0',
      '/api/uploads/u2/1',
      '/api/uploads/u2/2',
    ]);
  });

  it('retries a failed chunk then succeeds', async () => {
    const file = makeFile();
    let fails = 2;
    const f = fakeFetch((method, path) => {
      if (method === 'POST' && path === '/api/uploads') return json(200, { uploadId: 'u3', chunkSize: 4, chunks: 3, received: [0, 2] });
      if (method === 'PUT' && path === '/api/uploads/u3/1') {
        if (fails-- > 0) return json(503, { detail: 'busy' });
        return json(200, {});
      }
      return json(500, { detail: 'unexpected' });
    });
    vi.stubGlobal('fetch', f);
    expect(await api.uploadFile(file)).toBe('u3');
    expect(f.calls.filter((c) => c.method === 'PUT').length).toBe(3);
  });

  it('gives up after 3 retries with the server error text', async () => {
    const file = makeFile();
    const f = fakeFetch((method, path) => {
      if (method === 'POST') return json(200, { uploadId: 'u4', chunkSize: 4, chunks: 3, received: [] });
      return json(507, { detail: '磁碟已滿' });
    });
    vi.stubGlobal('fetch', f);
    await expect(api.uploadFile(file)).rejects.toThrow('磁碟已滿');
    expect(f.calls.filter((c) => c.method === 'PUT').length).toBe(4); // 1 次 + 重試 3 次
  });

  it('createMeeting clears the resume keys of the used uploads', async () => {
    localStorage.setItem('upload:a.m4a:1:1', 'u1');
    localStorage.setItem('upload:b.m4a:1:1', 'u9');
    const f = fakeFetch((method, path, opts) => {
      expect(JSON.parse(opts.body)).toEqual({ title: 'T', group: null, uploads: ['u1'] });
      return json(200, { id: 'm1', status: 'queued' });
    });
    vi.stubGlobal('fetch', f);
    const m = await api.createMeeting({ title: 'T', group: null, uploads: ['u1'] });
    expect(m.id).toBe('m1');
    expect(localStorage.getItem('upload:a.m4a:1:1')).toBeNull();
    expect(localStorage.getItem('upload:b.m4a:1:1')).toBe('u9');
  });
});

describe('jobs', () => {
  it('waitJob resolves on done and rejects on failed with server error text', async () => {
    let n = 0;
    vi.stubGlobal(
      'fetch',
      fakeFetch((method, path) => {
        if (path === '/api/jobs/j1') {
          n++;
          return json(200, n < 3 ? { id: 'j1', state: 'running', progress: { stage: 'ai', pct: n * 30, msg: '處理中' } } : { id: 'j1', state: 'done', progress: { pct: 100 } });
        }
        if (path === '/api/jobs/j2') return json(200, { id: 'j2', state: 'failed', error: 'Claude 用量已達上限' });
        return json(404, { detail: 'no' });
      })
    );
    const seen = [];
    const done = await api.waitJob('j1', (p) => seen.push(p && p.pct));
    expect(done.state).toBe('done');
    expect(seen).toContain(30);
    await expect(api.waitJob('j2')).rejects.toThrow('Claude 用量已達上限');
  });

  it('waitJob keeps polling through transient offline / 5xx errors', async () => {
    let n = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        n++;
        if (n === 1) throw new TypeError('Failed to fetch');
        if (n === 2) return json(502, { detail: 'bad gateway' });
        return json(200, { id: 'j5', state: 'done' });
      })
    );
    expect((await api.waitJob('j5')).state).toBe('done');
    expect(n).toBe(3);
  });

  it('waitJob gives up when the errors persist past maxPollFailMs, and at once on 404', async () => {
    api.config.maxPollFailMs = 20;
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const e = await api.waitJob('j6').catch((x) => x);
    expect(e.offline).toBe(true);
    api.config.maxPollFailMs = 5 * 60 * 1000;
    let calls = 0;
    vi.stubGlobal('fetch', vi.fn(async () => { calls++; return json(404, { detail: '找不到工作' }); }));
    await expect(api.waitJob('j7')).rejects.toThrow('找不到工作');
    expect(calls).toBe(1);
  });

  it('waitJob rejects with 已取消 when the job is cancelled', async () => {
    vi.stubGlobal('fetch', fakeFetch(() => json(200, { id: 'j3', state: 'cancelled' })));
    await expect(api.waitJob('j3')).rejects.toThrow('已取消');
  });

  it('runAI posts the action and params and returns the jobId', async () => {
    const f = fakeFetch((method, path, opts) => {
      expect(method).toBe('POST');
      expect(path).toBe('/api/meetings/m%201/ai');
      expect(JSON.parse(opts.body)).toEqual({ action: 'enhance', section: 'qa' });
      return json(200, { jobId: 'j9' });
    });
    vi.stubGlobal('fetch', f);
    expect(await api.runAI('m 1', 'enhance', { section: 'qa' })).toBe('j9');
  });
});

describe('errors and urls', () => {
  it('throws Error with the server detail text', async () => {
    vi.stubGlobal('fetch', fakeFetch(() => json(404, { detail: '找不到這場會議' })));
    await expect(api.getMeeting('x')).rejects.toThrow('找不到這場會議');
  });

  it('validation detail arrays become readable text', async () => {
    vi.stubGlobal('fetch', fakeFetch(() => json(422, { detail: [{ msg: 'field required' }] })));
    await expect(api.saveSettings({})).rejects.toThrow('field required');
  });

  it('network failure is marked offline', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const e = await api.listMeetings().catch((x) => x);
    expect(e.offline).toBe(true);
  });

  it('saveMeeting PUTs the whole meeting', async () => {
    const f = fakeFetch((method, path, opts) => json(200, { ...JSON.parse(opts.body), updatedAt: 5 }));
    vi.stubGlobal('fetch', f);
    const r = await api.saveMeeting({ id: 'a', title: 'x' });
    expect(f.calls[0]).toMatchObject({ method: 'PUT', path: '/api/meetings/a' });
    expect(r.updatedAt).toBe(5);
  });

  it('builds file urls', () => {
    expect(api.audioUrl('a b', 0)).toBe('/api/meetings/a%20b/audio/0');
    expect(api.exportAllUrl()).toBe('/api/export');
    expect(api.docxUrl('a')).toBe('/api/meetings/a/export.docx');
  });
});
