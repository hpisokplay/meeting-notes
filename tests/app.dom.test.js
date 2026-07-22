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
