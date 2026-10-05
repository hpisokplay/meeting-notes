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
    saveMeeting: vi.fn(async (m) => {
      nas.calls.push(['saveMeeting', m.id]);
      nas.meetings[m.id] = clone(m);
      return clone(m);
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
