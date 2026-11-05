import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import axios from 'axios';
import {
  Activity, AlertTriangle, CheckCircle2, Cpu, Database,
  Download, Gauge, History, MemoryStick, Play, RefreshCw, RotateCcw,
  Square, Trash2, Users,
} from 'lucide-react';
import { LoginPage, apiErrorMessage, buttonStyle, fieldStyle, type AuthUser } from './AccountPortal';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const TAIPEI_TIME_ZONE = 'Asia/Taipei';

type Fixture = {
  id: string;
  name: string;
  relative_path: string;
  bytes: number;
  size_class: 'small' | 'medium' | 'large';
  source?: 'system_model' | 'uploaded' | 'derived_part';
};

type LiveSample = {
  at: string;
  cpu_percent?: number | null;
  memory_percent?: number | null;
};

type StressRun = {
  run_id: string;
  status: string;
  phase: string;
  level?: number;
  round?: number;
  max_concurrency?: number;
  rounds?: number;
  active_workers?: number;
  elapsed_seconds?: number;
  engine?: string;
  test_mode?: 'annotation_capacity' | 'auto_dual_capacity';
  fixture_id?: string;
  fixture_name?: string;
  exclusive?: boolean;
  requested_at?: string;
  latest_health?: { ok?: boolean; latency_seconds?: number; status?: number };
  latest_resources?: LiveSample & {
    memory_available_bytes?: number;
    disk_free_bytes?: number;
  };
  recommendation?: Record<string, any>;
  report?: Record<string, any> | null;
  report_json_url?: string | null;
  report_markdown_url?: string | null;
  error?: string;
  message?: string;
  events?: Array<{ at: string; event: string; message: string; level?: number; round?: number }>;
};

const panel: CSSProperties = {
  background: '#171717', border: '1px solid #303030', borderRadius: 6, padding: 16,
};

const muted: CSSProperties = { color: '#a3a3a3', fontSize: 12 };

function formatBytes(value?: number) {
  const bytes = Number(value || 0);
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatDuration(seconds?: number) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 60) return `${value.toFixed(1)} 秒`;
  const minutes = Math.floor(value / 60);
  const remainder = Math.round(value % 60);
  return `${minutes} 分 ${remainder} 秒`;
}

