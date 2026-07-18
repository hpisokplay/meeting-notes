import { getApiKey, setApiKey, hasApiKey } from './settings.js';
import { list, get, save, remove, exportAll } from './store.js';
import { transcribeAndSummarize } from './gemini.js';
import { formatDate, defaultTitle, transcriptToText } from './format.js';

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
        const kp = (m.summary && m.summary.keyPoints) || [];
        const snip = kp.length ? kp.join('、') : transcriptToText(m.transcript).slice(0, 60);
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
      <input type="file" id="audio" accept="audio/*" />
      <button class="big" id="go">開始辨識</button>
      <div class="warn">辨識長會議可能需要數分鐘。過程中請<b>保持螢幕開啟、不要切換到其他 App</b>，以免中斷。系統會盡量幫你維持螢幕不熄。</div>
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
      const meeting = { id: uid(), title: defaultTitle(f.name, ts), createdAt: ts, transcript, summary };
      await save(meeting);
      location.hash = '#/m/' + meeting.id;
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
  const listHtml = (arr) =>
    arr && arr.length
      ? `<ul class="list">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`
      : `<div class="meta" style="padding-left:4px">（無）</div>`;

  const colors = speakerColors(m.transcript);
  const segHtml = (m.transcript || [])
    .map((s) => `<div class="seg"><span class="spk" style="color:${colors[s.speaker] || 'var(--ink)'}">${esc(s.speaker)}</span>${esc(s.text)}</div>`)
    .join('');

  view.innerHTML = `
    <div class="card">
      <input type="text" id="titleInput" value="${esc(m.title)}" />
      <div class="meta" style="margin-top:8px">${formatDate(m.createdAt)}</div>
    </div>
    <div class="card">
      <div class="section-title" style="margin-top:0">📌 重點條列 <button class="copy" data-copy="kp">複製</button></div>
      ${listHtml(m.summary.keyPoints)}
      <div class="section-title">✅ 待辦事項 <button class="copy" data-copy="ai">複製</button></div>
      ${listHtml(m.summary.actionItems)}
      <div class="section-title">📝 決議事項 <button class="copy" data-copy="dc">複製</button></div>
      ${listHtml(m.summary.decisions)}
    </div>
    <div class="section-title">🗣️ 逐字稿 <button class="copy" data-copy="tr">複製</button></div>
    <div class="transcript-box">${segHtml || '<div class="meta">（無逐字稿）</div>'}</div>
    <button class="big danger" id="del">刪除這場會議</button>`;

  document.getElementById('titleInput').onchange = async (e) => {
    m.title = e.target.value.trim() || m.title;
    await save(m);
  };
  const texts = {
    kp: (m.summary.keyPoints || []).join('\n'),
    ai: (m.summary.actionItems || []).join('\n'),
    dc: (m.summary.decisions || []).join('\n'),
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
    }
  };
}

function renderSettings() {
  setHeader('設定', true);
  view.innerHTML = `
    <div class="card">
      <p style="margin-top:0">Gemini API 金鑰</p>
      <input type="password" id="key" placeholder="貼上你的金鑰" value="${esc(getApiKey())}" autocomplete="off" />
      <button class="big" id="saveKey">儲存</button>
      <div class="hint">
        金鑰只存在這支手機（不會上傳到任何伺服器）。<br>
        取得方式：到 <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com</a> →
        API Keys → 複製你的免費金鑰。
      </div>
    </div>`;
  document.getElementById('saveKey').onclick = () => {
    setApiKey(document.getElementById('key').value);
    alert('已儲存');
    location.hash = '#/';
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

// 註冊 Service Worker（PWA / 離線）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));
}
