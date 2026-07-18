const DB_NAME = 'meetings-db';
const STORE = 'meetings';
const VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function run(mode, fn) {
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const os = db.transaction(STORE, mode).objectStore(STORE);
        const req = fn(os);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      })
  );
}

export async function save(meeting) {
  await run('readwrite', (os) => os.put(meeting));
  return meeting;
}

export async function get(id) {
  const result = await run('readonly', (os) => os.get(id));
  return result || null;
}

export async function list() {
  const all = (await run('readonly', (os) => os.getAll())) || [];
  return all.sort((a, b) => b.createdAt - a.createdAt);
}

export async function remove(id) {
  await run('readwrite', (os) => os.delete(id));
}

export async function exportAll() {
  const meetings = await list();
  return JSON.stringify({ exportedAt: Date.now(), meetings }, null, 2);
}