function localDateTime(value?: string) {
  if (!value) return '時間未記錄';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '時間未記錄';
  return date.toLocaleString('zh-TW', {
    timeZone: TAIPEI_TIME_ZONE,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

function runTimestamp(run: StressRun) {
  const value = run.requested_at || String(run.report?.generated_at || '');
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function MetricCard({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail?: string }) {
  return <div style={{ ...panel, minWidth: 165, flex: '1 1 165px' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, color: '#a3a3a3', fontSize: 11 }}>{icon}{label}</div>
    <div style={{ color: '#f5f5f5', fontSize: 23, fontWeight: 700, marginTop: 8 }}>{value}</div>
    {detail && <div style={{ ...muted, marginTop: 5 }}>{detail}</div>}
  </div>;
}

function ReportField({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return <div style={{ background: '#111', border: `1px solid ${warn ? '#92400e' : '#292929'}`, borderRadius: 4, padding: 11 }}>
    <div style={muted}>{label}</div>
    <div style={{ color: warn ? '#fbbf24' : '#e5e5e5', fontSize: 14, fontWeight: 600, marginTop: 6, wordBreak: 'break-word' }}>{value}</div>
  </div>;
}

function Sparkline({ samples, field, color }: { samples: LiveSample[]; field: 'cpu_percent' | 'memory_percent'; color: string }) {
  const values = samples.map(sample => Number(sample[field] || 0)).slice(-120);
  const points = values.map((value, index) => {
    const x = values.length <= 1 ? 0 : index * 100 / (values.length - 1);
    return `${x},${100 - Math.max(0, Math.min(100, value))}`;
  }).join(' ');
  return <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ width: '100%', height: 90, background: '#101010', border: '1px solid #292929', borderRadius: 4 }}>
    {[25, 50, 75].map(y => <line key={y} x1="0" x2="100" y1={y} y2={y} stroke="#292929" strokeWidth="0.5" />)}
    {points && <polyline points={points} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />}
  </svg>;
}

function CapacityCharts({ assessments }: { assessments: any[] }) {
  if (!assessments.length) return null;
  const points = assessments.map((item, index) => ({
    ...item,
    index,
    throughput: Number(item.average_throughput_jobs_per_second || 0),
    cpu: Number(item.maximum_cpu_percent || 0),
    memory: Number(item.maximum_memory_percent || 0),
    shortLabel: `${item.workload === 'analysis' ? '分' : item.workload === 'mixed' ? '混' : '標'}${item.level}`,
  }));
  const maxThroughput = Math.max(0.001, ...points.map(item => item.throughput));
  const x = (index: number) => points.length <= 1 ? 50 : 8 + index * 84 / (points.length - 1);
  const line = (field: 'cpu' | 'memory') => points.map(item => `${x(item.index)},${92 - Math.min(100, item[field]) * 0.82}`).join(' ');
  const axisLabels = <div style={{ display: 'grid', gridTemplateColumns: `repeat(${points.length}, minmax(28px, 1fr))`, gap: 2, margin: '5px 4% 0', overflowX: 'auto' }}>
    {points.map(item => <span key={`${item.workload}-${item.level}`} title={`${item.workload} level ${item.level}`} style={{ color: item.passed ? '#a3a3a3' : '#fca5a5', fontSize: 10, textAlign: 'center', whiteSpace: 'nowrap' }}>{item.shortLabel}</span>)}
  </div>;
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12, marginBottom: 16 }}>
    <div style={{ background: '#101010', border: '1px solid #292929', padding: 12 }}>
      <div style={{ ...muted, marginBottom: 8 }}>吞吐量與容量關係（jobs/s）</div>
      <svg viewBox="0 0 100 94" preserveAspectRatio="none" style={{ width: '100%', height: 160, display: 'block' }}>
        {[25, 50, 75].map(y => <line key={y} x1="5" x2="96" y1={y} y2={y} stroke="#292929" strokeWidth="0.5" />)}
        {points.map(item => {
          const height = item.throughput / maxThroughput * 78;
          return <g key={`${item.workload}-${item.level}`}>
            <rect x={x(item.index) - 3} y={92 - height} width="6" height={height} fill={item.passed ? '#2563eb' : '#7f1d1d'} />
          </g>;
        })}
      </svg>
      {axisLabels}
      <div style={muted}>藍色為 PASS，紅色為 FAIL；分／標／混代表分析、標註、混合工作池。</div>
    </div>
    <div style={{ background: '#101010', border: '1px solid #292929', padding: 12 }}>
      <div style={{ ...muted, marginBottom: 8 }}>資源峰值與容量關係</div>
      <svg viewBox="0 0 100 94" preserveAspectRatio="none" style={{ width: '100%', height: 160, display: 'block' }}>
        {[25, 50, 75].map(y => <line key={y} x1="5" x2="96" y1={92 - y * 0.82} y2={92 - y * 0.82} stroke="#292929" strokeWidth="0.5" />)}
        <line x1="5" x2="96" y1={92 - 85 * 0.82} y2={92 - 85 * 0.82} stroke="#f59e0b" strokeWidth="0.7" strokeDasharray="2 2" />
        <polyline points={line('cpu')} fill="none" stroke="#60a5fa" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
        <polyline points={line('memory')} fill="none" stroke="#34d399" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
      </svg>
      {axisLabels}
      <div style={{ ...muted, display: 'flex', gap: 16 }}><span style={{ color: '#60a5fa' }}>CPU</span><span style={{ color: '#34d399' }}>記憶體</span><span style={{ color: '#f59e0b' }}>85% 警戒線</span></div>
    </div>
  </div>;
}

export default function AdminStressTestPage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [fixtures, setFixtures] = useState<Fixture[]>([]);
  const [history, setHistory] = useState<StressRun[]>([]);
  const [current, setCurrent] = useState<StressRun | null>(null);
  const [concurrency, setConcurrency] = useState({ analysis_max_concurrency: 1, annotation_max_concurrency: 1, hard_limit: 8 });
  const [probeMaxConcurrency, setProbeMaxConcurrency] = useState(32);
  const [fixtureId, setFixtureId] = useState('');
  const [engine, setEngine] = useState<'legacy_smart' | 'experimental_v026'>('legacy_smart');
  const [testMode, setTestMode] = useState<'annotation_capacity' | 'auto_dual_capacity'>('auto_dual_capacity');
  const [maxConcurrency, setMaxConcurrency] = useState(16);
  const [rounds, setRounds] = useState(2);
  const [timeoutSeconds, setTimeoutSeconds] = useState(900);
  const [exclusive, setExclusive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [samples, setSamples] = useState<LiveSample[]>([]);

  const load = async () => {
    const [configResponse, runsResponse] = await Promise.all([
      axios.get(`${API_BASE}/api/admin/stress-tests/config`),
      axios.get(`${API_BASE}/api/admin/stress-tests`),
    ]);
    const available = configResponse.data.fixtures || [];
    setFixtures(available);
    setConcurrency(configResponse.data.concurrency);
    const probeLimit = configResponse.data.probe_max_concurrency || 32;
    setProbeMaxConcurrency(probeLimit);
    setMaxConcurrency(previous => Math.min(previous, probeLimit));
    setFixtureId(previous => previous || available[0]?.id || '');
    const runs: StressRun[] = [...(runsResponse.data.runs || [])]
      .sort((left, right) => runTimestamp(right) - runTimestamp(left));
    setHistory(runs);
    const activeId = configResponse.data.active_run_id;
    const preferredId = activeId
      || (current && runs.some(item => item.run_id === current.run_id) ? current.run_id : null)
      || runs[0]?.run_id;
    if (preferredId) {
      const detailResponse = await axios.get(`${API_BASE}/api/admin/stress-tests/${preferredId}`);
      setCurrent(detailResponse.data.run);
    } else {
      setCurrent(null);
    }
  };

  useEffect(() => {
    axios.get(`${API_BASE}/api/auth/me`)
      .then(response => setUser(response.data.user))
      .catch(() => setUser(null))
      .finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    if (user?.role !== 'ADMIN') return;
    load().catch(reason => setError(apiErrorMessage(reason, '壓力測試設定載入失敗')));
  }, [user?.role]);

  useEffect(() => {
    if (!current?.run_id || !['starting', 'running', 'stopping'].includes(current.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await axios.get(`${API_BASE}/api/admin/stress-tests/${current.run_id}`);
        const run: StressRun = response.data.run;
        setCurrent(run);
        if (run.latest_resources) {
          setSamples(previous => [...previous, run.latest_resources as LiveSample].slice(-180));
        }
        if (!['starting', 'running', 'stopping'].includes(run.status)) await load();
      } catch (reason) {
        setError(apiErrorMessage(reason, '即時壓測狀態更新失敗'));
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [current?.run_id, current?.status]);

  const start = async () => {
    if (!fixtureId) return;
    const warning = exclusive
      ? '獨占模式會暫停其他使用者送出新的模型分析與智慧標註工作。'
      : '非獨占模式的結果會受到其他使用者工作影響。';
    const scope = testMode === 'auto_dual_capacity' ? '分析池與標註池自動搜尋，並執行混合驗證' : '標註池容量階梯';
    if (!window.confirm(`${warning}\n\n${scope}，探測上限 ${maxConcurrency}。確定開始嗎？`)) return;
    setBusy(true);
    setError('');
    setSamples([]);
    try {
      const response = await axios.post(`${API_BASE}/api/admin/stress-tests`, {
        fixture_id: fixtureId,
        engine,
        test_mode: testMode,
        max_concurrency: maxConcurrency,
        rounds,
        job_timeout_seconds: timeoutSeconds,
        exclusive,
      });
      setCurrent(response.data.run);
      await load();
    } catch (reason) {
      setError(apiErrorMessage(reason, '壓力測試啟動失敗'));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!current || !window.confirm('停止派發後續壓測工作？目前執行中的 Worker 會先安全收尾。')) return;
    await axios.post(`${API_BASE}/api/admin/stress-tests/${current.run_id}/stop`);
    setCurrent({ ...current, status: 'stopping' });
  };

  const applyRecommendation = async () => {
    if (!current?.report || !window.confirm('套用報告建議的智慧標註同時上限？目前分析上限不會變更。')) return;
    setBusy(true);
    try {
      const response = await axios.post(`${API_BASE}/api/admin/stress-tests/${current.run_id}/apply-recommendation`);
      setConcurrency(response.data.settings);
      await load();
    } catch (reason) {
      setError(apiErrorMessage(reason, '建議設定套用失敗'));
    } finally {
      setBusy(false);
    }
  };

  const restoreSettings = async () => {
    if (!current || !window.confirm('還原為這次壓測開始前的分析與標註容量設定？')) return;
    setBusy(true);
    try {
      const response = await axios.post(`${API_BASE}/api/admin/stress-tests/${current.run_id}/restore-settings`);
      setConcurrency(response.data.settings);
      await load();
    } catch (reason) {
      setError(apiErrorMessage(reason, '設定還原失敗'));
    } finally {
      setBusy(false);
    }
  };

  const openHistory = async (runId: string) => {
    setError('');
    setSamples([]);
    try {
      const response = await axios.get(`${API_BASE}/api/admin/stress-tests/${runId}`);
      setCurrent(response.data.run);
    } catch (reason) {
      setError(apiErrorMessage(reason, '歷史報告載入失敗'));
    }
  };

  const deleteHistory = async (run: StressRun) => {
    if (['starting', 'running', 'stopping'].includes(run.status)) return;
    if (!window.confirm(`刪除 ${localDateTime(run.requested_at || String(run.report?.generated_at || ''))} 的壓力測試報告？\n\n報告、事件與 Worker 輸出都會一併刪除。`)) return;
    setBusy(true);
    setError('');
    try {
      await axios.delete(`${API_BASE}/api/admin/stress-tests/${run.run_id}`);
      if (current?.run_id === run.run_id) setCurrent(null);
      await load();
    } catch (reason) {
      setError(apiErrorMessage(reason, '歷史報告刪除失敗'));
    } finally {
      setBusy(false);
    }
  };

  const report = current?.report || null;
  const recommendation = report?.recommendation || current?.recommendation || null;
  const capacityPlanning = report?.capacity_planning || null;
  const assessments: any[] = recommendation?.level_assessments || [];
  const reportResults: any[] = report?.results || [];
  const reportWorkers = reportResults.flatMap(result => result.workers || []);
  const reportFailures = reportWorkers.filter((worker: any) => worker.status !== 'ok' || Number(worker.exit_code || 0) !== 0);
  const reportCpuPeak = Math.max(0, ...reportResults.map(item => Number(item.resources?.cpu_peak_percent || 0)));
  const reportMemoryPeak = Math.max(0, ...reportResults.map(item => Number(item.resources?.memory_peak_percent || 0)));
  const reportMinAvailability = reportResults.length ? Math.min(...reportResults.map(item => Number(item.health?.availability_percent || 0))) : 0;
  const liveCpu = Number(current?.latest_resources?.cpu_percent || 0);
  const liveMemory = Number(current?.latest_resources?.memory_percent || 0);
  const running = !!current && ['starting', 'running', 'stopping'].includes(current.status);
  const selectedFixture = useMemo(() => fixtures.find(item => item.id === fixtureId), [fixtures, fixtureId]);
  const capacityRows = useMemo(() => {
    if (capacityPlanning?.pools?.length) {
      return capacityPlanning.pools.map((pool: any) => ({
        workload: pool.workload,
        label: pool.workload === 'analysis' ? '分析工作池' : '標註工作池',
        slots: Number(pool.recommended_active_slots || 0),
        verifiedCeiling: Number(pool.verified_individual_ceiling || 0),
        averageSeconds: Number(pool.measured_average_job_seconds || 0),
        scenarios: pool.queue_scenarios || [],
      }));
    }
    if (!recommendation) return [];
    const definitions = recommendation.recommended_analysis_concurrency !== undefined
      ? [
          { workload: 'analysis', label: '分析工作池', slots: Number(recommendation.recommended_analysis_concurrency || 0) },
          { workload: 'annotation', label: '標註工作池', slots: Number(recommendation.recommended_annotation_concurrency || 0) },
        ]
      : [{ workload: 'annotation', label: '標註工作池', slots: Number(recommendation.recommended_active_heavy_users || 0) }];
    return definitions.filter(item => item.slots > 0).map(item => {
      const matching = reportResults.filter(result => result.workload === item.workload && Number(result.level) === item.slots && !result.failures);
      const mixed = reportResults.filter(result => result.workload === 'mixed'
        && Number(result[`${item.workload}_workers`] || 0) === item.slots && !result.failures);
      const source = matching.length ? matching : mixed;
      const workerDurations = source.flatMap(result => (result.workers || []))
        .filter((worker: any) => worker.workload === item.workload && Number(worker.elapsed_seconds || 0) > 0)
        .map((worker: any) => Number(worker.elapsed_seconds));
      const averageSeconds = workerDurations.length
        ? workerDurations.reduce((sum: number, seconds: number) => sum + seconds, 0) / workerDurations.length
        : source.length
          ? source.reduce((sum, result) => sum + Number(result.wall_seconds || 0), 0) / source.length
          : 0;
      const scenarios = [10, 20, 50, 100].map(users => {
        const waits = Array.from({ length: users }, (_, index) => Math.floor(index / item.slots) * averageSeconds);
        const ordered = [...waits].sort((left, right) => left - right);
        return {
          submitted_users: users,
          active_immediately: Math.min(users, item.slots),
          queued_initially: Math.max(0, users - item.slots),
          average_wait_seconds: waits.reduce((sum, value) => sum + value, 0) / users,
          p95_wait_seconds: ordered[Math.max(0, Math.ceil(users * 0.95) - 1)] || 0,
          last_user_starts_after_seconds: Math.max(...waits, 0),
          estimated_queue_drain_seconds: Math.max(...waits, 0) + averageSeconds,
        };
      });
      return { ...item, verifiedCeiling: 0, averageSeconds, scenarios };
    });
  }, [capacityPlanning, recommendation, reportResults]);

  if (checking) return <div style={centerStyle}>正在確認管理員權限…</div>;
  if (!user) return <LoginPage onLogin={setUser} />;
  if (user.role !== 'ADMIN') return <div style={centerStyle}><div><h2>權限不足</h2><p>此頁面僅供系統管理員使用。</p><a href="/" style={buttonStyle}>返回主系統</a></div></div>;

  return <div style={{ minHeight: '100%', background: '#101010', color: '#e5e5e5' }}>
    <header style={{ height: 58, position: 'sticky', top: 0, zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 18px', background: '#171717', borderBottom: '1px solid #303030' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Gauge size={20} color="#60a5fa" />
        <strong>CAD 容量壓力測試中心</strong>
        <span style={muted}>管理員專用 · 隔離 Worker · 不寫入案例庫</span>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={() => load().catch(reason => setError(apiErrorMessage(reason, '重新整理失敗')))} style={{ ...buttonStyle, background: '#262626' }}><RefreshCw size={15} />重新整理</button>
      </div>
    </header>

    <main style={{ padding: 18, display: 'grid', gridTemplateColumns: 'minmax(300px, 370px) minmax(0, 1fr)', gap: 16 }}>
      <aside style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <section style={panel}>
          <h3 style={{ margin: '0 0 14px', fontSize: 15 }}>測試設定</h3>
          <label style={labelStyle}>STEP 測試模型
            <select value={fixtureId} onChange={event => setFixtureId(event.target.value)} disabled={running} style={{ ...fieldStyle, width: '100%', marginTop: 6 }}>
              {!fixtures.length && <option value="">找不到可用的 STEP 模型</option>}
              {fixtures.map(fixture => <option key={fixture.id} value={fixture.id}>{fixture.name} · {fixture.source === 'system_model' ? '系統原始模型' : fixture.source === 'uploaded' ? '使用者上傳' : '拆件模型'} · {formatBytes(fixture.bytes)} · {fixture.size_class}</option>)}
            </select>
          </label>
          {!fixtures.length && <div style={{ color: '#fca5a5', fontSize: 12, marginTop: -4, marginBottom: 12 }}>模型目錄目前沒有可供壓測的 .stp／.step 檔案，請先上傳模型或重新整理。</div>}
          {selectedFixture && <div style={{ ...muted, marginTop: -4, marginBottom: 12, wordBreak: 'break-all' }}>{selectedFixture.relative_path}</div>}
          <label style={labelStyle}>繪圖引擎
            <select value={engine} onChange={event => setEngine(event.target.value as typeof engine)} disabled={running} style={{ ...fieldStyle, width: '100%', marginTop: 6 }}>
              <option value="legacy_smart">現行繪圖引擎</option>
              <option value="experimental_v026">新版實驗繪圖引擎</option>
            </select>
          </label>
          <label style={labelStyle}>測試模式
            <select value={testMode} onChange={event => setTestMode(event.target.value as typeof testMode)} disabled={running} style={{ ...fieldStyle, width: '100%', marginTop: 6 }}>
              <option value="auto_dual_capacity">自動決定分析 / 標註容量</option>
              <option value="annotation_capacity">只測標註繪圖容量</option>
            </select>
          </label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <label style={labelStyle}>{testMode === 'auto_dual_capacity' ? '每個工作池探測上限' : '最高同時人數'}
              <input type="number" min={1} max={probeMaxConcurrency} value={maxConcurrency} onChange={event => setMaxConcurrency(Number(event.target.value))} disabled={running} style={{ ...fieldStyle, width: '100%', marginTop: 6 }} />
            </label>
            <label style={labelStyle}>每階層輪數
              <input type="number" min={1} max={5} value={rounds} onChange={event => setRounds(Number(event.target.value))} disabled={running} style={{ ...fieldStyle, width: '100%', marginTop: 6 }} />
            </label>
          </div>
          <div style={{ ...muted, marginTop: -3, marginBottom: 10 }}>{testMode === 'auto_dual_capacity' ? '使用倍增與二分搜尋分別找出分析／標註容量，再做混合負載驗證。' : '將從 1 人逐級增加到第一個失敗階層。'} 探測上限 {probeMaxConcurrency}；正式服務硬上限 {concurrency.hard_limit} 與探測範圍分開。</div>
          <label style={labelStyle}>單輪逾時秒數
            <input type="number" min={30} max={3600} value={timeoutSeconds} onChange={event => setTimeoutSeconds(Number(event.target.value))} disabled={running} style={{ ...fieldStyle, width: '100%', marginTop: 6 }} />
          </label>
          <label style={{ display: 'flex', gap: 9, alignItems: 'flex-start', color: '#d4d4d4', fontSize: 12, margin: '12px 0' }}>
            <input type="checkbox" checked={exclusive} onChange={event => setExclusive(event.target.checked)} disabled={running} />
            <span>獨占重型工作模式<br /><span style={muted}>暫停其他人新增分析與標註，登入與查看結果不受影響。</span></span>
          </label>
          {!running
            ? <button onClick={start} disabled={busy || !fixtureId} style={{ ...buttonStyle, width: '100%', justifyContent: 'center', background: '#2563eb', borderColor: '#2563eb' }}><Play size={16} />開始容量階梯測試</button>
            : <button onClick={stop} style={{ ...buttonStyle, width: '100%', justifyContent: 'center', background: '#7f1d1d', borderColor: '#991b1b' }}><Square size={15} />停止後續測試</button>}
        </section>

        <section style={panel}>
          <h3 style={{ margin: '0 0 12px', fontSize: 15, display: 'flex', gap: 7, alignItems: 'center' }}><History size={16} />歷史報告</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7, maxHeight: 310, overflowY: 'auto' }}>
            {history.map(run => <div key={run.run_id} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 34px', border: `1px solid ${current?.run_id === run.run_id ? '#2563eb' : '#333'}`, background: '#202020', borderRadius: 4 }}>
              <button onClick={() => openHistory(run.run_id)} style={{ minWidth: 0, textAlign: 'left', border: 0, background: 'transparent', color: '#ddd', padding: 9, cursor: 'pointer' }}>
                <div style={{ fontSize: 12, fontWeight: 650 }}>{localDateTime(run.requested_at || String(run.report?.generated_at || ''))}</div>
                <div style={{ ...muted, marginTop: 4 }}>{run.status} · {run.engine} · {run.fixture_name}</div>
                <div style={{ color: '#737373', fontSize: 10, marginTop: 3 }}>{run.run_id}</div>
              </button>
              <button onClick={() => deleteHistory(run)} disabled={busy || ['starting', 'running', 'stopping'].includes(run.status)} title="刪除這筆報告" aria-label={`刪除 ${run.run_id}`} style={{ border: 0, borderLeft: '1px solid #333', background: 'transparent', color: '#a3a3a3', cursor: 'pointer', display: 'grid', placeItems: 'center' }}><Trash2 size={15} /></button>
            </div>)}
            {!history.length && <div style={muted}>尚無壓力測試紀錄。</div>}
          </div>
        </section>
      </aside>

      <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {error && <div style={{ ...panel, borderColor: '#7f1d1d', color: '#fecaca', display: 'flex', gap: 8 }}><AlertTriangle size={17} />{error}</div>}
        {current?.error && <div style={{ ...panel, borderColor: '#7f1d1d', color: '#fecaca', display: 'flex', gap: 8 }}><AlertTriangle size={17} />{current.error}</div>}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <MetricCard icon={<Users size={15} />} label="目前測試階層" value={current ? `${current.level || 0} / ${current.max_concurrency || 0}` : '—'} detail={`Active workers ${current?.active_workers || 0}`} />
          <MetricCard icon={<Cpu size={15} />} label="CPU" value={current?.latest_resources ? `${liveCpu.toFixed(1)}%` : '—'} detail="系統整體使用率" />
          <MetricCard icon={<MemoryStick size={15} />} label="記憶體" value={current?.latest_resources ? `${liveMemory.toFixed(1)}%` : '—'} detail={current?.latest_resources ? `可用 ${formatBytes(current.latest_resources.memory_available_bytes)}` : undefined} />
          <MetricCard icon={<Activity size={15} />} label="API Health" value={current?.latest_health ? (current.latest_health.ok ? '正常' : '失敗') : '—'} detail={current?.latest_health ? `${Number(current.latest_health.latency_seconds || 0).toFixed(3)} 秒` : undefined} />
          <MetricCard icon={<Database size={15} />} label="目前容量設定" value={`${concurrency.analysis_max_concurrency} / ${concurrency.annotation_max_concurrency}`} detail={`分析 / 標註，硬上限 ${concurrency.hard_limit}`} />
        </div>

        <section style={{ ...panel, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div><div style={{ ...muted, marginBottom: 7 }}>CPU 時序</div><Sparkline samples={samples} field="cpu_percent" color="#60a5fa" /></div>
          <div><div style={{ ...muted, marginBottom: 7 }}>記憶體時序</div><Sparkline samples={samples} field="memory_percent" color="#34d399" /></div>
        </section>

        <section style={panel}>
          <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>即時執行紀錄</h3>
          <div style={{ maxHeight: 210, overflowY: 'auto', background: '#101010', border: '1px solid #292929', borderRadius: 4, padding: 10, fontFamily: 'Consolas, monospace', fontSize: 11, lineHeight: 1.65 }}>
            {(current?.events || []).map((event, index) => <div key={`${event.at}-${index}`} style={{ color: event.event.includes('FAIL') || event.event.includes('ABORT') || event.event.includes('EXIT') ? '#fca5a5' : '#d4d4d4' }}>
              <span style={{ color: '#737373' }}>{new Date(event.at).toLocaleTimeString('zh-TW', { timeZone: TAIPEI_TIME_ZONE, hour12: false })}</span>{' '}<span style={{ color: '#60a5fa' }}>[{event.event}]</span>{' '}{event.message}
            </div>)}
            {!current?.events?.length && <div style={muted}>{current?.message || '尚無執行紀錄。'}</div>}
          </div>
        </section>

        {recommendation && <section style={{ ...panel, borderColor: '#14532d' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8, fontSize: 16 }}><CheckCircle2 size={18} color="#34d399" />容量建議</h3>
              <div style={{ color: recommendation.capacity_boundary_found ? '#34d399' : '#fbbf24', fontSize: 12, marginTop: 8 }}>{recommendation.capacity_boundary_found ? '已找到第一個失敗階層' : `尚未找到極限，目前只能確認至少 ${recommendation.verified_active_heavy_user_ceiling} 人`}</div>
              <div style={{ marginTop: 12, display: 'flex', gap: 22 }}>
                {recommendation.recommended_analysis_concurrency !== undefined
                  ? <>
                    <div><div style={muted}>建議分析容量</div><strong style={{ fontSize: 28, color: '#60a5fa' }}>{recommendation.recommended_analysis_concurrency}</strong></div>
                    <div><div style={muted}>建議標註容量</div><strong style={{ fontSize: 28, color: '#34d399' }}>{recommendation.recommended_annotation_concurrency}</strong></div>
                    <div><div style={muted}>混合負載驗證</div><strong style={{ fontSize: 18, color: recommendation.mixed_verified ? '#34d399' : '#fbbf24' }}>{recommendation.mixed_verified ? 'PASS' : '未通過'}</strong></div>
                  </>
                  : <>
                    <div><div style={muted}>已驗證高強度上限</div><strong style={{ fontSize: 28 }}>{recommendation.verified_active_heavy_user_ceiling}</strong></div>
                    <div><div style={muted}>建議同時高強度人數</div><strong style={{ fontSize: 28, color: '#34d399' }}>{recommendation.recommended_active_heavy_users}</strong></div>
                  </>}
              </div>
              <p style={{ ...muted, maxWidth: 700 }}>{recommendation.note}</p>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {current?.report_json_url && <a href={current.report_json_url} target="_blank" rel="noreferrer" style={{ ...buttonStyle, textDecoration: 'none', background: '#262626' }}><Download size={15} />JSON</a>}
              {current?.report_markdown_url && <a href={current.report_markdown_url} target="_blank" rel="noreferrer" style={{ ...buttonStyle, textDecoration: 'none', background: '#262626' }}><Download size={15} />Markdown</a>}
              <button onClick={applyRecommendation} disabled={busy || current?.status !== 'completed' || Number(recommendation.recommended_annotation_concurrency ?? recommendation.recommended_active_heavy_users ?? 0) < 1 || Number(recommendation.recommended_annotation_concurrency ?? recommendation.recommended_active_heavy_users ?? 0) > concurrency.hard_limit || Number(recommendation.recommended_analysis_concurrency || 1) > concurrency.hard_limit} title={(Number(recommendation.recommended_annotation_concurrency ?? recommendation.recommended_active_heavy_users ?? 0) > concurrency.hard_limit || Number(recommendation.recommended_analysis_concurrency || 1) > concurrency.hard_limit) ? `建議值超過目前正式服務硬上限 ${concurrency.hard_limit}` : undefined} style={{ ...buttonStyle, background: '#166534', borderColor: '#15803d' }}><Gauge size={15} />套用建議</button>
              <button onClick={restoreSettings} disabled={busy} style={{ ...buttonStyle, background: '#262626' }}><RotateCcw size={15} />還原測試前設定</button>
            </div>
            {(Number(recommendation.recommended_annotation_concurrency ?? recommendation.recommended_active_heavy_users ?? 0) > concurrency.hard_limit || Number(recommendation.recommended_analysis_concurrency || 0) > concurrency.hard_limit) && <div style={{ color: '#fbbf24', fontSize: 12, maxWidth: 310 }}>建議值超過正式服務硬上限 {concurrency.hard_limit}，需要調整部署容量並重啟服務，因此不允許直接套用。</div>}
          </div>
        </section>}

        {report && <section style={panel}>
          <h3 style={{ margin: '0 0 14px', fontSize: 16 }}>格式化壓力測試報告</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginBottom: 16 }}>
            <ReportField label="報告編號" value={String(report.run_id || current?.run_id || '—')} />
            <ReportField label="測試狀態" value={String(report.status || current?.status || '—').toUpperCase()} />
            <ReportField label="測試模式" value={report.test_mode === 'auto_dual_capacity' ? '分析／標註自動校準' : '標註容量階梯'} />
            <ReportField label="模型" value={String(report.fixture?.name || current?.fixture_name || '—')} />
            <ReportField label="繪圖引擎" value={String(report.engine || current?.engine || '—')} />
            <ReportField label="候選特徵" value={`${Number(report.candidate_rule_count || 0)} 項`} />
            <ReportField label="產生時間（台北）" value={report.generated_at ? localDateTime(report.generated_at) : '—'} />
            <ReportField label="測試輪次" value={`${reportResults.length} 輪`} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 16 }}>
            <ReportField label="CPU 最高峰值" value={`${reportCpuPeak.toFixed(1)}%`} warn={reportCpuPeak >= 85} />
            <ReportField label="記憶體最高峰值" value={`${reportMemoryPeak.toFixed(1)}%`} warn={reportMemoryPeak >= 90} />
            <ReportField label="最低 API 可用率" value={`${reportMinAvailability.toFixed(1)}%`} warn={reportMinAvailability < 99.5} />
            <ReportField label="Worker 失敗" value={`${reportFailures.length} 個`} warn={reportFailures.length > 0} />
          </div>
          <div style={{ border: '1px solid #1e3a5f', background: '#101923', padding: 13, marginBottom: 16 }}>
            <strong style={{ color: '#bfdbfe', fontSize: 13 }}>容量數字的正確解讀</strong>
            <p style={{ ...muted, margin: '8px 0 10px', lineHeight: 1.6 }}>
              這份報告測量的是「這台主機、這個 STEP 模型、這個引擎」可同時執行的真實 CAD Worker，不是登入網站的總人數。瀏覽、查詢結果與閒置使用者不會占用這個容量；提交模型分析或標註時才會進入對應工作池。
            </p>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
              <ReportField label="本機建議同時重型 CAD 使用者" value={`${Number(capacityPlanning?.recommended_simultaneous_heavy_users || capacityRows.reduce((sum: number, row: any) => sum + row.slots, 0))} 人`} />
              <ReportField label="個別工作池已驗證上限合計" value={`${Number(capacityPlanning?.verified_individual_pool_ceiling_total || recommendation?.verified_active_heavy_user_ceiling || 0)} 人`} warn />
            </div>
            {capacityRows.map((row: any) => <div key={row.workload} style={{ marginTop: 10 }}>
              <div style={{ color: '#e5e5e5', fontSize: 12, marginBottom: 6 }}><strong>{row.label}</strong>：建議同時 {row.slots} 個重型作業{row.verifiedCeiling ? `，個別驗證上限 ${row.verifiedCeiling}` : ''}，實測單件平均 {row.averageSeconds ? formatDuration(row.averageSeconds) : '無法估計'}。</div>
              <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead><tr>{['同時送件', '立即執行', '初始排隊', '平均等待', 'P95 等待', '最後一人開始', '整批清空'].map(label => <th key={label} style={thStyle}>{label}</th>)}</tr></thead>
                <tbody>{row.scenarios.map((scenario: any) => <tr key={scenario.submitted_users}>
                  <td style={tdStyle}>{scenario.submitted_users} 人</td><td style={tdStyle}>{scenario.active_immediately}</td><td style={tdStyle}>{scenario.queued_initially}</td>
                  <td style={tdStyle}>{formatDuration(scenario.average_wait_seconds)}</td><td style={tdStyle}>{formatDuration(scenario.p95_wait_seconds)}</td>
                  <td style={tdStyle}>{formatDuration(scenario.last_user_starts_after_seconds)}</td><td style={tdStyle}>{formatDuration(scenario.estimated_queue_drain_seconds)}</td>
                </tr>)}</tbody>
              </table></div>
            </div>)}
            <div style={{ ...muted, marginTop: 10 }}>排隊時間是以本次同模型輪次耗時估算；不同模型複雜度、網路與其他主機負載會改變實際等待時間。</div>
          </div>
          <CapacityCharts assessments={assessments} />
          {reportResults.length > 0 && <div style={{ overflowX: 'auto' }}>
            <h4 style={{ margin: '0 0 9px', fontSize: 13 }}>每輪執行明細</h4>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead><tr>{['工作池', '階層', '輪次', '分析 / 標註', '成功 / 失敗', '耗時', '吞吐量', 'CPU', '記憶體', 'API'].map(label => <th key={label} style={thStyle}>{label}</th>)}</tr></thead>
              <tbody>{reportResults.map((item, index) => <tr key={`${item.workload}-${item.level}-${item.round}-${index}`}>
                <td style={tdStyle}>{item.workload === 'analysis' ? '分析' : item.workload === 'mixed' ? '混合' : '標註'}</td>
                <td style={tdStyle}>{item.level}</td><td style={tdStyle}>{item.round}</td>
                <td style={tdStyle}>{item.analysis_workers || 0} / {item.annotation_workers || 0}</td>
                <td style={{ ...tdStyle, color: item.failures ? '#f87171' : '#34d399' }}>{item.successes} / {item.failures}</td>
                <td style={tdStyle}>{Number(item.wall_seconds || 0).toFixed(2)}s</td>
                <td style={tdStyle}>{Number(item.throughput_jobs_per_second || 0).toFixed(3)} jobs/s</td>
                <td style={{ ...tdStyle, color: Number(item.resources?.cpu_peak_percent || 0) >= 85 ? '#fbbf24' : '#d4d4d4' }}>{Number(item.resources?.cpu_peak_percent || 0).toFixed(1)}%</td>
                <td style={tdStyle}>{Number(item.resources?.memory_peak_percent || 0).toFixed(1)}%</td>
                <td style={tdStyle}>{Number(item.health?.availability_percent || 0).toFixed(1)}%</td>
              </tr>)}</tbody>
            </table>
          </div>}
          {reportFailures.length > 0 && <div style={{ marginTop: 14, border: '1px solid #7f1d1d', background: '#211313', padding: 12 }}>
            <strong style={{ color: '#fca5a5' }}>Worker 失敗明細</strong>
            {reportFailures.map((worker: any, index: number) => <div key={index} style={{ color: '#fecaca', fontSize: 12, marginTop: 7 }}>{worker.workload} Worker {worker.worker}: {worker.error_type || worker.status} — {worker.error || '沒有錯誤內容'}</div>)}
          </div>}
        </section>}

        <section style={{ ...panel, overflowX: 'auto' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>容量階梯結果</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead><tr>{['工作池', '人數', '結果', '吞吐量', 'CPU 峰值', '記憶體峰值', 'API 可用率', 'Health p95', '原因'].map(label => <th key={label} style={thStyle}>{label}</th>)}</tr></thead>
            <tbody>{assessments.map(item => <tr key={`${item.workload || 'annotation'}-${item.level}`}>
              <td style={tdStyle}>{item.workload === 'analysis' ? '分析' : item.workload === 'mixed' ? '混合' : '標註'}</td>
              <td style={tdStyle}>{item.level}</td>
              <td style={{ ...tdStyle, color: item.passed ? '#34d399' : '#f87171', fontWeight: 700 }}>{item.passed ? 'PASS' : 'FAIL'}</td>
              <td style={tdStyle}>{Number(item.average_throughput_jobs_per_second || 0).toFixed(3)} jobs/s</td>
              <td style={tdStyle}>{Number(item.maximum_cpu_percent || 0).toFixed(1)}%</td>
              <td style={tdStyle}>{Number(item.maximum_memory_percent || 0).toFixed(1)}%</td>
              <td style={tdStyle}>{Number(item.minimum_api_availability_percent || 0).toFixed(1)}%</td>
              <td style={tdStyle}>{Number(item.maximum_health_p95_seconds || 0).toFixed(3)}s</td>
              <td style={tdStyle}>{(item.reasons || []).join(', ') || '—'}</td>
            </tr>)}</tbody>
          </table>
          {!assessments.length && <div style={{ ...muted, padding: '18px 0' }}>{running ? `正在執行第 ${current?.level || 0} 階層，第 ${current?.round || 0} 輪…` : '選擇測試紀錄或開始新的容量測試。'}</div>}
        </section>
      </div>
    </main>
  </div>;
}

const centerStyle: CSSProperties = { minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#171717', color: '#eee', textAlign: 'center' };
const labelStyle: CSSProperties = { display: 'block', color: '#d4d4d4', fontSize: 12, marginBottom: 12 };
const thStyle: CSSProperties = { textAlign: 'left', color: '#a3a3a3', padding: '8px 9px', borderBottom: '1px solid #333', whiteSpace: 'nowrap' };
const tdStyle: CSSProperties = { padding: '9px', borderBottom: '1px solid #262626', color: '#d4d4d4', whiteSpace: 'nowrap' };
