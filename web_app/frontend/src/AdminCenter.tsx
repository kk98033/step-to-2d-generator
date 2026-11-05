import { lazy, Suspense, useEffect, useState, type MouseEvent } from 'react';
import axios from 'axios';
import { Activity, ArchiveRestore, ArrowLeft, Cpu, Gauge, LayoutDashboard, Menu, Network, Power, Settings, ShieldCheck, Tags, Users, X } from 'lucide-react';
import { LoginPage, apiErrorMessage, buttonStyle, type AuthUser } from './AccountPortal';
import { accountSections, adminActivePath, adminGroups } from './adminNavigation';
import { auditActionLabels, requestInfo } from './AuditRequestDetails';
import './adminCenter.css';
import SystemStatus from './SystemStatus';

const Accounts = lazy(() => import('./AdminAccountPage'));
const System = lazy(() => import('./AdminSystemPage'));
const Workforce = lazy(() => import('./AdminWorkforceDashboard'));
const Stress = lazy(() => import('./AdminStressTestPage'));
const Graph = lazy(() => import('./AdminApiGraphPage'));
const Replay = lazy(() => import('./AdminOperationReplayPage'));
const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const icons: Record<string, typeof Users> = { overview: LayoutDashboard, users: Users, presence: Activity, resources: Cpu, control: Power, stress: Gauge, tags: Tags, trash: ArchiveRestore, logs: Activity, settings: Settings, graph: Network };

export default function AdminCenter() {
  const [path, setPath] = useState(window.location.pathname.replace(/\/$/, '') || '/admin');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [runtime, setRuntime] = useState<any>(null);
  const [workforce, setWorkforce] = useState<any>(null);
  const [connected, setConnected] = useState(false);
  const [sampledAt, setSampledAt] = useState('');
  const activePath = adminActivePath(path);
  const item = adminGroups.flatMap(group => group.items).find(entry => entry.path === activePath);

  useEffect(() => {
    axios.get(`${API_BASE}/api/auth/me`).then(response => setUser(response.data.user))
      .catch(() => setUser(null)).finally(() => setChecking(false));
    const onPop = () => { setPath(window.location.pathname.replace(/\/$/, '') || '/admin'); setMenuOpen(false); };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  useEffect(() => {
    if (user?.role !== 'ADMIN') return;
    let disposed = false;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const [system, people] = await Promise.allSettled([
          axios.get(`${API_BASE}/api/admin/runtime`, { timeout: 5000 }),
          axios.get(`${API_BASE}/api/admin/workforce`, { timeout: 5000 }),
        ]);
        if (disposed) return;
        setConnected(system.status === 'fulfilled');
        if (system.status === 'fulfilled') { setRuntime(system.value.data); setSampledAt(new Date().toLocaleTimeString('zh-TW', { hour12: false })); }
        if (people.status === 'fulfilled') setWorkforce(people.value.data);
      } finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [user?.id, user?.role]);

  const navigate = (event: MouseEvent<HTMLDivElement>) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as HTMLElement).closest('a');
    if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin || !(url.pathname === '/admin' || url.pathname.startsWith('/admin/'))) return;
    event.preventDefault();
    if (url.href !== window.location.href) window.history.pushState(null, '', url.pathname + url.search + url.hash);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  if (checking) return <div className="admin-gate">正在驗證管理員權限…</div>;
  if (!user) return <LoginPage onLogin={setUser} />;
  if (user.role !== 'ADMIN') return <div className="admin-gate"><div><h2>需要管理員權限</h2><a href="/" style={buttonStyle}>返回主系統</a></div></div>;

  let content;
  if (accountSections[path]) content = <Accounts section={accountSections[path]} />;
  else if (path === '/admin') content = <Overview runtime={runtime} workforce={workforce} connected={connected} />;
  else if (path === '/admin/presence') content = <div className="admin-page"><Workforce view="presence" /></div>;
  else if (path === '/admin/system') content = <System mode="monitor" />;
  else if (path === '/admin/service-control') content = <System mode="control" />;
  else if (path === '/admin/stress-test') content = <Stress />;
  else if (path === '/admin/api-graph') content = <Graph />;
  else if (path.startsWith('/admin/operations/')) content = <Replay key={path} />;
  else content = <div className="admin-page"><h1>找不到此管理頁</h1><a href="/admin">返回管理總覽</a></div>;

  return <div className="admin-center" onClick={navigate}>
    <header className="admin-topbar">
      <button className="admin-menu-toggle" onClick={() => setMenuOpen(!menuOpen)} aria-label="切換管理導覽" aria-expanded={menuOpen}>{menuOpen ? <X size={19} /> : <Menu size={19} />}</button>
      <a href="/admin" className="admin-brand"><ShieldCheck size={22} color="#60a5fa" />系統管理中心</a>
      <div className="admin-status"><SystemStatus snapshot={{ connected, maintenance: Boolean(runtime?.runtime?.maintenance_mode), online: workforce?.summary?.online ?? null, idle: workforce?.summary?.idle ?? null, executing: workforce?.summary?.active_cad_users ?? null, queued: workforce?.summary?.queued_cad_users ?? null }} /><span title="最後成功更新時間">更新 {sampledAt || '—'}</span></div>
      <span className="admin-identity">{user.display_name || user.email}</span><a href="/" className="admin-home"><ArrowLeft size={15} />主系統</a>
    </header>
    {menuOpen && <button className="admin-menu-backdrop" onClick={() => setMenuOpen(false)} aria-label="關閉管理導覽" />}
    <aside className={`admin-sidebar ${menuOpen ? 'is-open' : ''}`}><nav aria-label="管理中心導覽">{adminGroups.map(group => <section key={group.label}><h2>{group.label}</h2>{group.items.map(entry => {
      const Icon = icons[entry.icon];
      return <a key={entry.path} href={entry.path} aria-current={activePath === entry.path ? 'page' : undefined} className={activePath === entry.path ? 'is-active' : ''}><Icon size={17} />{entry.label}</a>;
    })}</section>)}</nav><div className="admin-sidebar-user">{user.email}<br />管理員</div></aside>
    <div className="admin-workspace">
      <div className="admin-breadcrumb">管理中心 / {path.startsWith('/admin/operations/') ? '系統紀錄 / CAD 工作詳情' : item?.label || '管理頁'}</div>
      {activePath === '/admin/accounts' && <nav className="admin-subnav" aria-label="帳號管理功能">{[['/admin/accounts', '帳號清單'], ['/admin/accounts/import', '批次匯入'], ['/admin/accounts/passwords', '密碼管理']].map(([href, label]) => <a href={href} key={href} aria-current={path === href ? 'page' : undefined}>{label}</a>)}</nav>}
      <div className="admin-content" key={activePath}><Suspense fallback={<div className="admin-page" role="status">正在載入管理功能…</div>}>{content}</Suspense></div>
    </div>
  </div>;
}

