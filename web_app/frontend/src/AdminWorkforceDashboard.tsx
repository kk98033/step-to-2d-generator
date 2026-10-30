import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import axios from 'axios';
import { Activity, AlertTriangle, Clock3, Download, Search, Users } from 'lucide-react';
import { apiErrorMessage, buttonStyle, fieldStyle } from './AccountPortal';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const panel: CSSProperties = { background: '#171717', border: '1px solid #303030', borderRadius: 6, padding: 16 };
const muted: CSSProperties = { color: '#a3a3a3', fontSize: 12 };
const th: CSSProperties = { textAlign: 'left', color: '#a3a3a3', padding: 9, borderBottom: '1px solid #333', whiteSpace: 'nowrap' };
const td: CSSProperties = { padding: 9, borderBottom: '1px solid #262626', color: '#d4d4d4', verticalAlign: 'top' };

function localTime(value?: string) {
  if (!value) return '—';
  return new Date(value).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false });
}

function duration(seconds?: number) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 60) return `${value.toFixed(0)} 秒`;
  return `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒`;
}

function pageLabel(page?: string) {
  const labels: Record<string, string> = {
    '/': '主系統', '/admin/accounts': '帳號管理', '/admin/system': '系統監控',
    '/admin/stress-test': '容量壓測', '/admin/api-graph': 'API Graph',
  };
  if (page?.startsWith('/admin/operations/')) return '工作重現';
  return labels[page || ''] || page || '未知頁面';
}

function csvCell(value: unknown) {
  return `"${String(value ?? '').replaceAll('"', '""')}"`;
}

function downloadCsv(filename: string, rows: unknown[][]) {
  const csv = `\ufeff${rows.map(row => row.map(csvCell).join(',')).join('\r\n')}`;
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function TrendChart({ trend }: { trend: any[] }) {
  const values = trend.map(item => Number(item.seen_users || 0));
  const active = trend.map(item => Number(item.active_users || 0));
  const maximum = Math.max(1, ...values);
  const line = (items: number[]) => items.map((value, index) => {
    const x = items.length <= 1 ? 0 : index * 100 / (items.length - 1);
    return `${x},${96 - value / maximum * 88}`;
  }).join(' ');
  return <div style={{ background: '#101010', border: '1px solid #292929', padding: 12 }}>
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ width: '100%', height: 145, display: 'block' }}>
      {[25, 50, 75].map(y => <line key={y} x1="0" x2="100" y1={y} y2={y} stroke="#292929" strokeWidth="0.5" />)}
      {values.length > 1 && <polyline points={line(values)} fill="none" stroke="#60a5fa" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />}
      {active.length > 1 && <polyline points={line(active)} fill="none" stroke="#34d399" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />}
    </svg>
    <div style={{ ...muted, display: 'flex', justifyContent: 'space-between', gap: 12 }}><span><span style={{ color: '#60a5fa' }}>●</span> 曾出現人數　<span style={{ color: '#34d399' }}>●</span> 活動人數</span><span>最近 60 分鐘，峰值 {maximum} 人</span></div>
  </div>;
}

