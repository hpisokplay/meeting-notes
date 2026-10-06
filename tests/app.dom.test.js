// 整合測試：以 jsdom 實際載入 app.js（api.js 換成記憶體裡的假 NAS），渲染清單、詳情、進度、設定頁。
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as api from '../js/api.js';

const nas = vi.hoisted(() => ({ meetings: {}, groups: [], jobs: {}, calls: [] }));
vi.mock('../js/api.js', () => {
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const notFound = () => Object.assign(new Error('找不到'), { status: 404 });
  return {
    config: {},
    listMeetings: vi.fn(async () => Object.values(nas.meetings).map(({ transcript, ...rest }) => clone(rest))),
    getMeeting: vi.fn(async (id) => {
      if (!nas.meetings[id]) throw notFound();
      return clone(nas.meetings[id]);
    }),
    // 跟真伺服器一樣：review 由伺服器管（用戶端送來的不算）；摘要／筆記／逐字稿有變就重新驗證（簡化版：
    // 指向已不存在條目的 pending 項目消失；已走新流程的會議裡沒附原句的摘要條目 → 新的待確認項目）
    saveMeeting: vi.fn(async (m) => {
      nas.calls.push(['saveMeeting', m.id]);
      const prev = nas.meetings[m.id];
      const next = clone(m);
      if (prev) {
        if (prev.review) next.review = clone(prev.review);
        else delete next.review;
        const changed = ['summary', 'notes', 'transcript'].some((k) => JSON.stringify(prev[k]) !== JSON.stringify(next[k]));
        if (changed && next.review) {
          const idsOf = (sec) => {
            const [b, k] = String(sec).split('.');
            return (((next[b] || {})[k]) || []).map((x) => (x && typeof x === 'object' ? x.id : x));
          };
          const items = next.review.items.filter((r) => r.state !== 'pending' || !r.itemId || idsOf(r.section).includes(r.itemId));
          Object.entries(next.summary || {}).forEach(([k, arr]) =>
            (arr || []).forEach((x) => {
              const ok = x && typeof x === 'object' && Array.isArray(x.src) && x.src.some((q) => q && q.quote);
              const rid = 'nq-' + k + '-' + (typeof x === 'string' ? x : x.text);
              if (!ok && !items.some((r) => r.id === rid))
                items.push({ id: rid, kind: 'quote', section: 'summary.' + k, itemId: typeof x === 'string' ? x : x.id, text: typeof x === 'string' ? x : x.text, detail: '沒有附逐字稿原句，請確認。', state: 'pending' });
            })
          );
          next.review.items = items;
        }
      }
      nas.meetings[m.id] = next;
      return clone(next);
    }),
    deleteMeeting: vi.fn(async (id) => {
      delete nas.meetings[id];
    }),
    getGroups: vi.fn(async () => clone(nas.groups)),
    saveGroups: vi.fn(async (g) => {
      nas.groups = clone(g);
    }),
    getStatus: vi.fn(async () => ({ version: 'v100', worker: { online: true, lastSeen: Date.now() }, queue: 2, claudeModel: 'sonnet', diskFreeGB: 812 })),
    getSettings: vi.fn(async () => ({ claudeModel: 'sonnet', fallbackEnabled: true, fallbackMinutes: 20, tgNotify: true })),
    saveSettings: vi.fn(async (s) => s),
    startUpload: vi.fn(),
    uploadFile: vi.fn(),
    createMeeting: vi.fn(),
    // 假 AI：enhance 直接寫回伺服器端的會議（與真伺服器一樣先備份到 alt）
    runAI: vi.fn(async (id, action, params) => {
      nas.calls.push(['runAI', id, action, params]);
      const m = nas.meetings[id];
      if (action === 'enhance') {
        m.alt = m.alt || {};
        m.alt['summary:' + params.section] = (m.summary[params.section] || []).slice();
        m.summary[params.section] = ['加強後的重點一', '加強後的重點二'];
      }
      return 'job-1';
    }),
    jobIdOf: (r) => (r && (r.jobId || r.aiJobId)) || null,
    // 待確認清單：照契約改狀態、edit 改條目文字、delete 移掉條目，回傳更新後的會議
    acceptHighReview: vi.fn(async () => { throw new Error('unmocked'); }),
    applyReviewBatch: vi.fn(async () => { throw new Error('unmocked'); }),
    reviewAction: vi.fn(async (id, rid, action, text) => {
      nas.calls.push(['reviewAction', id, rid, action, text]);
      const m = nas.meetings[id];
      const r = m.review.items.find((x) => x.id === rid);
      const [, key] = r.section.split('.');
      const arr = m.summary[key];
      const at = arr.findIndex((x) => x.id === r.itemId);
      if (action === 'edit' && at >= 0) arr[at].text = text;
      if (action === 'delete' && at >= 0) arr.splice(at, 1);
      r.state = { confirm: 'confirmed', edit: 'edited', delete: 'deleted' }[action];
      m.updatedAt = (m.updatedAt || 0) + 1;
      return clone(m);
    }),
    setMeetingMode: vi.fn(async (id, mode) => {
      nas.calls.push(['setMeetingMode', id, mode]);
      nas.meetings[id].mode = mode;
      return { jobId: 'job-m' };
    }),
    restoreRaw: vi.fn(async (id) => {
      nas.calls.push(['restoreRaw', id]);
      return { jobId: 'job-r' };
    }),
    getJob: vi.fn(async () => ({ state: 'done' })),
    waitJob: vi.fn(async () => ({ state: 'done' })),
    cancelJob: vi.fn(async () => {}),
    retranscribe: vi.fn(async () => ({})),
    audioUrl: (id, n) => `/api/meetings/${id}/audio/${n}`,
    importBackup: vi.fn(async () => ({ added: 1, updated: 0, kept: 0, groups: 0 })),
    exportAllUrl: () => '/api/export',
    docxUrl: (id) => `/api/meetings/${id}/export.docx`,
  };
});

const BODY = `
  <header class="topbar">
    <button id="backBtn" class="ghost" hidden>‹ 返回</button>
    <h1 id="title">會議記錄</h1>
    <button id="settingsBtn" class="ghost">⚙︎</button>
  </header>
  <main id="view"></main>
  <nav class="tabbar">
    <button id="homeTab">📋 清單</button>
    <button id="groupsTab">📂 分類</button>
    <button id="newTab" class="primary">＋ 新增會議</button>
  </nav>`;

const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const go = async (hash) => {
  location.hash = hash;
  window.dispatchEvent(new Event('hashchange'));
  await tick();
};

const MEETING = {
  id: 'test-1',
  title: '產品週會',
  createdAt: new Date('2026-07-18T10:00:00').getTime(),
  updatedAt: 1,
  status: 'done',
  audio: [
    { file: '0-a.m4a', name: 'a.m4a', size: 10, durationSec: 600 },
    { file: '1-b.m4a', name: 'b.m4a', size: 10, durationSec: 600 },
  ],
  transcript: [
    { speaker: '說話者1', text: '討論上線時程', t: 5 },
    { speaker: '說話者2', text: '下週三比較穩', t: 300 },
    { speaker: '說話者3', text: '我明天開始測試', t: 700 },
  ],
  summary: {
    actionItems: ['說話者3 明天開始測試 [DRI: 說話者3]'],
    mainPoints: ['討論上線時程'],
    qa: [],
  },
};

