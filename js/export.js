// 匯出：把一場會議產生成可列印/可下載的文件。
// - PDF：開新視窗載入乾淨排版後呼叫列印（中文字體用系統字體最穩，iPhone 也能存成 PDF）。
// - Word：產生 Word 可開啟的 .doc（HTML 格式），保留中文與排版、可再編輯。
import { formatDate } from './format.js';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function ol(items) {
  return items && items.length
    ? '<ol>' + items.map((i) => `<li>${esc(i)}</li>`).join('') + '</ol>'
    : '<p class="none">（無）</p>';
}

export function safeFileName(title) {
  return (String(title || 'meeting').replace(/[\\/:*?"<>|]+/g, '_').trim() || 'meeting').slice(0, 80);
}

// 會議內容主體 HTML（PDF 與 Word 共用）
export function meetingToHtmlBody(meeting) {
  const s = meeting.summary || {};
  const actionItems = s.actionItems || [];
  const mainPoints = s.mainPoints || s.keyPoints || [];
  const qa = s.qa || [];
  const segs = (meeting.transcript || [])
    .map((seg) => `<p class="seg"><strong>${esc(seg.speaker)}：</strong>${esc(seg.text)}</p>`)
    .join('');
  return (
    `<h1>${esc(meeting.title)}</h1>` +
    `<p class="date">${esc(formatDate(meeting.createdAt))}</p>` +
    `<h2>待辦事項 Action Item</h2>${ol(actionItems)}` +
    `<h2>會議重點 Main Point</h2>${ol(mainPoints)}` +
    `<h2>會議提問 Q&amp;A</h2>${qa.length ? ol(qa) : '<p class="none">無</p>'}` +
    `<h2>逐字稿 Transcribe</h2>${segs || '<p class="none">（無逐字稿）</p>'}`
  );
}

const STYLE = `
  body{font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;line-height:1.7;color:#111;max-width:820px;margin:24px auto;padding:0 18px;}
  h1{font-size:22px;margin:0 0 4px;}
  .date{color:#666;margin:0 0 18px;}
  h2{font-size:16px;border-bottom:2px solid #0a84ff;padding-bottom:4px;margin:22px 0 8px;color:#0a6;}
  ul{margin:6px 0;padding-left:22px;} li{margin:5px 0;}
  p{margin:6px 0;} .seg{margin:4px 0;} .none{color:#999;}
  @media print{ body{margin:0;} }
`;

export function fullHtmlDoc(meeting) {
  return (
    `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">` +
    `<title>${esc(meeting.title)}</title><style>${STYLE}</style></head>` +
    `<body>${meetingToHtmlBody(meeting)}</body></html>`
  );
}

function downloadBlob(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

export function exportWord(meeting) {
  // Word 可直接開啟 HTML 內容的 .doc；加 BOM 確保中文正確。
  const blob = new Blob(['﻿' + fullHtmlDoc(meeting)], { type: 'application/msword' });
  downloadBlob(blob, safeFileName(meeting.title) + '.doc');
}

export function exportPdf(meeting) {
  const w = window.open('', '_blank');
  if (!w) {
    alert('請允許彈出視窗，才能匯出 PDF（或改用「匯出 Word」）。');
    return;
  }
  w.document.write(fullHtmlDoc(meeting));
  w.document.close();
  w.focus();
  // 等版面完成再叫列印；使用者在列印畫面選「儲存為 PDF」。
  setTimeout(() => {
    try {
      w.print();
    } catch (_) {}
  }, 500);
}
