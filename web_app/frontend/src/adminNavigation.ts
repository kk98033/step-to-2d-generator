export type AccountSection = 'accounts' | 'import' | 'passwords' | 'tags' | 'trash' | 'logs' | 'settings';

export const accountSections: Record<string, AccountSection> = {
  '/admin/accounts': 'accounts', '/admin/accounts/import': 'import', '/admin/accounts/passwords': 'passwords',
  '/admin/tags': 'tags', '/admin/trash': 'trash', '/admin/logs': 'logs', '/admin/settings': 'settings',
};

export const adminGroups = [
  { label: '管理總覽', items: [{ path: '/admin', label: '管理總覽', icon: 'overview' }] },
  { label: '使用者管理', items: [{ path: '/admin/accounts', label: '帳號管理', icon: 'users' }] },
  { label: '系統運行', items: [
    { path: '/admin/presence', label: '使用者即時狀態', icon: 'presence' },
    { path: '/admin/system', label: '資源與工作池監控', icon: 'resources' },
    { path: '/admin/service-control', label: '服務控制', icon: 'control' },
    { path: '/admin/stress-test', label: 'CAD 壓力測試', icon: 'stress' },
  ] },
  { label: '資料與紀錄', items: [
    { path: '/admin/tags', label: '推薦標籤', icon: 'tags' },
    { path: '/admin/trash', label: '全域回收桶', icon: 'trash' },
    { path: '/admin/logs', label: '系統紀錄', icon: 'logs' },
  ] },
  { label: '系統管理', items: [
    { path: '/admin/settings', label: '系統設定', icon: 'settings' },
    { path: '/admin/api-graph', label: 'API 關係圖（進階）', icon: 'graph' },
  ] },
];

export function adminActivePath(path: string) {
  if (path.startsWith('/admin/accounts/')) return '/admin/accounts';
  if (path.startsWith('/admin/operations/')) return '/admin/logs';
  return path;
}