describe('app（整合，假 NAS）', () => {
  let appMod;
  beforeEach(async () => {
    window.confirm = () => true;
    window.alert = () => {};
    nas.meetings = { 'test-1': JSON.parse(JSON.stringify(MEETING)) };
    nas.groups = [];
    nas.calls = [];
    if (!appMod) {
      // app.js 載入時就抓住 #view 等節點 → 只能在載入前設一次 DOM
      document.body.innerHTML = BODY;
      // jsdom 沒有實作媒體播放
      HTMLMediaElement.prototype.pause = () => {};
      HTMLMediaElement.prototype.load = () => {};
      HTMLMediaElement.prototype.play = () => Promise.resolve();
      location.hash = '#/';
      appMod = await import('../js/app.js');
      await tick();
    }
  });

  it('清單、詳情、分類頁都能從 NAS 資料渲染', async () => {
    await go('#/');
    const view = document.getElementById('view');
    expect(view.innerHTML).toContain('產品週會');
    expect(view.innerHTML).toContain('grp-chip');
    expect(view.innerHTML).toContain('未分類');

    await go('#/m/test-1');
    const html = document.getElementById('view').innerHTML;
    expect(html).toContain('說話者1');
    expect(html).toContain('說話者2');
    expect(html).toContain('說話者3');
    expect(html).toContain('待辦事項 Action Item');
    expect(html).toContain('會議重點 Main Point');
    expect(html).toContain('會議提問');
    expect(html).toContain('[DRI: 說話者3]');
    expect(html).toMatch(/會議提問[\s\S]*?無/);
    expect(html).toMatch(/class="spk" style="color:#/);
    expect(html).toContain('📄 PDF');
    expect(html).toContain('📝 Word');
    expect(html).toContain('📤 分享');
    expect(html).toContain('加強待辦');
    expect(html).toContain('data-enh="qa"');
    expect(html).toContain('spk-chip');
    expect(html).toContain('English');
    expect(html).toContain('data-i="0"');
    expect(html).toContain('data-sec="ai"');
    expect(html).toContain('data-sec="tr"');
    expect(html).toContain('chev');
    expect(html).toContain('data-jump="ai:0"');
    expect(html).toContain('問這場會議');
    expect(html).toContain('chatAsk');
    expect(html).toContain('專有名詞訂正');
    expect(html).toContain('scanTerms');
    // 不再有任何金鑰相關提示
    expect(html).not.toContain('Gemini');

    nas.groups = [{ id: 'g1', name: '客戶會議', color: '#0a84ff' }];
    await go('#/groups');
    await tick();
    const gHtml = document.getElementById('view').innerHTML;
    expect(gHtml).toContain('客戶會議');
    expect(gHtml).toContain('新增群組');
    expect(gHtml).toContain('未分類');
  });

  it('專有名詞訂正在加強按鈕下方、錄音上方（兩種模式都一樣；沒錄音時緊接加強按鈕）', async () => {
    const pos = (h, s) => h.indexOf(s);
    await go('#/m/test-1');
    let h = document.getElementById('view').innerHTML;
    const order = [pos(h, 'id="enhGrid"'), pos(h, 'id="termsCard"'), pos(h, 'id="audioCard"'), pos(h, 'id="detailBody"'), pos(h, 'id="chatCard"')];
    expect(order.every((n) => n >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(h).toContain('問這場會議');
    // 學習筆記模式位置不變
    document.querySelector('#modeToggle [data-mode="notes"]').click();
    await tick();
    h = document.getElementById('view').innerHTML;
    expect(pos(h, 'id="enhGrid"')).toBeLessThan(pos(h, 'id="termsCard"'));
    expect(pos(h, 'id="termsCard"')).toBeLessThan(pos(h, 'id="audioCard"'));
    document.querySelector('#modeToggle [data-mode="meeting"]').click();
    await tick();
    // 沒錄音
    nas.meetings['test-na'] = Object.assign(JSON.parse(JSON.stringify(MEETING)), { id: 'test-na', audio: [] });
    await go('#/m/test-na');
    h = document.getElementById('view').innerHTML;
    expect(pos(h, 'id="audioCard"')).toBe(-1);
    expect(pos(h, 'id="enhGrid"')).toBeLessThan(pos(h, 'id="termsCard"'));
    expect(pos(h, 'id="termsCard"')).toBeLessThan(pos(h, 'id="detailBody"'));
  });

  it('詳情頁有錄音播放器（每支一個、preload=none）與重新轉錄；點時間標記播放對的那一支', async () => {
    await go('#/m/test-1');
    const audios = document.querySelectorAll('#audioCard audio');
    expect(audios.length).toBe(2);
    expect(audios[0].getAttribute('preload')).toBe('none');
    expect(audios[1].getAttribute('src')).toBe('/api/meetings/test-1/audio/1');
    expect(document.getElementById('retrBtn')).toBeTruthy();
    // 第 3 段 t=700 → 第 2 支的 100 秒
    const mark = document.querySelector('[data-seek="2"]');
    expect(mark).toBeTruthy();
    mark.click();
    const parts = document.querySelectorAll('#audioCard .audio-part');
    expect(parts[1].classList.contains('floating')).toBe(true);
    expect(parts[0].classList.contains('floating')).toBe(false);
  });

  it('加強按鈕：送 runAI → 等工作 → 重抓會議並顯示伺服器寫回的新版（可換版）', async () => {
    await go('#/m/test-1');
    document.querySelector('[data-enh="mainPoints"]').click();
    await tick(120);
    expect(nas.calls).toContainEqual(['runAI', 'test-1', 'enhance', { section: 'mainPoints' }]);
    const html = document.getElementById('view').innerHTML;
    expect(html).toContain('加強後的重點一');
    expect(html).toContain('data-swap="mp"');
    // 換版是前端改完整份存回 NAS
    document.querySelector('[data-swap="mp"]').click();
    await tick(120);
    expect(nas.calls.some((c) => c[0] === 'saveMeeting')).toBe(true);
    expect(nas.meetings['test-1'].summary.mainPoints).toEqual(['討論上線時程']);
  });

  it('處理中的會議：清單有狀態徽章，進度頁顯示階段與備援轉錄器', async () => {
    nas.meetings.q1 = {
      id: 'q1',
      title: '排隊的會議',
      createdAt: Date.now(),
      status: 'transcribing',
      jobId: 'jt',
      progress: { stage: 'transcribing', pct: 40, msg: '第 2 支', engine: 'nas-cpu' },
      audio: [{ name: 'x.m4a' }],
    };
    await go('#/');
    await tick();
    const card = document.querySelector('.card[data-id="q1"]');
    expect(card.innerHTML).toContain('轉錄中 40%');
    card.click();
    window.dispatchEvent(new Event('hashchange'));
    await tick(120);
    expect(location.hash).toBe('#/job/q1');
    const html = document.getElementById('view').innerHTML;
    expect(html).toContain('轉錄中');
    expect(html).toContain('NAS 備援轉錄（較慢、不分說話者）');
    expect(html).toContain('■ 停止');
    expect(html).toContain('取消並刪除');
    nas.meetings.q1.status = 'queued';
    nas.meetings.q1.progress = { stage: 'queued' };
    await go('#/');
    await tick();
    expect(document.querySelector('.card[data-id="q1"]').innerHTML).toContain('排隊中（等電腦轉錄器）');
  });

  it('轉錄完成但摘要失敗（done + error）：詳情頁顯示錯誤與「重新產生摘要」', async () => {
    nas.meetings['test-1'].error = '額度用完';
    await go('#/m/test-1');
    const html = document.getElementById('view').innerHTML;
    expect(html).toContain('摘要失敗：額度用完');
    expect(document.getElementById('reSummary')).toBeTruthy();
    // 沒有錯誤的完成會議不掛橫幅
    delete nas.meetings['test-1'].error;
    await go('#/');
    await go('#/m/test-1');
    expect(document.getElementById('reSummary')).toBeNull();
  });

  it('摘要中按停止／取消並刪除：取消的是 AI 工作（aiJobId），不是已完成的轉錄工作', async () => {
    const mk = () => ({
      id: 's1',
      title: '摘要中的會議',
      createdAt: Date.now(),
      status: 'summarizing',
      jobId: 'jt-done',
      aiJobId: 'ja-run',
      progress: { stage: 'summarizing', pct: 0 },
      audio: [{ name: 'x.m4a' }],
      transcript: [{ speaker: 'A', text: '嗨', t: 0 }],
    });
    nas.meetings.s1 = mk();
    api.cancelJob.mockClear();
    await go('#/job/s1');
    await tick(60);
    document.getElementById('pgStop').click();
    await tick(60);
    expect(api.cancelJob).toHaveBeenCalledWith('ja-run');
    expect(api.cancelJob).not.toHaveBeenCalledWith('jt-done');
    api.cancelJob.mockClear();
    nas.meetings.s1 = mk();
    await go('#/');
    await go('#/job/s1');
    await tick(60);
    document.getElementById('pgDel').click();
    await tick(60);
    expect(api.cancelJob).toHaveBeenCalledWith('ja-run');
    expect(api.cancelJob).not.toHaveBeenCalledWith('jt-done');
  });

  it('進度頁顯示目前細階段（每階段進度條會重來）', async () => {
    nas.meetings.g1 = {
      id: 'g1',
      title: 'GPU 會議',
      createdAt: Date.now(),
      status: 'transcribing',
      jobId: 'jg',
      progress: { stage: 'diar', pct: 0, engine: 'gpu' },
      audio: [{ name: 'x.m4a' }],
    };
    await go('#/job/g1');
    await tick(60);
    expect(document.querySelector('.prog-label').textContent).toContain('轉錄中・分辨說話者（第 3/4 步）');
    await go('#/');
  });

  it('換頁不會讓輪詢越疊越多：清單每 5 秒只問一次 NAS', async () => {
    nas.meetings.p1 = { id: 'p1', title: '處理中', createdAt: Date.now(), status: 'transcribing', progress: { pct: 10 } };
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const flush = async () => {
      for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r));
    };
    const nav = async (h) => {
      location.hash = h;
      window.dispatchEvent(new Event('hashchange'));
      await flush();
    };
    try {
      await nav('#/');
      window.dispatchEvent(new Event('hashchange')); // 同一頁連畫兩次（像啟動時）
      await flush();
      await nav('#/m/test-1');
      await nav('#/');
      await vi.advanceTimersByTimeAsync(1); // 讓 jsdom 自己排的 hashchange 也跑完
      await flush();
      api.listMeetings.mockClear();
      await vi.advanceTimersByTimeAsync(5000);
      await flush();
      expect(api.listMeetings).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(5000);
      await flush();
      expect(api.listMeetings).toHaveBeenCalledTimes(2);
      // 離開清單後就不再問
      await nav('#/settings');
      api.listMeetings.mockClear();
      await vi.advanceTimersByTimeAsync(15000);
      await flush();
      expect(api.listMeetings).toHaveBeenCalledTimes(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('編輯模式刪除：NAS 上的內容已變（那一條不在了）→ 不刪別條，提示再操作一次', async () => {
    nas.meetings['test-1'].summary.mainPoints = ['重點甲', '重點乙'];
    await go('#/m/test-1');
    document.querySelector('[data-edit="mp"]').click();
    await tick();
    // 別台裝置剛把「重點甲」改掉了
    nas.meetings['test-1'].summary.mainPoints = ['重點丙', '重點乙'];
    document.querySelector('[data-del="mp:0"]').click();
    await tick(120);
    expect(nas.meetings['test-1'].summary.mainPoints).toEqual(['重點丙', '重點乙']);
    expect(document.getElementById('toast').textContent).toBe('內容已更新，請再操作一次');
    expect(document.getElementById('view').innerHTML).toContain('重點丙');
    // 順序變了但那一條還在 → 刪對的那條
    nas.meetings['test-1'].summary.mainPoints = ['新插入', '重點丙', '重點乙'];
    document.querySelector('[data-del="mp:0"]').click(); // 畫面上第 0 條是「重點丙」
    await tick(120);
    expect(nas.meetings['test-1'].summary.mainPoints).toEqual(['新插入', '重點乙']);
  });

  it('設定頁：NAS 狀態、Claude 型號、備援、TG 通知、備份；沒有金鑰與 GitHub 設定', async () => {
    await go('#/settings');
    await tick();
    const html = document.getElementById('view').innerHTML;
    expect(html).toContain('● 線上');
    expect(html).toContain('2 件');
    expect(html).toContain('id="claudeModel"');
    expect(html).toContain('opus');
    expect(html).toContain('haiku');
    expect(html).toContain('id="fallbackEnabled"');
    expect(html).toContain('id="fallbackMinutes"');
    expect(html).toContain('id="tgNotify"');
    expect(html).toContain('/api/export');
    expect(html).toContain('匯入備份檔');
    expect(html).toContain('檢查並載入最新版');
    expect(html).not.toContain('Gemini');
    expect(html).not.toContain('GitHub');
    expect(html).not.toContain('Groq');
    expect(document.getElementById('fallbackMinutes').value).toBe('20');
  });

  // ===== 董事長版：Item 條目、講座模式、待確認清單、匯出把關、自動校正、詞庫 =====
  const it8 = (id, text, t, extra = {}) => ({ id, text, src: [{ t, quote: '原句片段' }], ...extra });
  const lectureMeeting = () => ({
    ...JSON.parse(JSON.stringify(MEETING)),
    id: 'lec-1',
    updatedAt: Date.now() + Math.random(), // 每個測試都是「新版」，別被上一個測試留下的快取擋住
    mode: 'lecture',
    summary: {
      conclusions: [it8('c0000001', '結論：先規劃再贈與', 5)],
      advice: [it8('a0000001', '不要自行把美元換算成台幣', 300)],
      risks: [it8('r0000001', '小心 CRS 申報', 700)],
      cases: [],
      mainPoints: [it8('m0000001', '稅務重點甲', 5, { group: '稅務' }), it8('m0000002', '信託重點乙', 300, { group: '信託' })],
      qa: [it8('q0000001', '問：可以分年贈與嗎？\n答：可以', 700)],
    },
    review: {
      items: [
        { id: 'rv000001', kind: 'number', section: 'summary.risks', itemId: 'r0000001', text: '小心 CRS 申報', detail: '數字在逐字稿找不到', suggestion: '小心 CRS 申報（講者原話）', t: 700, state: 'pending' },
        { id: 'rv000002', kind: 'quote', section: 'summary.advice', itemId: 'a0000001', text: '不要自行把美元換算成台幣', detail: '找不到原句', t: 300, state: 'pending' },
        { id: 'rv000003', kind: 'name', section: 'summary.conclusions', itemId: 'c0000001', text: '結論：先規劃再贈與', detail: 'x', state: 'confirmed' },
      ],
      at: 1,
    },
  });

  it('物件條目：顯示文字與出處時間鈕（不會出現 [object Object]），點時間鈕跳到逐字稿並播放', async () => {
    nas.meetings['test-1'].summary = {
      actionItems: [it8('aaaaaaaa', '說話者3 明天開始測試', 700)],
      mainPoints: ['舊字串重點', it8('bbbbbbbb', '新物件重點', 300)],
      qa: [it8('cccccccc', '問：何時上線？\n答：下週三', 300)],
    };
    await go('#/m/test-1');
    const view = document.getElementById('view');
    const html = view.innerHTML;
    expect(html).not.toContain('[object Object]');
    expect(html).toContain('舊字串重點');
    expect(html).toContain('新物件重點');
    expect(html).toContain('何時上線？');
    expect(html).toContain('待辦事項 Action Item'); // 會議模式標題不變
    const chip = view.querySelector('.src-chip[data-src-t="700"]');
    expect(chip.textContent).toContain('11:40');
    chip.click();
    // 700 秒 → 第 3 段逐字稿；錄音第 2 支浮起來
    expect(document.querySelector('.seg[data-seg="2"]').classList.contains('flash')).toBe(true);
    expect(document.querySelectorAll('#audioCard .audio-part')[1].classList.contains('floating')).toBe(true);
    // 清單卡片摘要也只用文字
    await go('#/');
    expect(document.getElementById('view').innerHTML).not.toContain('[object Object]');
  });

  it('講座模式：核心結論／講者建議／風險提醒／案例／重點（依主題分組）／Q&A，沒有待辦', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    const html = document.getElementById('view').innerHTML;
    for (const t of ['核心結論', '講者建議', '風險提醒', '案例', '📌 重點', 'Q&amp;A', '稅務', '信託', '先規劃再贈與'])
      expect(html).toContain(t);
    expect(html).not.toContain('待辦事項');
    expect(html).not.toContain('加強待辦');
    expect(html).toContain('data-kind="lecture"');
    expect(document.querySelector('[data-kind="lecture"]').classList.contains('on')).toBe(true);
    expect(document.querySelector('.item-group').textContent).toBe('稅務');
    // 分組後編號連續
    expect(html).toContain('start="2"');
  });

  it('講座／會議切換：POST mode → 等工作 → 重繪', async () => {
    await go('#/m/test-1');
    document.querySelector('[data-kind="lecture"]').click();
    await tick(120);
    expect(nas.calls).toContainEqual(['setMeetingMode', 'test-1', 'lecture']);
    expect(api.waitJob).toHaveBeenCalledWith('job-m', expect.any(Function));
    expect(document.querySelector('[data-kind="lecture"]').classList.contains('on')).toBe(true);
  });

  it('重新整理（不重新轉錄）：送 ai reprocess', async () => {
    await go('#/m/test-1');
    expect(document.getElementById('reprocessBtn').textContent).toContain('重新整理（不重新轉錄）');
    document.getElementById('reprocessBtn').click();
    await tick(120);
    expect(nas.calls).toContainEqual(['runAI', 'test-1', 'reprocess', {}]);
  });

  it('待確認清單：在模式切換正下方、加強按鈕上方；列出 pending 項目與數量', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    const h = document.getElementById('view').innerHTML;
    const pos = (s) => h.indexOf(s);
    expect(pos('id="modeToggle"')).toBeLessThan(pos('id="reviewCard"'));
    expect(pos('id="reviewCard"')).toBeLessThan(pos('id="enhGrid"'));
    expect(pos('id="enhGrid"')).toBeLessThan(pos('id="termsCard"'));
    const box = document.getElementById('reviewCard');
    expect(box.hidden).toBe(false);
    expect(document.getElementById('reviewCount').textContent).toBe('2');
    expect(box.querySelectorAll('.review-row').length).toBe(2); // confirmed 的不列
    expect(box.innerHTML).toContain('數字無依據');
    expect(box.innerHTML).toContain('找不到原句');
    expect(box.innerHTML).toContain('建議：小心 CRS 申報（講者原話）');
    expect(box.querySelector('[data-rt="700"]').textContent).toContain('11:40');
  });

  it('待確認清單：確認／改正／刪除都打 review API，清空後顯示「全部確認完成」並解除匯出鎖', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    // 確認
    document.querySelector('.review-row[data-rid="rv000001"] [data-rv="confirm"]').click();
    await tick(120);
    expect(nas.calls).toContainEqual(['reviewAction', 'lec-1', 'rv000001', 'confirm', undefined]);
    expect(document.getElementById('reviewCount').textContent).toBe('1');
    // 改正（行內編輯）
    document.querySelector('.review-row[data-rid="rv000002"] [data-rv="edit"]').click();
    const ta = document.querySelector('.review-row[data-rid="rv000002"] textarea');
    expect(ta.value).toBe('不要自行把美元換算成台幣');
    ta.value = '不要自行換算匯率';
    document.querySelector('.review-row[data-rid="rv000002"] [data-re="save"]').click();
    await tick(120);
    expect(nas.calls).toContainEqual(['reviewAction', 'lec-1', 'rv000002', 'edit', '不要自行換算匯率']);
    const html = document.getElementById('view').innerHTML;
    expect(html).toContain('不要自行換算匯率');
    expect(document.getElementById('reviewCard').textContent).toContain('全部確認完成');
    expect(document.getElementById('exportLock').hidden).toBe(true);
    expect(document.getElementById('pdfBtn').classList.contains('locked')).toBe(false);
  });

  it('待確認清單：刪除', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    document.querySelector('.review-row[data-rid="rv000001"] [data-rv="delete"]').click();
    await tick(120);
    expect(nas.calls).toContainEqual(['reviewAction', 'lec-1', 'rv000001', 'delete', undefined]);
    expect(document.getElementById('view').innerHTML).not.toContain('小心 CRS 申報');
  });

  it('匯出把關：還有待確認時 分享／PDF／Word 不能用，提示還有幾項', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    const alerts = [];
    window.alert = (msg) => alerts.push(msg);
    const lock = document.getElementById('exportLock');
    expect(lock.hidden).toBe(false);
    expect(lock.textContent).toContain('還有 2 項待確認，處理完才能匯出');
    for (const bid of ['shareBtn', 'pdfBtn', 'wordBtn']) {
      const b = document.getElementById(bid);
      expect(b.getAttribute('aria-disabled')).toBe('true');
      expect(b.title).toBe('還有 2 項待確認，處理完才能匯出');
      b.click();
      await tick(20);
    }
    expect(alerts).toEqual(Array(3).fill('還有 2 項待確認，處理完才能匯出'));
    expect(document.querySelector('.sheet-ov')).toBeNull(); // 沒有開出匯出選單
    // 舊會議（沒有 review）照常可匯出
    await go('#/m/test-1');
    expect(document.getElementById('exportLock').hidden).toBe(true);
    expect(document.getElementById('reviewCard').hidden).toBe(true);
    document.getElementById('pdfBtn').click();
    await tick(20);
    const sheet = document.querySelector('.sheet-ov');
    expect(sheet).toBeTruthy();
    expect(sheet.innerHTML).toContain('待辦事項');
    document.getElementById('expCancel').click();
  });

  it('講座的匯出選單列出講座區塊', async () => {
    const m = lectureMeeting();
    m.review.items.forEach((r) => (r.state = 'confirmed'));
    nas.meetings['lec-1'] = m;
    await go('#/m/lec-1');
    document.getElementById('wordBtn').click();
    await tick(20);
    const sheet = document.querySelector('.sheet-ov');
    expect(sheet.innerHTML).toContain('核心結論');
    expect(sheet.innerHTML).toContain('風險提醒');
    expect(sheet.innerHTML).not.toContain('待辦事項');
    document.getElementById('expCancel').click();
  });

  it('專有名詞訂正卡片：列出自動校正（錯 → 對、次數），可還原原始逐字稿', async () => {
    Object.assign(nas.meetings['test-1'], {
      transcriptRaw: [{ speaker: '說話者1', text: '討論上線時程', t: 5 }],
      corrections: {
        items: [
          { wrong: '附委託', right: '複委託', confidence: 'high', count: 3, ts: [5], state: 'applied' },
          { wrong: '股力', right: '股利', confidence: 'medium', count: 1, ts: [300], state: 'pending' },
        ],
        at: 1,
      },
    });
    await go('#/m/test-1');
    const card = document.getElementById('termsCard');
    expect(card.innerHTML).toContain('已自動校正 1 個');
    expect(card.innerHTML).toContain('附委託');
    expect(card.innerHTML).toContain('複委託');
    expect(card.innerHTML).toContain('3 處');
    expect(card.innerHTML).toContain('另有 1 個沒把握的校正');
    // 專有名詞訂正卡片仍在加強按鈕下方、錄音上方
    const h = document.getElementById('view').innerHTML;
    expect(h.indexOf('id="enhGrid"')).toBeLessThan(h.indexOf('id="termsCard"'));
    expect(h.indexOf('id="termsCard"')).toBeLessThan(h.indexOf('id="audioCard"'));
    document.getElementById('restoreRaw').click();
    await tick(120);
    expect(nas.calls).toContainEqual(['restoreRaw', 'test-1']);
    expect(api.waitJob).toHaveBeenCalledWith('job-r', expect.any(Function));
  });

  it('設定頁：轉錄詞庫讀出並存回 settings.glossary；型號預設 Opus', async () => {
    const orig = api.getSettings.getMockImplementation();
    // 換頁時 router 可能跑兩次（jsdom 的 hashchange ＋ 手動觸發）→ 用持續的假值，最後還原
    api.getSettings.mockImplementation(async () => ({ claudeModel: 'opus', fallbackEnabled: false, fallbackMinutes: 20, tgNotify: false, glossary: '股利\n複委託' }));
    api.saveSettings.mockClear();
    await go('#/settings');
    await tick();
    api.getSettings.mockImplementation(orig);
    const ta = document.getElementById('glossary');
    expect(ta.value).toBe('股利\n複委託');
    expect(ta.disabled).toBe(false);
    expect(document.getElementById('claudeModel').value).toBe('opus');
    expect(document.querySelector('#claudeModel option').textContent).toContain('Opus（預設');
    ta.value = '股利\n  BVI  \n\n複委託';
    ta.dispatchEvent(new Event('change'));
    await tick();
    expect(api.saveSettings).toHaveBeenCalledTimes(1);
    expect(api.saveSettings.mock.calls[0][0].glossary).toBe('股利\nBVI\n複委託');
  });

  it('設定頁：伺服器沒給詞庫時，改別的設定不會送出空詞庫', async () => {
    api.saveSettings.mockClear();
    await go('#/settings');
    await tick();
    document.getElementById('tgNotify').click();
    await tick();
    expect(api.saveSettings).toHaveBeenCalledTimes(1);
    expect('glossary' in api.saveSettings.mock.calls[0][0]).toBe(false);
  });

  // ---- 修正第 1 輪 ----
  const twoFile = () => ({
    ...JSON.parse(JSON.stringify(MEETING)),
    id: 'two-1',
    updatedAt: Date.now() + Math.random(),
    transcript: [
      { speaker: 'A', text: '第一支開場白大家好', t: 10, fi: 0 },
      { speaker: 'B', text: '第一支講稅務', t: 100, fi: 0 },
      { speaker: 'A', text: '第二支講信託安排的細節', t: 12, fi: 1 },
      { speaker: 'B', text: '第二支結尾', t: 100, fi: 1 },
    ],
    summary: {
      actionItems: [],
      mainPoints: [{ id: 'mp000001', text: '信託要先安排', src: [{ t: 15, quote: '信託安排' }] }],
      qa: [],
    },
    review: { items: [{ id: 'rv0000t1', kind: 'quote', section: 'summary.mainPoints', itemId: 'mp000001', text: '信託要先安排', detail: 'x', t: 15, state: 'pending' }] },
  });

  it('多支錄音：時間鈕依原句找到第二支的段落，跳逐字稿並播放第二支', async () => {
    nas.meetings['two-1'] = twoFile();
    await go('#/m/two-1');
    document.querySelector('.src-chip[data-src-t="15"]').click();
    expect(document.querySelector('.seg[data-seg="2"]').classList.contains('flash')).toBe(true);
    expect(document.querySelector('.seg[data-seg="0"]').classList.contains('flash')).toBe(false);
    const parts = document.querySelectorAll('#audioCard .audio-part');
    expect(parts[1].classList.contains('floating')).toBe(true);
    expect(parts[0].classList.contains('floating')).toBe(false);
    expect(document.querySelector('.seg[data-seg="2"] mark.hit')).toBeTruthy();
  });

  it('多支錄音：待確認清單的時間鈕用條目出處原句定位到第二支', async () => {
    nas.meetings['two-1'] = twoFile();
    await go('#/m/two-1');
    document.querySelector('.review-row [data-rt="15"]').click();
    expect(document.querySelector('.seg[data-seg="2"]').classList.contains('flash')).toBe(true);
    expect(document.querySelectorAll('#audioCard .audio-part')[1].classList.contains('floating')).toBe(true);
  });

  it('手動刪掉有待確認項目的條目 → 該項不再算待確認，匯出鎖跟著更新', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    expect(document.getElementById('reviewCount').textContent).toBe('2');
    document.querySelector('[data-edit="lr"]').click();
    await tick();
    document.querySelector('[data-del="lr:0"]').click(); // 刪掉「小心 CRS 申報」（rv000001 指向它）
    await tick(120);
    expect(nas.meetings['lec-1'].summary.risks).toEqual([]);
    expect(document.getElementById('reviewCount').textContent).toBe('1');
    expect(document.getElementById('exportLock').textContent).toContain('還有 1 項待確認');
    expect(document.querySelector('.review-row[data-rid="rv000001"]')).toBeNull();
  });

  it('待確認動作回 404／409 → 重抓會議重畫，不跳錯誤', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    const alerts = [];
    window.alert = (msg) => alerts.push(msg);
    // 別台裝置已經確認過這一項
    nas.meetings['lec-1'].review.items[0].state = 'confirmed';
    nas.meetings['lec-1'].updatedAt += 1;
    api.reviewAction.mockImplementationOnce(async () => {
      throw Object.assign(new Error('已處理'), { status: 409 });
    });
    document.querySelector('.review-row[data-rid="rv000001"] [data-rv="confirm"]').click();
    await tick(120);
    expect(alerts).toEqual([]);
    expect(document.getElementById('reviewCount').textContent).toBe('1');
    expect(document.getElementById('toast').textContent).toContain('整理中，請稍後再確認');
    expect(document.getElementById('toast').textContent).not.toContain('已經處理過');
  });

  it('待確認動作回 404 → 重抓會議重畫，提示已處理過', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    api.reviewAction.mockImplementationOnce(async () => {
      throw Object.assign(new Error('找不到'), { status: 404 });
    });
    document.querySelector('.review-row[data-rid="rv000001"] [data-rv="confirm"]').click();
    await tick(120);
    expect(document.getElementById('toast').textContent).toContain('已重新整理清單');
  });

  it('換版成沒有出處的舊版 → 伺服器重新驗證回傳待確認，畫面照伺服器的清單上鎖（不在前端隱藏）', async () => {
    const m = lectureMeeting();
    m.review.items.forEach((r) => (r.state = 'confirmed'));
    m.alt = { 'summary:mainPoints': ['AI 自己補的一句', '另一句沒有出處'] };
    nas.meetings['lec-1'] = m;
    await go('#/m/lec-1');
    const alerts = [];
    window.alert = (msg) => alerts.push(msg);
    expect(document.getElementById('exportLock').hidden).toBe(true);
    document.querySelector('[data-swap="mp"]').click();
    await tick(150);
    expect(nas.meetings['lec-1'].summary.mainPoints).toEqual(['AI 自己補的一句', '另一句沒有出處']);
    expect(document.getElementById('reviewCount').textContent).toBe('2');
    expect(document.getElementById('exportLock').textContent).toContain('還有 2 項待確認');
    document.getElementById('wordBtn').click();
    await tick(20);
    expect(alerts).toEqual(['還有 2 項待確認，處理完才能匯出']);
    expect(document.querySelector('.sheet-ov')).toBeNull();
  });

  it('伺服器回報的待確認項目即使指向的條目不在畫面上，也照算（不再前端隱藏）', async () => {
    const m = lectureMeeting();
    m.review.items[0].itemId = 'ffffffff'; // 條目已不存在，但伺服器說還 pending
    nas.meetings['lec-1'] = m;
    await go('#/m/lec-1');
    expect(document.getElementById('reviewCount').textContent).toBe('2');
    expect(document.getElementById('exportLock').textContent).toContain('還有 2 項待確認');
  });

  it('講者口誤（沒有對應條目）只有「知道了」，送出 confirm', async () => {
    const m = lectureMeeting();
    m.review.items.push({ id: 'rvslip01', kind: 'slip', section: 'transcript', text: '只要繳五千塊', detail: '口誤', t: 700, state: 'pending' });
    nas.meetings['lec-1'] = m;
    await go('#/m/lec-1');
    const row = document.querySelector('.review-row[data-rid="rvslip01"]');
    const ops = [...row.querySelectorAll('[data-rv]')];
    expect(ops.map((b) => b.dataset.rv)).toEqual(['confirm']);
    expect(ops[0].textContent).toContain('知道了');
    // 指向條目的項目仍有三個按鈕
    expect(document.querySelectorAll('.review-row[data-rid="rv000001"] [data-rv]').length).toBe(3);
    api.reviewAction.mockImplementationOnce(async (id, rid, action) => {
      nas.calls.push(['reviewAction', id, rid, action]);
      const mm = nas.meetings[id];
      mm.review.items.find((x) => x.id === rid).state = 'confirmed';
      return JSON.parse(JSON.stringify(mm));
    });
    ops[0].click();
    await tick(120);
    expect(nas.calls).toContainEqual(['reviewAction', 'lec-1', 'rvslip01', 'confirm']);
    expect(document.querySelector('.review-row[data-rid="rvslip01"]')).toBeNull();
  });

  it('meeting.warnings：詳情頁在匯出列下方每則顯示一行 ⚠️', async () => {
    nas.meetings['test-1'].warnings = ['名詞校正失敗，已略過：Claude 執行失敗：boom', '檢查口誤失敗，已略過：x'];
    await go('#/m/test-1');
    const w = [...document.querySelectorAll('.export-warn')].map((x) => x.textContent);
    expect(w).toEqual(['⚠️ 名詞校正失敗，已略過：Claude 執行失敗：boom', '⚠️ 檢查口誤失敗，已略過：x']);
    const h = document.getElementById('view').innerHTML;
    expect(h.indexOf('id="exportLock"')).toBeLessThan(h.indexOf('export-warn'));
    expect(h.indexOf('id="wordBtn"')).toBeLessThan(h.indexOf('export-warn'));
  });

  it('各區「複製」也受匯出把關', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    const alerts = [];
    window.alert = (msg) => alerts.push(msg);
    const writes = [];
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (t) => writes.push(t) }, configurable: true });
    document.querySelector('[data-copy="mp"]').click();
    await tick(20);
    expect(alerts).toEqual(['還有 2 項待確認，處理完才能匯出']);
    expect(writes).toEqual([]);
    // 清空後可以複製
    const m = lectureMeeting();
    m.review.items.forEach((r) => (r.state = 'confirmed'));
    nas.meetings['lec-1'] = m;
    await go('#/');
    await go('#/m/lec-1');
    document.querySelector('[data-copy="mp"]').click();
    await tick(20);
    expect(writes[0]).toContain('【稅務】');
    expect(writes[0]).toContain('1. 稅務重點甲');
  });

  it('AI 作業進行中：待確認清單重畫後按鈕仍停用', async () => {
    nas.meetings['lec-1'] = lectureMeeting();
    await go('#/m/lec-1');
    let release;
    api.waitJob.mockImplementationOnce(() => new Promise((r) => (release = () => r({ state: 'done' }))));
    document.getElementById('reprocessBtn').click();
    await tick();
    document.getElementById('reviewHead').click(); // 收起
    document.getElementById('reviewHead').click(); // 展開 → 重畫
    const btns = document.querySelectorAll('#reviewCard [data-rv]');
    expect(btns.length).toBe(6);
    expect([...btns].every((b) => b.disabled)).toBe(true);
    release();
    await tick(120);
  });

  it('bannerStatus：進度訊息跟狀態重複時不重複', async () => {
    const { bannerStatus } = await import('../js/app.js');
    expect(bannerStatus({ status: 'summarizing', progress: { msg: '摘要中' } })).toBe('摘要中');
    expect(bannerStatus({ status: 'summarizing', progress: { msg: '摘要' } })).toBe('摘要中');
    expect(bannerStatus({ status: 'summarizing', progress: { msg: '名詞校正中' } })).toBe('摘要中・名詞校正中');
    expect(bannerStatus({ status: 'summarizing', progress: {} })).toBe('摘要中');
  });

  it('專有名詞套用到物件條目只改 text，保留 id／src', async () => {
    nas.meetings['test-1'].summary = { actionItems: [], mainPoints: [it8('bbbbbbbb', '台積的重點', 300)], qa: [] };
    nas.meetings['test-1'].terms = { items: [{ t: '台積', cat: 'org', fix: '', draft: '台積電' }] };
    await go('#/m/test-1');
    document.getElementById('applyTerms').click();
    await tick(120);
    const mp = nas.meetings['test-1'].summary.mainPoints[0];
    expect(mp).toEqual({ id: 'bbbbbbbb', text: '台積電的重點', src: [{ t: 300, quote: '原句片段' }] });
  });

  it('整理中：逐字稿編輯、講者改名、專有名詞套用全部停用並提示', async () => {
    nas.meetings['test-1'].terms = { items: [{ t: '台積', cat: 'org', fix: '', draft: '台積電' }] };
    nas.meetings['test-1'].status = 'summarizing';
    nas.meetings['test-1'].aiJobId = 'ja-1';
    await go('#/m/test-1');
    const view = document.getElementById('view');
    expect(view.textContent).toContain('整理中，完成後再修改');
    expect(view.querySelector('.spk-chip').disabled).toBe(true);
    expect(view.querySelector('#applyTerms').disabled).toBe(true);
    expect(view.querySelector('#addTerm').disabled).toBe(true);
    api.saveMeeting.mockClear();
    view.querySelector('.seg[data-i]').click();
    expect(view.querySelector('.seg-edit')).toBeNull();
    view.querySelector('#applyTerms').click();
    await tick(80);
    expect(api.saveMeeting).not.toHaveBeenCalled();
  });

  it('存檔回 409（整理中）→ 提示「整理中，完成後再修改」並重抓', async () => {
    await go('#/m/test-1');
    const alerts = [];
    window.alert = (msg) => alerts.push(msg);
    api.saveMeeting.mockImplementationOnce(async () => {
      throw Object.assign(new Error('整理中，完成後再修改'), { status: 409 });
    });
    api.getMeeting.mockClear();
    document.querySelector('.seg[data-i]').click();
    const ta = document.querySelector('.seg-edit');
    ta.value = '被改過的句子';
    document.querySelector('.seg-save').click();
    await tick(120);
    expect(alerts).toEqual([]);
    expect(document.getElementById('toast').textContent).toContain('整理中，完成後再修改');
    expect(api.getMeeting.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  // ---- 單字訂正卡（ReviewItem v2）----
  const wordMeeting = () => {
    const m = JSON.parse(JSON.stringify(MEETING));
    m.id = 'w-1';
    m.updatedAt = Date.now() + Math.random();
    m.summary.mainPoints = [{ id: 'mp000001', text: '王小明說要測試', src: [{ t: 700, quote: '我明天開始測試' }] }];
    m.review = {
      items: [
        {
          id: 'rw000001', span: '王曉明', action: 'replace', suggestion: '王小明', reason: '[00:11:40] 講者說「王小明明天測試」', confidence: 'high',
          targets: [{ where: 'transcript', count: 2 }, { where: 'summary.mainPoints', itemId: 'mp000001', count: 1 }, { where: 'notes.outline', count: 1 }],
          context: '由<b>【王曉明】</b>負責測試', sources: ['name'], state: 'pending',
        },
        { id: 'rw000002', span: '48%', action: 'keep', suggestion: '', reason: '原話如此', confidence: 'low', targets: [{ where: 'transcript', count: 1 }], context: '成長【48%】', sources: ['number'], state: 'pending' },
        { id: 'rw000003', span: '嗯那個', action: 'delete', suggestion: '', reason: '口誤', confidence: 'medium', targets: [{ where: 'summary.mainPoints', count: 1 }], context: '【嗯那個】測試', sources: ['slip'], state: 'pending' },
        { id: 'rw000004', span: '王曉明', action: 'replace', suggestion: '王小明', reason: 'x', confidence: 'high', targets: [], context: '【王曉明】', state: 'merged' },
        { id: 'rv000009', kind: 'quote', section: 'summary.mainPoints', itemId: 'mp000001', text: '王小明說要測試', detail: '找不到原句', t: 700, state: 'pending' },
      ],
    };
    m.gaps = [{ start: 60, end: 150 }, { start: 400, end: 490 }];
    return m;
  };
  // ---- 待確認勾選清單（v107）----
  const checklistMeeting = () => {
    const m = wordMeeting();
    m.review.items.push(
      // 同一組字（王曉明 → 王小明）、不同 span → 併成同一列
      { id: 'rw000005', span: '王曉明負責', action: 'replace', suggestion: '王小明負責', from: '王曉明', to: '王小明', reason: 'r', confidence: 'low',
        targets: [{ where: 'transcript', count: 3 }], context: '【王曉明負責】', state: 'pending' },
      // 長句 span、只改一個詞：只顯示那組字（沒有 from／to 的舊資料由前端算）
      { id: 'rw000006', span: '所以選對專案你就自打嘴巴了', action: 'replace', suggestion: '所以選對專案你就打自己嘴巴了', reason: '', confidence: 'medium',
        targets: [{ where: 'transcript', count: 1 }], context: '【所以選對專案你就自打嘴巴了】', state: 'pending' }
    );
    return m;
  };
  const rowOf = (from) => [...document.querySelectorAll('.wl-row')].find((x) => x.querySelector('.wl-from').textContent === from);

  it('勾選清單：只顯示字詞對、預設全勾、合併同一組字、keep／merged 不顯示；舊格式退回舊卡；沒有大按鈕', async () => {
    nas.meetings['w-1'] = checklistMeeting();
    await go('#/m/w-1');
    const box = document.getElementById('reviewCard');
    expect(box.querySelector('.review-head').textContent).toContain('⚠️ 待確認（4 項）'); // 3 列＋1 張舊卡
    expect(box.textContent).toContain('AI 已勾選建議，沒問題直接按確認；取消勾選 = 保留原文');
    const rows = [...box.querySelectorAll('.wl-row')];
    expect(rows.map((x) => x.querySelector('.wl-pair').textContent)).toEqual(['王曉明→王小明', '嗯那個→（刪除）', '自打→打自己']);
    expect(rows.every((x) => x.querySelector('.wl-check').checked)).toBe(true);
    expect(box.textContent).not.toContain('保留（不是錯誤）'); // keep 卡（rw000002）不出現
    expect(box.textContent).not.toContain('48%');
    const r1 = rowOf('王曉明');
    expect(r1.querySelector('.wl-to').tagName).toBe('B');
    expect(r1.querySelector('.wl-n').textContent).toBe('7 處'); // 2+1+1（rw000001）+3（rw000005）
    expect(r1.querySelector('.wl-low')).toBeTruthy(); // 合併後最低信心是 low → ⚠️，但仍勾選
    expect(r1.querySelector('.wl-check').checked).toBe(true);
    expect(rowOf('自打').querySelector('.wl-low')).toBeNull();
    // 細節預設收起；▸ 展開看上下文（跳脫）、理由時間鈕
    const d = r1.querySelector('.wl-detail');
    expect(d.hidden).toBe(true);
    r1.querySelector('.wl-more').click();
    expect(d.hidden).toBe(false);
    expect(d.querySelector('mark.word-hit').textContent).toBe('王曉明');
    expect(d.querySelector('.word-ctx b')).toBeNull();
    const chip = d.querySelector('.word-reason [data-rt="700"]');
    expect(chip.textContent).toContain('00:11:40');
    chip.click();
    expect(document.querySelector('.seg[data-seg="2"]').classList.contains('flash')).toBe(true);
    // 一顆確認鈕；舊的逐張大按鈕與「全部照建議」都不見了
    expect(document.getElementById('wlConfirm').textContent).toBe('✅ 確認（套用 3 項，保留 0 項）');
    expect(box.querySelector('[data-wv]')).toBeNull();
    expect(document.getElementById('acceptHighBtn')).toBeNull();
    const old = box.querySelector('.review-row[data-rid="rv000009"]');
    expect(old.querySelector('[data-rv="confirm"]')).toBeTruthy();
    expect(old.textContent).toContain('找不到原句');
  });

  it('勾選清單：取消勾選＝保留；確認送 accept／keep 卡 id、toast、依回傳重畫', async () => {
    nas.meetings['w-1'] = checklistMeeting();
    await go('#/m/w-1');
    const cb = rowOf('自打').querySelector('.wl-check');
    cb.checked = false;
    cb.dispatchEvent(new Event('change'));
    expect(document.getElementById('wlConfirm').textContent).toBe('✅ 確認（套用 2 項，保留 1 項）');
    let release;
    let body;
    api.applyReviewBatch.mockImplementationOnce(async (id, accept, keep) => {
      body = { id, accept, keep };
      await new Promise((r) => (release = r));
      const m = nas.meetings[id];
      m.review.items.forEach((x) => {
        if (accept.includes(x.id) || keep.includes(x.id)) x.state = 'confirmed';
      });
      m.summary.mainPoints[0].text = '王小明說要測試（已改）';
      m.updatedAt += 1;
      return { ...JSON.parse(JSON.stringify(m)), applied: { cards: 3, kept: 1, transcript: 5, items: 1 } };
    });
    document.getElementById('wlConfirm').click();
    await tick();
    expect(document.getElementById('wlConfirm').disabled).toBe(true); // 送出中停用
    expect(body).toEqual({ id: 'w-1', accept: ['rw000001', 'rw000005', 'rw000003'], keep: ['rw000006'] });
    release();
    await tick(120);
    expect(document.getElementById('toast').textContent).toBe('已套用 2 項：逐字稿 5 處、摘要 1 條');
    expect(document.querySelector('.wl-row')).toBeNull();
    expect(document.getElementById('reviewCount').textContent).toBe('1'); // 只剩舊卡
    expect(document.getElementById('view').innerHTML).toContain('（已改）');
  });

  it('勾選清單：自己改把這一列每張卡都送 edit（span 裡的錯字換成輸入）', async () => {
    nas.meetings['w-1'] = checklistMeeting();
    await go('#/m/w-1');
    const calls = [];
    const editReply = async (id, rid, action, text) => {
      calls.push([rid, action, text]);
      const m = nas.meetings[id];
      m.review.items.find((x) => x.id === rid).state = 'edited';
      return { ...JSON.parse(JSON.stringify(m)), applied: { transcript: 1, items: 1 } };
    };
    api.reviewAction.mockImplementationOnce(editReply).mockImplementationOnce(editReply);
    const r1 = rowOf('王曉明');
    r1.querySelector('.wl-more').click();
    const inp = r1.querySelector('.word-input');
    expect(inp.value).toBe('王小明');
    inp.value = '王筱明';
    r1.querySelector('.wl-edit-btn').click();
    await tick(120);
    expect(calls).toEqual([['rw000001', 'edit', '王筱明'], ['rw000005', 'edit', '王筱明負責']]);
    expect(document.getElementById('toast').textContent).toBe('已改：逐字稿 2 處、摘要 2 條');
    expect(rowOf('王曉明')).toBeUndefined();
  });

  it('勾選清單：409 時重抓並提示整理中', async () => {
    nas.meetings['w-1'] = checklistMeeting();
    await go('#/m/w-1');
    api.applyReviewBatch.mockImplementationOnce(async () => {
      throw Object.assign(new Error('整理中'), { status: 409 });
    });
    document.getElementById('wlConfirm').click();
    await tick(120);
    expect(document.getElementById('toast').textContent).toBe('整理中，請稍後再確認');
  });

  it('單字訂正卡：只剩 stale／merged 時整個清單隱藏', async () => {
    const m = wordMeeting();
    m.review.items = [
      { id: 'rw000012', kind: 'word', span: '不見了', action: 'replace', suggestion: 'x', targets: [], context: '【不見了】', state: 'stale' },
      { id: 'rv000013', kind: 'name', section: 'summary.mainPoints', itemId: 'mp000001', text: 't', state: 'merged', mergedInto: 'rw000012' },
    ];
    nas.meetings['w-1'] = m;
    await go('#/m/w-1');
    expect(document.getElementById('reviewCard').hidden).toBe(true);
  });

  it('未辨識段落：匯出列下方顯示約 X 分鐘，展開列出時間鈕；沒有 gaps 不顯示', async () => {
    nas.meetings['w-1'] = wordMeeting();
    await go('#/m/w-1');
    const g = document.getElementById('gapsLine');
    expect(g.querySelector('summary').textContent).toBe('⚠️ 約 3 分鐘未辨識');
    const chips = g.querySelectorAll('[data-gap-t]');
    expect(chips.length).toBe(2);
    expect(chips[0].textContent).toContain('1:00–2:30');
    const h = document.getElementById('view').innerHTML;
    expect(h.indexOf('id="exportLock"')).toBeLessThan(h.indexOf('id="gapsLine"'));
    chips[1].click();
    expect(document.querySelector('.seg[data-seg="1"]').classList.contains('flash')).toBe(true);
    await go('#/m/test-1');
    expect(document.getElementById('gapsLine')).toBeNull();
  });
});

describe('播放與狀態的純函式', () => {
  it('seekTarget：絕對秒數依各支長度換算；舊資料 fi 直接指定第幾支', async () => {
    const appMod = await import('../js/app.js');
    const audio = [{ durationSec: 600 }, { durationSec: 600 }];
    expect(appMod.seekTarget({ t: 30 }, audio)).toEqual({ part: 0, time: 30 });
    expect(appMod.seekTarget({ t: 700 }, audio)).toEqual({ part: 1, time: 100 });
    expect(appMod.seekTarget({ t: 5000 }, audio)).toEqual({ part: 1, time: 4400 });
    expect(appMod.seekTarget({ t: 12, fi: 1 }, audio)).toEqual({ part: 1, time: 12 });
    expect(appMod.seekTarget({ t: '01:05' }, [{}])).toEqual({ part: 0, time: 65 });
    expect(appMod.seekTarget({ t: 5 }, [])).toBeNull();
  });

  it('progressLabel／activeJobId：細階段文字與要取消的工作', async () => {
    const { progressLabel, activeJobId } = await import('../js/app.js');
    expect(progressLabel({ status: 'transcribing', progress: { stage: 'download' } })).toBe('轉錄中・下載錄音（第 1/4 步）');
    expect(progressLabel({ status: 'transcribing', progress: { stage: 'asr' } })).toBe('轉錄中・語音轉文字（第 2/4 步）');
    expect(progressLabel({ status: 'transcribing', progress: { stage: 'upload' } })).toBe('轉錄中・上傳結果（第 4/4 步）');
    expect(progressLabel({ status: 'transcribing', progress: { stage: 'transcribe' } })).toBe('轉錄中・語音轉文字');
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'claude' } })).toBe('摘要中');
    expect(progressLabel({ status: 'queued', progress: { stage: 'queued' } })).toBe('排隊中（等電腦轉錄器）');
    expect(progressLabel({ status: 'transcribing', progress: {} })).toBe('轉錄中');
    // 重新整理的各步驟：stage 或伺服器訊息文字都認得
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'correction' } })).toBe('摘要中・名詞校正中（第 1/5 步）');
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'verify' } })).toBe('摘要中・驗證中（第 5/5 步）');
    expect(progressLabel({ status: 'summarizing', progress: { msg: '判斷講座／會議' } })).toBe('摘要中・判斷講座／會議（第 2/5 步）');
    // 「整理摘要」本身分不出是 reprocess 還是一般摘要 → 沒看過 reprocess 專屬步驟、沒標動作時維持舊標籤
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'claude', msg: '整理摘要中' } })).toBe('摘要中');
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'summary' } })).toBe('摘要中');
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'notes', msg: '整理筆記中' } })).toBe('摘要中');
    // 看過專屬步驟（seen）或伺服器標了 action=reprocess → 編號
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'claude', msg: '整理摘要中' } }, true)).toBe('摘要中・整理摘要中（第 3/5 步）');
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'notes', action: 'reprocess' } })).toBe('摘要中・整理筆記中（第 4/5 步）');
    // 伺服器標了別的動作 → 就算訊息像 reprocess 也不編號
    expect(progressLabel({ status: 'summarizing', progress: { stage: 'claude', action: 'summary', msg: '驗證中' } })).toBe('摘要中');
    expect(activeJobId({ status: 'summarizing', jobId: 't', aiJobId: 'a' })).toBe('a');
    expect(activeJobId({ status: 'transcribing', jobId: 't', aiJobId: 'a' })).toBe('t');
    expect(activeJobId({ status: 'queued' })).toBeNull();
  });

  it('statusText：失敗時附上伺服器錯誤', async () => {
    const appMod = await import('../js/app.js');
    expect(appMod.statusText({ status: 'queued' })).toBe('排隊中（等電腦轉錄器）');
    expect(appMod.statusText({ status: 'summarizing' })).toBe('摘要中');
    expect(appMod.statusText({ status: 'done' })).toBe('完成');
    expect(appMod.statusText({ status: 'failed', error: '音檔損毀' })).toBe('失敗：音檔損毀');
  });

  it('agoText：秒、毫秒、ISO 字串都吃', async () => {
    const appMod = await import('../js/app.js');
    const now = 1_800_000_000_000;
    expect(appMod.agoText(now - 30_000, now)).toBe('30 秒前');
    expect(appMod.agoText((now - 120_000) / 1000, now)).toBe('2 分鐘前');
    expect(appMod.agoText(new Date(now - 3 * 3600_000).toISOString(), now)).toBe('3 小時前');
    expect(appMod.agoText(null, now)).toBe('從未連線');
  });

  it('removeSectionItemMatching：依內容找、同內容取最靠近原位置、找不到不動', async () => {
    const appMod = await import('../js/app.js');
    const m = { summary: { qa: ['A', 'B', 'A', 'C'] } };
    expect(appMod.removeSectionItemMatching(m, 'summary', 'qa', 1, 'B')).toBe(true);
    expect(m.summary.qa).toEqual(['A', 'A', 'C']);
    expect(appMod.removeSectionItemMatching(m, 'summary', 'qa', 3, 'A')).toBe(true); // 取最靠近 3 的 A（index 1）
    expect(m.summary.qa).toEqual(['A', 'C']);
    expect(appMod.removeSectionItemMatching(m, 'summary', 'qa', 0, 'Z')).toBe(false);
    expect(m.summary.qa).toEqual(['A', 'C']);
    const n = { notes: { concepts: [{ term: 'x' }, { term: 'y' }] } };
    expect(appMod.removeSectionItemMatching(n, 'notes', 'concepts', 0, { term: 'y' })).toBe(true);
    expect(n.notes.concepts).toEqual([{ term: 'x' }]);
  });

  it('uploadInProgress：上傳或建立會議中才延後 SW 重整', async () => {
    const appMod = await import('../js/app.js');
    expect(appMod.uploadInProgress({ phase: 'uploading' })).toBe(true);
    expect(appMod.uploadInProgress({ phase: 'creating' })).toBe(true);
    expect(appMod.uploadInProgress({ phase: 'error' })).toBe(false);
    expect(appMod.uploadInProgress(null)).toBe(false);
  });

  it('sortByName：多檔依檔名數字順序', async () => {
    const appMod = await import('../js/app.js');
    const fs = ['part10.m4a', 'part2.m4a', 'part1.m4a'].map((name) => ({ name }));
    expect(appMod.sortByName(fs).map((f) => f.name)).toEqual(['part1.m4a', 'part2.m4a', 'part10.m4a']);
  });
});

