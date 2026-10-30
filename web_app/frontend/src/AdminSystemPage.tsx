import { useEffect, useState, type CSSProperties } from 'react';
import axios from 'axios';
import { Activity, ArrowLeft, Cpu, Gauge, HardDrive, MemoryStick, PauseCircle, PlayCircle, RefreshCw, Server, ShieldAlert } from 'lucide-react';
import { LoginPage, apiErrorMessage, buttonStyle, fieldStyle, type AuthUser } from './AccountPortal';
import AdminWorkforceDashboard from './AdminWorkforceDashboard';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const panel: CSSProperties = { background: '#171717', border: '1px solid #303030', borderRadius: 6, padding: 16 };
const muted: CSSProperties = { color: '#a3a3a3', fontSize: 12 };

type RuntimePayload = {
  runtime: {
    maintenance_mode: boolean; reason?: string; pid: number; started_at: string; uptime_seconds: number;
    disk_free_bytes: number; process_control: { supervisor_available: boolean; restart_available: boolean; reason: string };
    resources: ResourceSample;
  };
  concurrency: { analysis_max_concurrency: number; annotation_max_concurrency: number; hard_limit: number };
  active_heavy_jobs: { analysis: number; annotation: number };
  queued_jobs: { analysis: number; annotation: number };
  active_slots: { analysis: number; annotation: number; experimental_shadow: number };
  stress_test: { active_run_id?: string | null; exclusive_mode: boolean };
};

type ResourceSample = {
  sampled_at: string; cpu_percent?: number | null; memory_percent?: number | null;
  memory_total_bytes?: number | null; memory_available_bytes?: number | null;
  process_working_set_bytes?: number | null; process_private_bytes?: number | null;
  disk_total_bytes: number; disk_free_bytes: number; disk_percent?: number | null;
};

function formatDuration(seconds: number) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const days = Math.floor(total / 86400); const hours = Math.floor((total % 86400) / 3600); const minutes = Math.floor((total % 3600) / 60);
  return `${days ? `${days} 天 ` : ''}${hours} 小時 ${minutes} 分`;
}

function formatBytes(bytes: number) { return `${(Number(bytes || 0) / 1024 ** 3).toFixed(1)} GB`; }

function ResourceChart({ samples }: { samples: ResourceSample[] }) {
  const visible = samples.slice(-120);
  const points = (field: 'cpu_percent' | 'memory_percent') => visible.map((sample, index) => {
    const x = visible.length <= 1 ? 0 : index * 100 / (visible.length - 1);
    const value = Math.max(0, Math.min(100, Number(sample[field] || 0)));
    return `${x},${100 - value}`;
  }).join(' ');
  return <div style={{ background: '#101010', border: '1px solid #292929', padding: 12 }}>
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ width: '100%', height: 150, display: 'block' }}>
      {[25, 50, 75].map(y => <line key={y} x1="0" x2="100" y1={y} y2={y} stroke="#292929" strokeWidth="0.5" />)}
      {visible.length > 1 && <polyline points={points('cpu_percent')} fill="none" stroke="#60a5fa" strokeWidth="1.7" vectorEffect="non-scaling-stroke" />}
      {visible.length > 1 && <polyline points={points('memory_percent')} fill="none" stroke="#34d399" strokeWidth="1.7" vectorEffect="non-scaling-stroke" />}
    </svg>
    <div style={{ ...muted, display: 'flex', gap: 16, marginTop: 8 }}><span style={{ color: '#60a5fa' }}>CPU</span><span style={{ color: '#34d399' }}>記憶體</span><span>每 2 秒取樣，顯示最近 4 分鐘</span></div>
  </div>;
}

