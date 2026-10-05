import { describe, it, expect, beforeEach } from 'vitest';
import { getGroups, setGroups, addGroup, renameGroup, removeGroup, groupName, groupColor, GROUP_COLORS } from '../js/groups.js';

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

  it('刪除', () => {
    const g = addGroup('內部');
    removeGroup(g.id);
    expect(getGroups()).toHaveLength(0);
    expect(localStorage.getItem('meeting_group_tombstones')).toBeNull();
  });

  it('顏色只接受 #hex：惡意字串換成調色盤顏色（不會注入 style 屬性）', () => {
    setGroups([
      { id: 'a', name: 'A', color: 'red;background:url(https://evil/x)" onmouseover="alert(1)' },
      { id: 'b', name: 'B', color: '#34C759' },
      { id: 'c', name: 'C' },
    ]);
    expect(groupColor('a')).toBe(GROUP_COLORS[0]);
    expect(groupColor('b')).toBe('#34C759');
    expect(groupColor('c')).toBe(GROUP_COLORS[2]);
    expect(groupColor('none')).toBe('#8e8e93');
  });
});