// 跳到逐字稿出處時，整段閃爍還是看不出是哪一句（段落常常很長）。
// bestSentence 在段落內再往下找一層，指出最相符的那一句。
describe('bestSentence（段落內精準定位）', () => {
  it('從長段落中找出最相符的那一句', async () => {
    const appMod = await import('../js/app.js');
    const seg = '我們先看氣冷的部分。氣冷行之有年、可靠度高、成本也低。但在有限空間內，單位體積解熱能力已經無法支援更高發熱量的 GPU。';
    const hit = appMod.bestSentence('氣冷在有限空間內單位體積解熱能力不足', seg);
    expect(hit).toContain('單位體積解熱能力');
    expect(hit).not.toContain('我們先看氣冷的部分');
  });

  it('英文句子用句點斷句也可以', async () => {
    const appMod = await import('../js/app.js');
    const seg = 'We looked at air cooling first. Liquid cooling has a leak risk of about one in a thousand.';
    expect(appMod.bestSentence('leak risk one in a thousand', seg)).toContain('leak risk');
  });

  it('完全比不上時回傳空字串（不要亂標）', async () => {
    const appMod = await import('../js/app.js');
    expect(appMod.bestSentence('完全無關的內容 zzz', '甲乙丙。丁戊己。')).toBe('');
  });

  it('段落只有一句時就回傳那一句', async () => {
    const appMod = await import('../js/app.js');
    expect(appMod.bestSentence('氣冷可靠', '氣冷行之有年可靠度高')).toBe('氣冷行之有年可靠度高');
  });
});

