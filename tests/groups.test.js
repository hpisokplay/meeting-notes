import { describe, it, expect, beforeEach } from 'vitest';
import { getGroups, addGroup, renameGroup, removeGroup, groupName, getGroupTombstones } from '../js/groups.js';

describe('groups（分類群組）', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('新增／改名／查名', () => {
    const g = addGroup('客戶會議');
    expect(g.id).toBeTruthy();
    expect(getGroups()).toHaveLength(1);
    expect(groupName(g.id)).toBe('客戶會議');
    renameGroup(g.id, '供應商');
    expect(groupName(g.id)).toBe('供應商');
  });

  it('空白名稱不會新增', () => {
    expect(addGroup('   ')).toBeNull();
    expect(getGroups()).toHaveLength(0);
  });

  it('刪除會留下墓碑（供跨裝置同步刪除）', () => {
    const g = addGroup('內部');
    removeGroup(g.id);
    expect(getGroups()).toHaveLength(0);
    expect(getGroupTombstones()).toContain(g.id);
  });
});