function Overview({ runtime, workforce, connected }: any) {
  const [events, setEvents] = useState<any[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    const load = () => axios.get(`${API_BASE}/api/admin/audit-events?limit=12`).then(response => {
      if (!disposed) { setEvents(response.data.events || []); setError(''); }
    }).catch(reason => { if (!disposed) setError(apiErrorMessage(reason, '近期紀錄載入失敗')); });
    void load();
    const timer = window.setInterval(load, 15000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);
  const resources = runtime?.runtime?.resources;
  return <div className="admin-page"><h1>管理總覽</h1><p className="admin-description">目前服務狀態、使用人數、CAD 工作與近期事件。</p>
    {!connected && <div className="admin-notice">正在重新連線後端；已有數據保留至下次更新。</div>}
    <div className="admin-metrics">{[
      ['在線／閒置', `${workforce?.summary?.online ?? '—'} / ${workforce?.summary?.idle ?? '—'}`],
      ['執行／排隊人數', `${workforce?.summary?.active_cad_users ?? '—'} / ${workforce?.summary?.queued_cad_users ?? '—'}`],
      ['CPU', resources?.cpu_percent == null ? '—' : `${Number(resources.cpu_percent).toFixed(1)}%`],
      ['記憶體', resources?.memory_percent == null ? '—' : `${Number(resources.memory_percent).toFixed(1)}%`],
    ].map(([label, value]) => <div className="admin-card" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
    <div className="admin-quicklinks"><a href="/admin/presence">查看使用者與排隊</a><a href="/admin/system">查看資源監控</a><a href="/admin/logs">查詢完整紀錄</a></div>
    {(workforce?.alerts || []).map((alert: any) => <div key={alert.code} className="admin-notice">{alert.message}</div>)}
    <section className="admin-card"><h2>近期系統事件</h2>{error && <p role="alert">{error}</p>}{!events.length && <p className="admin-description">尚無可顯示的紀錄。</p>}{events.map(event => {
      const info = requestInfo(event.details);
      return <div className="admin-event" key={event.id}><div><strong>{auditActionLabels[event.action] || event.action}</strong><span>{info.actor_display_name || event.actor_display_name || info.attempted_identifier || event.actor_email || '系統'} · IP {info.client_ip || '未記錄'}</span></div><span>{new Date(event.created_at).toLocaleString('zh-TW', { hour12: false })}</span></div>;
    })}</section>
  </div>;
}