// 專有名詞套用時，學習筆記也必須一起訂正——否則逐字稿改對了、筆記還是錯字。
describe('applyTermInNotes（訂正也要套到學習筆記）', () => {
  const notes = () => ({
    outline: [{ title: '泰昇科技的方案', anchor: '泰昇科技提到', points: ['泰昇科技用全鋁'] }],
    concepts: [{ term: '泰昇科技', plain: '泰昇科技是散熱廠', why: '本案主角' }],
    tables: [{ title: '泰昇科技 vs 他廠', headers: ['項目', '泰昇科技'], rows: [['成本', '泰昇科技較低']] }],
    figures: ['泰昇科技實測 1600W'],
    quiz: [{ q: '泰昇科技做什麼？', a: '泰昇科技做散熱模組。' }],
  });
  const fix = (s) => String(s).split('泰昇科技').join('鈦昇科技');

  it('五區的每一個文字欄位都會被替換', async () => {
    const appMod = await import('../js/app.js');
    const n = notes();
    appMod.applyTermInNotes(n, fix);
    expect(JSON.stringify(n)).not.toContain('泰昇科技');
    expect(n.outline[0].title).toBe('鈦昇科技的方案');
    expect(n.outline[0].anchor).toBe('鈦昇科技提到');
    expect(n.outline[0].points[0]).toBe('鈦昇科技用全鋁');
    expect(n.concepts[0].term).toBe('鈦昇科技');
    expect(n.tables[0].headers[1]).toBe('鈦昇科技');
    expect(n.tables[0].rows[0][1]).toBe('鈦昇科技較低');
    expect(n.figures[0]).toContain('鈦昇科技');
    expect(n.quiz[0].q).toContain('鈦昇科技');
    expect(n.quiz[0].a).toContain('鈦昇科技');
  });

  it('沒有筆記時不會丟錯', async () => {
    const appMod = await import('../js/app.js');
    expect(() => appMod.applyTermInNotes(null, fix)).not.toThrow();
    expect(() => appMod.applyTermInNotes({}, fix)).not.toThrow();
  });
});

