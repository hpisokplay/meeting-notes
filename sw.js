// Service Worker：快取 App 靜態殼，離線也能開啟（會議資料的離線快取在 IndexedDB，由 app 自己管）。
// /api/* 一律直接走網路、絕不快取；錄音的 Range 請求（播放器拖曳）也不攔，交給瀏覽器直接跟 NAS 談。
const CACHE = 'meeting-app-v110';
const ASSETS = [
  './',
  './index.html',
  './css/styles.css',
  './js/app.js',
  './js/api.js',
  './js/store.js',
  './js/format.js',
  './js/search.js',
  './js/export.js',
  './js/docx.js',
  './js/items.js',
  './js/groups.js',
  './js/wakelock.js',
  './manifest.webmanifest',
  './icons/icon-180.png',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches
      .open(CACHE)
      // no-cache：安裝時一律向伺服器驗證，不吃瀏覽器 HTTP 快取裡的舊檔
      .then((c) => c.addAll(ASSETS.map((a) => new Request(a, { cache: 'no-cache' }))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// 離線且快取沒有這個檔時的退路
async function offlineFallback(req) {
  const hit = await caches.match(req, { ignoreSearch: true });
  if (hit) return hit;
  const accept = req.headers.get('accept') || '';
  if (req.mode === 'navigate' || accept.includes('text/html')) {
    const idx = (await caches.match('./index.html')) || (await caches.match('./'));
    if (idx) return idx;
  }
  return new Response('離線中，且這個檔案沒有快取。', {
    status: 503,
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}

// 網路優先（network-first）：連線時一律拿最新程式碼，離線時才用快取。
self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return; // 外部資源一律走網路
  if (req.method !== 'GET') return;
  if (url.pathname.startsWith('/api/')) return; // API 與錄音：只走網路，不快取、不攔截
  if (req.headers.has('range')) return; // 媒體 Range 請求不攔
  e.respondWith(
    // no-cache：每次都帶 ETag 向伺服器驗證（沒變回 304 很省），確保拿到最新版程式碼
    fetch(req, { cache: 'no-cache' })
      .then((res) => {
        if (res.ok && res.status === 200) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      // 離線時回退到快取；ignoreSearch 讓帶 ?fresh=… 的強制更新網址也能命中 './'（否則離線白屏）
      // 都沒有：導覽請求退回快取的 index.html，其他給一個最小的離線回應（避免瀏覽器顯示網路錯誤頁）
      .catch(() => offlineFallback(req))
  );
});
