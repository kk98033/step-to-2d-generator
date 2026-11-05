import { useEffect, useState } from 'react';
import axios from 'axios';
import './systemTheme.css';

type Status = { connected: boolean; maintenance: boolean; online: number | null; idle: number | null; executing: number | null; queued: number | null };
const initial: Status = { connected: false, maintenance: false, online: null, idle: null, executing: null, queued: null };
const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export default function SystemStatus({ snapshot }: { snapshot?: Status }) {
  const [current, setCurrent] = useState(initial);
  useEffect(() => {
    if (snapshot !== undefined) return;
    let disposed = false;
    let pending = false;
    const controller = new AbortController();
    const load = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const { data } = await axios.get(`${API_BASE}/api/system/status`, { timeout: 5000, signal: controller.signal });
        if (!disposed) setCurrent({ connected: true, maintenance: data.maintenance_mode, online: data.online, idle: data.idle, executing: data.executing_users, queued: data.queued_users });
      } catch {
        if (!disposed) setCurrent(value => ({ ...value, connected: false }));
      } finally { pending = false; }
    };
    void load();
    const timer = window.setInterval(load, 10000);
    document.addEventListener('visibilitychange', load);
    return () => { disposed = true; controller.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', load); };
  }, [snapshot]);
  const status = snapshot ?? current;
  return <div className="system-status" role="status" title="在線、閒置與 CAD 執行／排隊人數；離線時保留最後一次數值。">
    <span className="system-status-service" style={{ color: !status.connected || status.maintenance ? '#fbbf24' : '#34d399' }}>{!status.connected ? '重新連線中' : status.maintenance ? '維護模式' : '服務正常'}</span>
    <span>在線 {status.online ?? '—'} · 閒置 {status.idle ?? '—'}</span>
    <span>執行 {status.executing ?? '—'} · 排隊 {status.queued ?? '—'}</span>
  </div>;
}