// 加強有時會讓某一區變差。保留前一版並可來回切換，比單向還原安全（按錯不會失去新版）。
describe('加強前後兩版切換', () => {
  it('swapSectionVersion 交換目前版與備份版（兩版都留著）', async () => {
    const appMod = await import('../js/app.js');
    const m = { summary: { qa: ['新A', '新B'] }, alt: { 'summary:qa': ['舊A'] } };
    appMod.swapSectionVersion(m, 'summary', 'qa');
    expect(m.summary.qa).toEqual(['舊A']);
    expect(m.alt['summary:qa']).toEqual(['新A', '新B']);
    // 再換一次回到原狀
    appMod.swapSectionVersion(m, 'summary', 'qa');
    expect(m.summary.qa).toEqual(['新A', '新B']);
    expect(m.alt['summary:qa']).toEqual(['舊A']);
  });

  it('學習筆記各區也適用，且互不影響', async () => {
    const appMod = await import('../js/app.js');
    const m = { notes: { concepts: [{ term: '新' }], figures: ['F'] }, alt: { 'notes:concepts': [{ term: '舊' }] } };
    appMod.swapSectionVersion(m, 'notes', 'concepts');
    expect(m.notes.concepts).toEqual([{ term: '舊' }]);
    expect(m.notes.figures).toEqual(['F']); // 其他區不動
  });

  it('沒有備份版時不做任何事', async () => {
    const appMod = await import('../js/app.js');
    const m = { summary: { qa: ['A'] } };
    expect(appMod.swapSectionVersion(m, 'summary', 'qa')).toBe(false);
    expect(m.summary.qa).toEqual(['A']);
  });

  it('removeSectionItem 刪掉指定位置的那一條', async () => {
    const appMod = await import('../js/app.js');
    const m = { summary: { mainPoints: ['一', '二', '三'] } };
    appMod.removeSectionItem(m, 'summary', 'mainPoints', 1);
    expect(m.summary.mainPoints).toEqual(['一', '三']);
    // 超出範圍不會壞掉
    appMod.removeSectionItem(m, 'summary', 'mainPoints', 9);
    expect(m.summary.mainPoints).toEqual(['一', '三']);
  });
});