export default function AdminWorkforceDashboard() {
  const [workforce, setWorkforce] = useState<any>(null);
  const [operationLogs, setOperationLogs] = useState<any[]>([]);
  const [auditEvents, setAuditEvents] = useState<any[]>([]);
  const [search, setSearch] = useState('');
  const [presenceFilter, setPresenceFilter] = useState('ALL');
  const [logFilter, setLogFilter] = useState('ALL');
  const [error, setError] = useState('');

  const loadWorkforce = async () => {
    const response = await axios.get(`${API_BASE}/api/admin/workforce`);
    setWorkforce(response.data);
  };
  const loadLogs = async () => {
    const [operations, audit] = await Promise.all([
      axios.get(`${API_BASE}/api/admin/operation-logs?limit=300`),
      axios.get(`${API_BASE}/api/admin/audit-events?limit=300`),
    ]);
    setOperationLogs(operations.data.logs || []);
    setAuditEvents(audit.data.events || []);
  };

  useEffect(() => {
    const refreshWorkforce = () => loadWorkforce().catch(reason => setError(apiErrorMessage(reason, '人流資料載入失敗')));
    const refreshLogs = () => loadLogs().catch(reason => setError(apiErrorMessage(reason, '操作紀錄載入失敗')));
    refreshWorkforce(); refreshLogs();
    const workforceTimer = window.setInterval(refreshWorkforce, 5000);
    const logTimer = window.setInterval(refreshLogs, 15000);
    return () => { window.clearInterval(workforceTimer); window.clearInterval(logTimer); };
  }, []);

  const users = useMemo(() => (workforce?.users || []).filter((user: any) => {
    if (presenceFilter !== 'ALL' && user.status !== presenceFilter) return false;
    const query = search.trim().toLowerCase();
    return !query || [user.display_name, user.email, user.username, user.page, ...(user.jobs || []).map((job: any) => `${job.model_id} ${job.part_id} ${job.job_id}`)]
      .some(value => String(value || '').toLowerCase().includes(query));
  }), [workforce, presenceFilter, search]);

  const activities = useMemo(() => {
    const operations = operationLogs.map(item => ({
      kind: 'CAD', at: item.created_at, user: item.engineer?.display_name || item.engineer?.email || '未知工程師',
      action: `${item.model_id || '未知模型'} / ${item.part_id || '未知零件'}`,
      status: item.status, detail: item.error || item.message || item.operation_id,
      operation_id: item.operation_id,
    }));
    const audits = auditEvents.map(item => ({
      kind: 'ADMIN', at: item.created_at, user: item.actor_display_name || item.actor_email || '系統',
      action: item.action, status: 'AUDIT', detail: `${item.target_type || 'SYSTEM'} ${item.target_id || ''}`,
      operation_id: undefined,
    }));
    const live = (workforce?.live_activities || []).map((item: any) => ({
      kind: 'LIVE', at: item.at, user: item.display_name || item.email || '未知使用者',
      action: item.action, status: String(item.status_code), detail: `${item.method} ${item.path}`,
      operation_id: undefined,
    }));
    const query = search.trim().toLowerCase();
    return [...operations, ...audits, ...live]
      .filter(item => logFilter === 'ALL' || item.kind === logFilter)
      .filter(item => !query || [item.user, item.action, item.status, item.detail].some(value => String(value || '').toLowerCase().includes(query)))
      .sort((left, right) => Date.parse(right.at || '') - Date.parse(left.at || ''))
      .slice(0, 300);
  }, [operationLogs, auditEvents, workforce, logFilter, search]);

  const summary = workforce?.summary || {};
  return <>
    <section style={panel}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div><h3 style={{ margin: 0, display: 'flex', gap: 8, alignItems: 'center' }}><Users size={17} />系統即時人流與容量</h3><div style={{ ...muted, marginTop: 6 }}>在線以 90 秒內 heartbeat 為準；閒置為 5 分鐘內沒有瀏覽器互動。</div></div>
        <button onClick={() => downloadCsv('workforce-presence.csv', [['姓名', 'Email', '狀態', '頁面', '最後活動', '工作'], ...users.map((user: any) => [user.display_name, user.email, user.status, pageLabel(user.page), user.last_seen_at, (user.jobs || []).map((job: any) => `${job.workload}:${job.status}:${job.job_id}`).join(' | ')])])} style={{ ...buttonStyle, background: '#262626' }}><Download size={14} />匯出人流 CSV</button>
      </div>
      {error && <div style={{ marginTop: 10, color: '#fca5a5' }}>{error}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 9, marginTop: 14 }}>
        {[
          ['在線', summary.online, '#34d399'], ['閒置', summary.idle, '#fbbf24'], ['剛離線', summary.recently_offline, '#a3a3a3'],
          ['CAD 執行中', summary.active_cad_users, '#60a5fa'], ['CAD 排隊中', summary.queued_cad_users, '#f97316'],
          ['建議同時重型容量', workforce?.capacity?.recommended_simultaneous_heavy_users ?? '—', '#e5e5e5'],
        ].map(([label, value, color]) => <div key={String(label)} style={{ background: '#101010', border: '1px solid #292929', padding: 11 }}><div style={muted}>{label}</div><strong style={{ display: 'block', marginTop: 6, fontSize: 22, color: String(color) }}>{String(value ?? 0)}</strong></div>)}
      </div>
      {(workforce?.alerts || []).length > 0 && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>{workforce.alerts.map((alert: any) => <div key={alert.code} style={{ border: '1px solid #92400e', background: '#21180d', color: '#fbbf24', padding: '8px 10px', fontSize: 12, display: 'flex', gap: 7 }}><AlertTriangle size={14} />{alert.message}</div>)}</div>}
      <div style={{ marginTop: 14 }}><TrendChart trend={workforce?.trend || []} /></div>
    </section>

    <section style={panel}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>使用者即時狀態</h3>
        <div style={{ display: 'flex', gap: 8 }}><select value={presenceFilter} onChange={event => setPresenceFilter(event.target.value)} style={fieldStyle}><option value="ALL">全部</option><option value="ONLINE">在線</option><option value="IDLE">閒置</option><option value="OFFLINE">剛離線</option></select><label style={{ position: 'relative' }}><Search size={14} style={{ position: 'absolute', left: 9, top: 9, color: '#777' }} /><input value={search} onChange={event => setSearch(event.target.value)} placeholder="搜尋人員、模型、Job" style={{ ...fieldStyle, paddingLeft: 29, width: 240 }} /></label></div>
      </div>
      <div style={{ overflowX: 'auto', maxHeight: 440, overflowY: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}><thead><tr>{['使用者', '狀態', '目前頁面', '最後活動', 'CAD 工作', '排隊 / 預估'].map(label => <th key={label} style={th}>{label}</th>)}</tr></thead><tbody>
        {users.map((user: any) => <tr key={user.user_id}><td style={td}><strong>{user.display_name}</strong><div style={muted}>{user.email || user.username}</div></td><td style={{ ...td, color: user.status === 'ONLINE' ? '#34d399' : user.status === 'IDLE' ? '#fbbf24' : '#737373' }}>{user.status === 'ONLINE' ? '在線' : user.status === 'IDLE' ? '閒置' : '剛離線'}</td><td style={td}>{pageLabel(user.page)}</td><td style={td}>{localTime(user.last_seen_at)}</td><td style={td}>{(user.jobs || []).length ? user.jobs.map((job: any) => <div key={job.job_id}><strong>{job.workload === 'analysis' ? '分析' : '標註'} · {job.status}</strong><div style={muted}>{job.filename || job.model_id || job.part_id || job.job_id}</div></div>) : <span style={muted}>無重型工作</span>}</td><td style={td}>{(user.jobs || []).map((job: any) => <div key={job.job_id}>{job.queue_position ? `第 ${job.queue_position} 位 · 約 ${duration(job.estimated_wait_seconds)}` : '執行中'}</div>)}</td></tr>)}
        {!users.length && <tr><td style={td} colSpan={6}>目前沒有符合條件的使用者。</td></tr>}
      </tbody></table></div>
    </section>

    <section style={panel}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}><div><h3 style={{ margin: 0, display: 'flex', gap: 8, alignItems: 'center' }}><Activity size={16} />工程師與管理操作 Log</h3><div style={{ ...muted, marginTop: 5 }}>重用既有 operation audit、管理 audit 與即時 API 操作摘要；不記錄 request body、密碼、滑鼠移動或輸入中文字。</div></div><div style={{ display: 'flex', gap: 8 }}><select value={logFilter} onChange={event => setLogFilter(event.target.value)} style={fieldStyle}><option value="ALL">全部紀錄</option><option value="LIVE">即時操作</option><option value="CAD">CAD 工作</option><option value="ADMIN">管理操作</option></select><button onClick={() => downloadCsv('workforce-activity.csv', [['時間', '類型', '使用者', '操作', '狀態', '詳細'], ...activities.map(item => [item.at, item.kind, item.user, item.action, item.status, item.detail])])} style={{ ...buttonStyle, background: '#262626' }}><Download size={14} />匯出 Log CSV</button></div></div>
      <div style={{ overflowX: 'auto', maxHeight: 460, overflowY: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}><thead><tr>{['時間', '類型', '使用者', '操作', '狀態', '詳細'].map(label => <th key={label} style={th}>{label}</th>)}</tr></thead><tbody>{activities.map((item, index) => <tr key={`${item.kind}-${item.at}-${index}`}><td style={td}><Clock3 size={13} style={{ verticalAlign: -2, marginRight: 5 }} />{localTime(item.at)}</td><td style={td}>{item.kind}</td><td style={td}>{item.user}</td><td style={td}>{item.operation_id ? <a href={`/admin/operations/${encodeURIComponent(item.operation_id)}`} style={{ color: '#60a5fa' }}>{item.action}</a> : item.action}</td><td style={td}>{item.status}</td><td style={{ ...td, maxWidth: 430, overflowWrap: 'anywhere' }}>{item.detail}</td></tr>)}</tbody></table></div>
    </section>
  </>;
}
