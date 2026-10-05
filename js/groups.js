// 分類群組：主本在 NAS（GET/PUT /api/groups），localStorage 只當快取（秒開、離線可看）。
// 群組 = { id, name, color }；會議用 m.group 指到群組 id。
// 本機改動（新增／改名／刪除）後呼叫 pushGroups() 把整份寫回 NAS。
import * as api from './api.js';

const KEY = 'meeting_groups';
const TOMB = 'meeting_group_tombstones';

// 群組調色盤：新群組依序取色，存進群組資料（會跟著雲端同步）
export const GROUP_COLORS = ['#0a84ff', '#34c759', '#ff9500', '#af52de', '#ff2d55', '#5ac8fa', '#ffcc00', '#63e6be'];
export function groupColor(id) {
  const gs = getGroups();
  const i = gs.findIndex((g) => g.id === id);
  if (i < 0) return '#8e8e93';
  return gs[i].color || GROUP_COLORS[i % GROUP_COLORS.length];
}

export function getGroups() {
  try {
    return (JSON.parse(localStorage.getItem(KEY)) || []).sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
  } catch (_) {
    return [];
  }
}
export function setGroups(gs) {
  localStorage.setItem(KEY, JSON.stringify(gs || []));
}
export function getGroupTombstones() {
  try {
    return JSON.parse(localStorage.getItem(TOMB)) || [];
  } catch (_) {
    return [];
  }
}
export function setGroupTombstones(ids) {
  localStorage.setItem(TOMB, JSON.stringify(Array.from(new Set(ids || []))));
}
const TOMB_TIME = 'meeting_group_tombstone_times';
export function getGroupTombstoneTimes() {
  try {
    return JSON.parse(localStorage.getItem(TOMB_TIME)) || {};
  } catch (_) {
    return {};
  }
}
export function setGroupTombstoneTimes(map) {
  localStorage.setItem(TOMB_TIME, JSON.stringify(map || {}));
}

export function addGroup(name) {
  const gs = getGroups();
  const g = {
    id: 'g' + Date.now() + Math.random().toString(36).slice(2, 6),
    name: String(name || '').trim(),
    color: GROUP_COLORS[gs.length % GROUP_COLORS.length],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  if (!g.name) return null;
  gs.push(g);
  setGroups(gs);
  return g;
}
export function renameGroup(id, name) {
  const n = String(name || '').trim();
  if (!n) return;
  const gs = getGroups().map((g) => (g.id === id ? { ...g, name: n, updatedAt: Date.now() } : g));
  setGroups(gs);
}
export function removeGroup(id) {
  setGroups(getGroups().filter((g) => g.id !== id));
  const t = getGroupTombstones();
  if (!t.includes(id)) {
    t.push(id);
    setGroupTombstones(t);
  }
  const times = getGroupTombstoneTimes();
  times[id] = Date.now();
  setGroupTombstoneTimes(times);
}
export function groupName(id) {
  const g = getGroups().find((x) => x.id === id);
  return g ? g.name : '';
}

// 從 NAS 拿最新分類寫進快取；連不上就沿用快取
export async function loadGroups() {
  const gs = await api.getGroups();
  setGroups((gs || []).map((g, i) => ({ ...g, createdAt: i })));
  return getGroups();
}
// 把目前快取的分類整份寫回 NAS
export async function pushGroups() {
  await api.saveGroups(getGroups().map(({ id, name, color }) => ({ id, name, color })));
}
