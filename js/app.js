import { getApiKey, setApiKey, hasApiKey } from './settings.js';
import { list, get, save, remove, exportAll, getTombstones, applyMerged } from './store.js';
import { transcribeAndSummarize } from './gemini.js';
import { formatDate, defaultTitle, transcriptToText } from './format.js';
import { exportPdf, exportWord } from './export.js';
import * as sync from './sync.js';
import { mergeState } from './sync.js';

const view = document.getElementById('view');
const titleEl = document.getElementById('title');
const backBtn = document.getElementById('backBtn');

document.getElementById('homeTab').onclick = () => (location.hash = '#/');
document.getElementById('newTab').onclick = () => (location.hash = '#/new');
document.getElementById('settingsBtn').onclick = () => (location.hash = '#/settings');
backBtn.onclick = () => (location.hash = '#/');

const SPEAKER_PALETTE = ['#0a84ff', '#34c759', '#ff9500', '#af52de', '#ff2d55', '#5ac8fa', '#ffcc00'];

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function uid() {
  return crypto.randomUUID ? crypto.randomUUID() : 'm' + Date.now() + Math.round(performance.now());
}
function setHeader(text, showBack) {
  titleEl.textContent = text;
  backBtn.hidden = !showBack;
}
function speakerColors(segments) {
  const map = {};
  let i = 0;
  (segments || []).forEach((s) => {
    if (!(s.speaker in map)) {
      map[s.speaker] = SPEAKER_PALETTE[i % SPEAKER_PALETTE.length];
      i++;
    }
  });
  return map;
}