export default function AdminSystemPage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [data, setData] = useState<RuntimePayload | null>(null);
  const [reason, setReason] = useState('管理員暫停新的 CAD 重型工作');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resourceSamples, setResourceSamples] = useState<ResourceSample[]>([]);

  const load = async () => {
    const response = await axios.get(`${API_BASE}/api/admin/runtime`);
    const payload: RuntimePayload = response.data;
    setData(payload);
    const sample = payload.runtime.resources;
    if (sample?.sampled_at) {
      setResourceSamples(previous => {
        if (previous.at(-1)?.sampled_at === sample.sampled_at) return previous;
        return [...previous, sample].slice(-120);
      });
    }
  };

  useEffect(() => {
    axios.get(`${API_BASE}/api/auth/me`).then(response => setUser(response.data.user)).catch(() => setUser(null)).finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    if (user?.role !== 'ADMIN') return;
    load().catch(reasonValue => setError(apiErrorMessage(reasonValue, '系統狀態載入失敗')));
    const timer = window.setInterval(() => load().catch(() => undefined), 2000);
    return () => window.clearInterval(timer);
  }, [user?.role]);

  const setMaintenance = async (enabled: boolean) => {
    const action = enabled ? '停止接受新的分析與標註工作' : '恢復接受 CAD 工作';
    if (!window.confirm(`確定要${action}嗎？`)) return;
    setBusy(true); setError('');
    try {
      await axios.post(`${API_BASE}/api/admin/runtime/maintenance`, { enabled, reason });
      await load();
    } catch (reasonValue) { setError(apiErrorMessage(reasonValue, '系統控制操作失敗')); }
    finally { setBusy(false); }
  };

  const restartBackend = async () => {
    if (!window.confirm('確定重新啟動後端？管理頁會短暫斷線並自動重新連線。')) return;
    setBusy(true); setError('');
    try {
      await axios.post(`${API_BASE}/api/admin/runtime/restart`, { force: false });
      window.setTimeout(() => window.location.reload(), 4000);
    } catch (reasonValue: any) {
      const detail = reasonValue?.response?.data?.detail;
      if (reasonValue?.response?.status === 409 && detail?.code === 'SYSTEM_BUSY') {
        const active = detail?.active_heavy_jobs || {};
        const confirmed = window.confirm(
          `目前仍有分析 ${active.analysis || 0} 筆、標註 ${active.annotation || 0} 筆。\n\n` +
          '強制重新啟動會中斷這些工作，但不會刪除原模型與暫存輸出。確定繼續？',
        );
        if (confirmed) {
          try {
            await axios.post(`${API_BASE}/api/admin/runtime/restart`, { force: true });
            window.setTimeout(() => window.location.reload(), 4000);
            return;
          } catch (forceReason) {
            setError(apiErrorMessage(forceReason, '強制重新啟動後端失敗'));
            setBusy(false);
            return;
          }
        }
        setBusy(false);
        return;
      }
      setError(apiErrorMessage(reasonValue, '後端重新啟動失敗'));
      setBusy(false);
    }
  };

  if (checking) return <div style={centerStyle}>正在確認管理員身分…</div>;
  if (!user) return <LoginPage onLogin={setUser} />;
  if (user.role !== 'ADMIN') return <div style={centerStyle}><div><ShieldAlert size={34} /><h2>需要管理員權限</h2><a href="/">返回主系統</a></div></div>;

  const maintenance = Boolean(data?.runtime.maintenance_mode);
  return <div style={{ height: '100vh', overflowY: 'auto', overflowX: 'hidden', background: '#0d0d0d', color: '#e5e5e5', scrollbarGutter: 'stable' }}>
    <header style={{ height: 46, borderBottom: '1px solid #333', background: '#171717', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}><Server size={19} color="#60a5fa" /><strong>後端監控與服務控制</strong></div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={() => load()} style={{ ...buttonStyle, background: '#262626' }}><RefreshCw size={15} />重新整理</button>
        <a href="/admin/stress-test" style={{ ...buttonStyle, textDecoration: 'none', background: '#262626' }}><Gauge size={15} />壓力測試</a>
        <a href="/admin/accounts" style={{ ...buttonStyle, textDecoration: 'none', background: '#262626' }}><ArrowLeft size={15} />管理介面</a>
      </div>
    </header>
    <main style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {error && <div style={{ ...panel, borderColor: '#7f1d1d', color: '#fecaca' }}>{error}</div>}
      <section style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12 }}>
        <Metric label="接單狀態" value={maintenance ? '維護模式' : '正常服務'} color={maintenance ? '#fbbf24' : '#34d399'} />
        <Metric label="後端 PID" value={String(data?.runtime.pid || '—')} />
        <Metric label="運行時間" value={data ? formatDuration(data.runtime.uptime_seconds) : '—'} />
        <Metric label="分析 / 標註設定" value={data ? `${data.concurrency.analysis_max_concurrency} / ${data.concurrency.annotation_max_concurrency}` : '—'} />
        <Metric label="活動分析 / 標註" value={data ? `${data.active_heavy_jobs.analysis} / ${data.active_heavy_jobs.annotation}` : '—'} />
        <Metric label="磁碟可用" value={data ? formatBytes(data.runtime.disk_free_bytes) : '—'} />
      </section>

      <section style={panel}>
        <h3 style={{ marginTop: 0, display: 'flex', gap: 8, alignItems: 'center' }}><Activity size={17} />電腦資源即時監測</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10, marginBottom: 12 }}>
          <Metric label="CPU 使用率" value={data?.runtime.resources?.cpu_percent == null ? '取樣中…' : `${Number(data.runtime.resources.cpu_percent).toFixed(1)}%`} color="#60a5fa" />
          <Metric label="記憶體使用率" value={data?.runtime.resources?.memory_percent == null ? '—' : `${Number(data.runtime.resources.memory_percent).toFixed(1)}%`} color="#34d399" />
          <Metric label="記憶體可用 / 總量" value={data?.runtime.resources ? `${formatBytes(Number(data.runtime.resources.memory_available_bytes || 0))} / ${formatBytes(Number(data.runtime.resources.memory_total_bytes || 0))}` : '—'} />
          <Metric label="後端程序 Working Set" value={data?.runtime.resources ? formatBytes(Number(data.runtime.resources.process_working_set_bytes || 0)) : '—'} />
          <Metric label="磁碟使用率" value={data?.runtime.resources?.disk_percent == null ? '—' : `${Number(data.runtime.resources.disk_percent).toFixed(1)}%`} color={Number(data?.runtime.resources?.disk_percent || 0) >= 90 ? '#fbbf24' : '#f5f5f5'} />
        </div>
        <ResourceChart samples={resourceSamples} />
        <div style={{ ...muted, marginTop: 9, display: 'flex', gap: 14, flexWrap: 'wrap' }}><span><Cpu size={13} style={{ verticalAlign: -2 }} /> CPU 來自 Windows 系統計數器</span><span><MemoryStick size={13} style={{ verticalAlign: -2 }} /> 記憶體為全機數值</span><span><HardDrive size={13} style={{ verticalAlign: -2 }} /> 磁碟為輸出目錄所在磁碟</span></div>
      </section>

      <section style={{ ...panel, display: 'grid', gridTemplateColumns: 'minmax(280px, 1fr) minmax(280px, 1fr)', gap: 18 }}>
        <div>
          <h3 style={{ marginTop: 0 }}>CAD 重型服務接單控制</h3>
          <p style={muted}>停止接單後，登入、監控、報告與既有結果仍可使用；新的上傳分析、比較與智慧標註會回覆維護中。</p>
          <input value={reason} onChange={event => setReason(event.target.value)} placeholder="維護原因" style={{ ...fieldStyle, width: '100%', margin: '8px 0 12px' }} />
          {maintenance
            ? <button disabled={busy} onClick={() => setMaintenance(false)} style={{ ...buttonStyle, background: '#166534', borderColor: '#15803d' }}><PlayCircle size={16} />恢復接單</button>
            : <button disabled={busy} onClick={() => setMaintenance(true)} style={{ ...buttonStyle, background: '#991b1b', borderColor: '#b91c1c' }}><PauseCircle size={16} />停止接單</button>}
          {data?.runtime.reason && <div style={{ ...muted, marginTop: 10 }}>目前原因：{data.runtime.reason}</div>}
        </div>
        <div>
          <h3 style={{ marginTop: 0 }}>實體程序控制</h3>
          <p style={muted}>{data?.runtime.process_control.reason || '正在檢查 supervisor…'}</p>
          <button disabled={busy || !data?.runtime.process_control.restart_available} onClick={restartBackend} style={{ ...buttonStyle, background: data?.runtime.process_control.restart_available ? '#2563eb' : '#262626', opacity: data?.runtime.process_control.restart_available ? 1 : 0.55 }}><RefreshCw size={15} />重新啟動後端</button>
          <div style={{ ...muted, marginTop: 10 }}>Supervisor：{data?.runtime.process_control.supervisor_available ? '已連線，後端受管理' : '後端未受管理（需由 supervisor 啟動）'}</div>
        </div>
      </section>

      <section style={panel}>
        <h3 style={{ marginTop: 0, display: 'flex', gap: 8, alignItems: 'center' }}><Activity size={17} />工作池即時狀態</h3>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}><thead><tr>{['工作池', '目前活動', '佇列', 'Limiter 使用量', '設定上限'].map(value => <th key={value} style={th}>{value}</th>)}</tr></thead><tbody>
          <tr><td style={td}>模型分析</td><td style={td}>{data?.active_heavy_jobs.analysis ?? '—'}</td><td style={td}>{data?.queued_jobs.analysis ?? '—'}</td><td style={td}>{data?.active_slots.analysis ?? '—'}</td><td style={td}>{data?.concurrency.analysis_max_concurrency ?? '—'}</td></tr>
          <tr><td style={td}>智慧標註</td><td style={td}>{data?.active_heavy_jobs.annotation ?? '—'}</td><td style={td}>{data?.queued_jobs.annotation ?? '—'}</td><td style={td}>{data?.active_slots.annotation ?? '—'}</td><td style={td}>{data?.concurrency.annotation_max_concurrency ?? '—'}</td></tr>
        </tbody></table>
      </section>

      <AdminWorkforceDashboard />
    </main>
  </div>;
}

function Metric({ label, value, color = '#f5f5f5' }: { label: string; value: string; color?: string }) {
  return <div style={panel}><div style={muted}>{label}</div><div style={{ color, fontSize: 22, fontWeight: 700, marginTop: 8 }}>{value}</div></div>;
}

const centerStyle: CSSProperties = { minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#171717', color: '#eee', textAlign: 'center' };
const th: CSSProperties = { textAlign: 'left', color: '#a3a3a3', padding: 9, borderBottom: '1px solid #333' };
const td: CSSProperties = { padding: 10, borderBottom: '1px solid #262626', color: '#d4d4d4' };