// 有時候兩版各有好料 → 合併聯集去重，再由使用者刪掉多餘的
describe('合併兩版', () => {
  it('字串清單：聯集去重，目前版在前', async () => {
    const appMod = await import('../js/app.js');
    const m = { summary: { qa: ['問A 答a', '問B 答b'] }, alt: { 'summary:qa': ['問B 答b', '問C 答c'] } };
    appMod.mergeSectionVersions(m, 'summary', 'qa');
    expect(m.summary.qa).toEqual(['問A 答a', '問B 答b', '問C 答c']);
  });

  it('物件清單：概念以名詞去重、測驗以題目去重', async () => {
    const appMod = await import('../js/app.js');
    const m = {
      notes: { concepts: [{ term: 'A', plain: '新的' }], quiz: [{ q: 'Q1', a: 'a1' }] },
      alt: { 'notes:concepts': [{ term: 'A', plain: '舊的' }, { term: 'B', plain: 'b' }] },
    };
    appMod.mergeSectionVersions(m, 'notes', 'concepts');
    expect(m.notes.concepts.map((x) => x.term)).toEqual(['A', 'B']);
    expect(m.notes.concepts[0].plain).toBe('新的'); // 重複時保留目前版
  });

  it('合併後仍可用切換退回合併前', async () => {
    const appMod = await import('../js/app.js');
    const m = { summary: { qa: ['新'] }, alt: { 'summary:qa': ['舊'] } };
    appMod.mergeSectionVersions(m, 'summary', 'qa');
    expect(m.summary.qa).toEqual(['新', '舊']);
    appMod.swapSectionVersion(m, 'summary', 'qa');
    expect(m.summary.qa).toEqual(['新']); // 退回合併前的目前版
  });

  it('沒有備份版時不做任何事', async () => {
    const appMod = await import('../js/app.js');
    const m = { summary: { qa: ['A'] } };
    expect(appMod.mergeSectionVersions(m, 'summary', 'qa')).toBe(false);
    expect(m.summary.qa).toEqual(['A']);
  });
});
