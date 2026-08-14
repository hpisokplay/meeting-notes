// 整合測試：以 jsdom 實際載入 app.js，渲染清單與詳情，確認語者分段有正確顯示、無執行期錯誤。
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach } from 'vitest';

const BODY = `
  <header class="topbar">
    <button id="backBtn" class="ghost" hidden>‹ 返回</button>
    <h1 id="title">會議記錄</h1>
    <button id="backupBtn" class="ghost" hidden>⬇︎</button>
    <button id="settingsBtn" class="ghost">⚙︎</button>
  </header>
  <main id="view"></main>
  <nav class="tabbar">
    <button id="homeTab">📋 清單</button>
    <button id="groupsTab">📂 分類</button>
    <button id="newTab" class="primary">＋ 新增會議</button>
  </nav>`;

const tick = () => new Promise((r) => setTimeout(r, 40));

describe('app（整合）', () => {
  beforeEach(() => {
    document.body.innerHTML = BODY;
    location.hash = '#/';
  });

  it('載入 app.js 不丟錯，並能渲染語者分段詳情', async () => {
    const store = await import('../js/store.js');
    await store.save({
      id: 'test-1',
      title: '產品週會',
      createdAt: new Date('2026-07-18T10:00:00').getTime(),
      transcript: [
        { speaker: '說話者1', text: '討論上線時程' },
        { speaker: '說話者2', text: '下週三比較穩' },
        { speaker: '說話者3', text: '我明天開始測試' },
      ],
      summary: {
        actionItems: ['說話者3 明天開始測試 [DRI: 說話者3]'],
        mainPoints: ['討論上線時程'],
        qa: [],
      },
    });

    // 載入主程式（會在載入時執行 router() 並渲染清單）
    const appMod = await import('../js/app.js');
    await tick();

    const view = document.getElementById('view');
    expect(view.innerHTML).toContain('產品週會');
    // 清單卡片有分類 chip（預設未分類）
    expect(view.innerHTML).toContain('grp-chip');
    expect(view.innerHTML).toContain('未分類');

    // 切到詳情頁
    location.hash = '#/m/test-1';
    window.dispatchEvent(new Event('hashchange'));
    await tick();

    const html = document.getElementById('view').innerHTML;
    // 三位說話者標籤都要出現
    expect(html).toContain('說話者1');
    expect(html).toContain('說話者2');
    expect(html).toContain('說話者3');
    // 新版四段
    expect(html).toContain('待辦事項 Action Item');
    expect(html).toContain('會議重點 Main Point');
    expect(html).toContain('會議提問');
    expect(html).toContain('[DRI: 說話者3]');
    // Q&A 空 → 顯示「無」
    expect(html).toMatch(/會議提問[\s\S]*?無/);
    // 語者上色（style color）
    expect(html).toMatch(/class="spk" style="color:#/);
    // 匯出按鈕
    expect(html).toContain('📄 PDF');
    expect(html).toContain('📝 Word');
    // 頂部動作列：分享、重整摘要、加強按鈕、語者改名 chip、語言切換
    expect(html).toContain('📤 分享');
    expect(html).toContain('加強待辦');
    expect(html).toContain('data-enh="qa"');
    expect(html).toContain('spk-chip');
    expect(html).toContain('English');
    // 逐字稿段落可點擊編輯（原文檢視有 data-i）
    expect(html).toContain('data-i="0"');
    // 四區可摺疊（sec-head + 箭頭）
    expect(html).toContain('data-sec="ai"');
    expect(html).toContain('data-sec="tr"');
    expect(html).toContain('chev');
    // 條目可點擊跳到逐字稿出處
    expect(html).toContain('data-jump="ai:0"');
    // 出處比對：待辦「說話者3 明天開始測試」應對到第 3 段（index 2）
    expect(
      appMod.bestSegIndex('說話者3 明天開始測試 [DRI: 說話者3]', [
        { speaker: '說話者1', text: '討論上線時程' },
        { speaker: '說話者2', text: '下週三比較穩' },
        { speaker: '說話者3', text: '我明天開始測試' },
      ])
    ).toBe(2);
    // 會議問答卡片
    expect(html).toContain('問這場會議');
    expect(html).toContain('chatAsk');
    // 專有名詞訂正卡片（含掃描按鈕）
    expect(html).toContain('專有名詞訂正');
    expect(html).toContain('scanTerms');

    // 分類頁：能渲染群組清單
    const groups = await import('../js/groups.js');
    groups.addGroup('客戶會議');
    location.hash = '#/groups';
    window.dispatchEvent(new Event('hashchange'));
    await tick();
    const gHtml = document.getElementById('view').innerHTML;
    expect(gHtml).toContain('客戶會議');
    expect(gHtml).toContain('新增群組');
    expect(gHtml).toContain('未分類');
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