// ---- 雲端同步 ----
function defaultSyncConfig() {
  return sync.getSyncConfig() || { token: '', owner: 'hpisokplay', repo: 'meeting-notes-data', path: 'meetings.json' };
}
function toast(msg) {
  let t = document.getElementById('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), 2600);
}

let syncing = false;
async function syncNow(silent) {
  if (!sync.isEnabled() || syncing) return;
  syncing = true;
  try {
    if (!silent) toast('雲端同步中…');
    let remote = await sync.pull();
    let merged = mergeState({ meetings: await list(), deleted: getTombstones() }, remote.doc);
    await applyMerged(merged);
    try {
      await sync.push(merged, remote.sha);
    } catch (e) {
      if (e.message === 'CONFLICT') {
        remote = await sync.pull();
        merged = mergeState({ meetings: await list(), deleted: getTombstones() }, remote.doc);
        await applyMerged(merged);
        await sync.push(merged, remote.sha);
      } else {
        throw e;
      }
    }
    toast('雲端已同步 ✓');
  } catch (e) {
    toast('同步失敗：' + (e && e.message ? e.message : e));
  } finally {
    syncing = false;
  }
}

async function renderList() {
  setHeader('會議記錄', false);
  const meetings = await list();
  if (!meetings.length) {
    view.innerHTML = `<div class="empty">還沒有會議記錄<br>點下方「＋ 新增會議」上傳錄音檔</div>`;
    return;
  }
  view.innerHTML =
    meetings
      .map((m) => {
        const mp = (m.summary && (m.summary.mainPoints || m.summary.keyPoints)) || [];
        const snip = mp.length ? mp.join('、') : transcriptToText(m.transcript).slice(0, 60);
        return `<div class="card tap" data-id="${m.id}">
          <h3>${esc(m.title)}</h3>
          <div class="meta">${formatDate(m.createdAt)}</div>
          <div class="snippet">${esc(snip)}</div>
        </div>`;
      })
      .join('') + `<button class="big secondary" id="exportBtn">⬇︎ 匯出備份</button>`;
  view.querySelectorAll('.card').forEach((c) => {
    c.onclick = () => (location.hash = '#/m/' + c.dataset.id);
  });
  document.getElementById('exportBtn').onclick = onExport;
}

async function onExport() {
  const json = await exportAll();
  const blob = new Blob([json], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `meetings-backup-${formatDate(Date.now()).replace(/[: ]/g, '-')}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

function renderNew() {
  setHeader('新增會議', true);
  if (!hasApiKey()) {
    view.innerHTML = `<div class="card">請先到右上角 ⚙︎ 設定，填入你的 Gemini API 金鑰。
      <button class="big" id="toSettings">前往設定</button></div>`;
    document.getElementById('toSettings').onclick = () => (location.hash = '#/settings');
    return;
  }
  view.innerHTML = `
    <div class="card">
      <p style="margin-top:0">選擇會議錄音檔（mp3 / m4a / wav 等）</p>
      <input type="file" id="audio" accept="audio/*,.m4a,.mp3,.wav,.aac,.caf,.aiff" />
      <button class="big" id="go">開始辨識</button>
      <div class="warn">辨識長會議可能需要數分鐘。過程中請<b>保持螢幕開啟、不要切換到其他 App</b>，以免中斷。系統會盡量幫你維持螢幕不熄。</div>
      <details class="hint" style="margin-top:12px">
        <summary style="cursor:pointer;font-weight:600">📌 錄音在「語音備忘錄」裡？點這看怎麼匯入</summary>
        <div style="margin-top:8px">
          iPhone 不允許網頁直接讀取語音備忘錄，只要先匯出一次即可：<br>
          1. 開「語音備忘錄」App → 點該則錄音<br>
          2. 點 <b>⋯ 或分享鈕</b> → <b>儲存到「檔案」</b> → 選個位置<br>
          3. 回這裡按上面的欄位 → <b>選擇檔案</b> → 找到那個 .m4a 選取
        </div>
      </details>
      <div class="progress" id="prog" hidden></div>
    </div>`;
  const prog = document.getElementById('prog');
  const goBtn = document.getElementById('go');

  goBtn.onclick = async () => {
    const f = document.getElementById('audio').files[0];
    if (!f) {
      alert('請先選擇音檔');
      return;
    }
    goBtn.disabled = true;
    prog.hidden = false;
    const show = (msg, isErr) => {
      prog.innerHTML = isErr
        ? `<div class="err">❌ ${esc(msg)}</div><button class="big secondary" id="retry">再試一次</button>`
        : `<div class="spinner"></div><div>${esc(msg)}</div>`;
      if (isErr) document.getElementById('retry').onclick = () => renderNew();
    };
    show('準備中…');

    // 長錄音防止螢幕鎖住中斷
    let wakeLock = null;
    try {
      if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen');
    } catch (_) {}

    try {
      const { transcript, summary } = await transcribeAndSummarize(f, getApiKey(), {
        onProgress: (msg) => show(msg),
      });
      const ts = Date.now();
      const meeting = { id: uid(), title: defaultTitle(f.name, ts), createdAt: ts, updatedAt: ts, transcript, summary };
      await save(meeting);
      location.hash = '#/m/' + meeting.id;
      syncNow();
    } catch (e) {
      show(e && e.message ? e.message : '發生未知錯誤', true);
      goBtn.disabled = false;
    } finally {
      if (wakeLock) {
        try {
          await wakeLock.release();
        } catch (_) {}
      }
    }
  };
}

async function renderDetail(id) {
  const m = await get(id);
  if (!m) {
    location.hash = '#/';
    return;
  }
  setHeader('會議詳情', true);
  const s = m.summary || {};
  // 向後相容舊記錄（keyPoints/decisions）
  const actionItems = s.actionItems || [];
  const mainPoints = s.mainPoints || s.keyPoints || [];
  const qa = s.qa || [];
  const olHtml = (arr) =>
    arr && arr.length
      ? `<ol class="list">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ol>`
      : `<div class="meta" style="padding-left:4px">（無）</div>`;

  const colors = speakerColors(m.transcript);
  const segHtml = (m.transcript || [])
    .map((s) => `<div class="seg"><span class="spk" style="color:${colors[s.speaker] || 'var(--ink)'}">${esc(s.speaker)}</span>${esc(s.text)}</div>`)
    .join('');

  view.innerHTML = `
    <div class="card">
      <input type="text" id="titleInput" value="${esc(m.title)}" />
      <div class="meta" style="margin-top:8px">${formatDate(m.createdAt)}</div>
      <div class="export-row">
        <button class="btn-export" id="pdfBtn">📄 匯出 PDF</button>
        <button class="btn-export" id="wordBtn">📝 匯出 Word (docx)</button>
      </div>
    </div>
    <div class="card">
      <div class="section-title" style="margin-top:0">✅ 待辦事項 Action Item <button class="copy" data-copy="ai">複製</button></div>
      ${olHtml(actionItems)}
      <div class="section-title">📌 會議重點 Main Point <button class="copy" data-copy="mp">複製</button></div>
      ${olHtml(mainPoints)}
      <div class="section-title">❓ 會議提問 Q&amp;A <button class="copy" data-copy="qa">複製</button></div>
      ${qa && qa.length ? olHtml(qa) : '<div class="meta" style="padding-left:4px">無</div>'}
    </div>
    <div class="section-title">🗣️ 逐字稿 <button class="copy" data-copy="tr">複製</button></div>
    <div class="transcript-box">${segHtml || '<div class="meta">（無逐字稿）</div>'}</div>
    <button class="big danger" id="del" style="margin-top:16px">刪除這場會議</button>`;

  document.getElementById('pdfBtn').onclick = () => exportPdf(m);
  document.getElementById('wordBtn').onclick = () => exportWord(m);

  document.getElementById('titleInput').onchange = async (e) => {
    m.title = e.target.value.trim() || m.title;
    m.updatedAt = Date.now();
    await save(m);
    syncNow();
  };
  const numbered = (arr) => (arr || []).map((x, i) => `${i + 1}. ${x}`).join('\n');
  const texts = {
    ai: numbered(actionItems),
    mp: numbered(mainPoints),
    qa: qa && qa.length ? numbered(qa) : '無',
    tr: transcriptToText(m.transcript),
  };
  view.querySelectorAll('.copy').forEach((b) => {
    b.onclick = async () => {
      try {
        await navigator.clipboard.writeText(texts[b.dataset.copy] || '');
        const old = b.textContent;
        b.textContent = '已複製';
        setTimeout(() => (b.textContent = old), 1200);
      } catch (_) {
        alert('複製失敗，請手動選取');
      }
    };
  });
  document.getElementById('del').onclick = async () => {
    if (confirm('確定刪除這場會議記錄？此動作無法復原。')) {
      await remove(id);
      location.hash = '#/';
      syncNow();
    }
  };
}

function renderSettings() {
  setHeader('設定', true);
  const cfg = defaultSyncConfig();
  const enabled = sync.isEnabled();
  view.innerHTML = `
    <div class="card">
      <p style="margin-top:0"><b>Gemini API 金鑰</b></p>
      <input type="password" id="key" placeholder="貼上你的金鑰" value="${esc(getApiKey())}" autocomplete="off" />
      <button class="big" id="saveKey">儲存</button>
      <div class="hint">
        金鑰只存在這支手機（不會上傳到任何伺服器）。<br>
        取得方式：到 <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com</a> →
        API Keys → 複製你的免費金鑰。
      </div>
    </div>
    <div class="card">
      <p style="margin-top:0"><b>☁️ GitHub 雲端同步（跨裝置記憶）</b>
        <span class="meta">${enabled ? '｜狀態：已開啟' : '｜狀態：未開啟（只存本機）'}</span></p>
      <input type="password" id="ghToken" placeholder="貼上 GitHub 權杖（token）" value="${esc(cfg.token || '')}" autocomplete="off" />
      <input type="text" id="ghRepo" placeholder="owner/repo" value="${esc((cfg.owner || '') + '/' + (cfg.repo || ''))}" style="margin-top:8px" />
      <button class="big" id="saveSync">儲存並同步</button>
      <button class="big secondary" id="syncBtn" style="margin-top:8px">立即同步</button>
      <button class="big secondary" id="clearSync" style="margin-top:8px">關閉同步（僅存本機）</button>
      <div class="hint">
        開啟後，會議記錄會存到你的<b>私人</b>資料庫 <code>${esc(cfg.owner)}/${esc(cfg.repo)}</code>，電腦與手機共用、長期保存。<br>
        <b>如何拿權杖：</b>到 <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">GitHub → Fine-grained tokens</a> →
        Repository access 選「Only select repositories → <b>meeting-notes-data</b>」→ Permissions 的
        <b>Contents</b> 設為 <b>Read and write</b> → 產生後貼到上面。權杖只存這台裝置。
      </div>
    </div>`;
  document.getElementById('saveKey').onclick = () => {
    setApiKey(document.getElementById('key').value);
    toast('金鑰已儲存');
  };
  document.getElementById('saveSync').onclick = async () => {
    const token = document.getElementById('ghToken').value.trim();
    const repoField = document.getElementById('ghRepo').value.trim();
    const [owner, repo] = repoField.split('/');
    if (!token || !owner || !repo) {
      alert('請填入權杖與 owner/repo');
      return;
    }
    sync.setSyncConfig({ token, owner, repo, path: 'meetings.json' });
    await syncNow();
    router();
  };
  document.getElementById('syncBtn').onclick = () => syncNow();
  document.getElementById('clearSync').onclick = () => {
    sync.clearSyncConfig();
    toast('已關閉雲端同步');
    router();
  };
}

function router() {
  const h = location.hash || '#/';
  if (h.startsWith('#/m/')) return renderDetail(h.slice(4));
  if (h === '#/new') return renderNew();
  if (h === '#/settings') return renderSettings();
  return renderList();
}
window.addEventListener('hashchange', router);
router();

// 啟動時若已開啟雲端同步：先拉取合併，再重新整理當前畫面
if (sync.isEnabled()) {
  syncNow(true).then(() => router());
}

// 註冊 Service Worker（PWA / 離線）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));
}
