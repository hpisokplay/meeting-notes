// 本機快取（IndexedDB）。資料主本在 NAS；這裡只為了「先秒開上次的畫面、再向伺服器更新」與離線瀏覽。
// 任何寫入失敗都不能擋住畫面，所以全部吞錯。
const DB_NAME = 'meetings-cache';
const MEETINGS = 'meetings';
const KV = 'kv';
const VERSION = 1;

let dbp = null;
function openDb() {
  if (dbp) return dbp;
  dbp = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(MEETINGS)) db.createObjectStore(MEETINGS, { keyPath: 'id' });
      if (!db.objectStoreNames.contains(KV)) db.createObjectStore(KV);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => {
      dbp = null;
      reject(req.error);
    };
  });
  return dbp;
}

function run(storeName, mode, fn) {
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const os = db.transaction(storeName, mode).objectStore(storeName);
        const r = fn(os);
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
      })
  );
}

const byNewest = (a, b) => (b.createdAt || 0) - (a.createdAt || 0);

// 會議清單（伺服器 GET /api/meetings 的結果，不含逐字稿）
export async function cacheList(list) {
  try {
    await run(KV, 'readwrite', (os) => os.put(Array.isArray(list) ? list : [], 'list'));
  } catch (_) {}
  return list;
}
export async function cachedList() {
  try {
    const l = await run(KV, 'readonly', (os) => os.get('list'));
    return Array.isArray(l) ? l.slice().sort(byNewest) : [];
  } catch (_) {
    return [];
  }
}

// 完整會議（含逐字稿）
export async function cacheMeeting(m) {
  if (!m || !m.id) return m;
  try {
    await run(MEETINGS, 'readwrite', (os) => os.put(m));
  } catch (_) {}
  return m;
}
export async function cachedMeeting(id) {
  try {
    return (await run(MEETINGS, 'readonly', (os) => os.get(id))) || null;
  } catch (_) {
    return null;
  }
}
export async function uncacheMeeting(id) {
  try {
    await run(MEETINGS, 'readwrite', (os) => os.delete(id));
    const l = await cachedList();
    await cacheList(l.filter((m) => m.id !== id));
  } catch (_) {}
}
