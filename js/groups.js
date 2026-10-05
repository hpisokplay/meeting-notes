// 分類群組：主本在 NAS（GET/PUT /api/groups），localStorage 只當快取（秒開、離線可看）。
// 群組 = { id, name, color }；會議用 m.group 指到群組 id。
// 本機改動（新增／改名／刪除）後呼叫 pushGroups() 把整份寫回 NAS。
import * as api from './api.js';

const KEY = 'meeting_groups';

// 群組調色盤：新群組依序取色，存進群組資料（存到 NAS）
export const GROUP_COLORS = ['#0a84ff', '#34c759', '#ff9500', '#af52de', '#ff2d55', '#5ac8fa', '#ffcc00', '#63e6be'];
// 顏色會直接插進 style 屬性 → 只接受 #hex，其他一律換成調色盤顏色（防止樣式／屬性注入）
const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
export function safeColor(c, i = 0) {
  return typeof c === 'string' && HEX.test(c.trim()) ? c.trim() : GROUP_COLORS[Math.abs(i) % GROUP_COLORS.length];
}
export function groupColor(id) {
  const gs = getGroups();
  const i = gs.findIndex((g) => g.id === id);
  if (i < 0) return '#8e8e93';
  return safeColor(gs[i].color, i);
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
