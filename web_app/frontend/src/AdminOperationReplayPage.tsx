import { Suspense, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Canvas, useLoader } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import * as THREE from 'three';
import {
  AlertCircle, ArrowLeft, Box, CheckCircle2, FileText,
  Loader2, Play, RefreshCw, Search, Settings2,
} from 'lucide-react';
import { LoginPage, apiErrorMessage, buttonStyle, fieldStyle, type AuthUser } from './AccountPortal';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

function selectedAnchor(record: any): number[] | null {
  const geometry = record?.geometry_payload || {};
  for (const value of [record?.center, record?.center_3d, geometry.center_3d, geometry.center]) {
    if (Array.isArray(value) && value.length >= 3 && value.slice(0, 3).every(Number.isFinite)) return value.slice(0, 3);
  }
  const bbox = record?.bbox_3d || geometry.bbox_3d;
  if (Array.isArray(bbox) && bbox.length >= 6 && bbox.slice(0, 6).every(Number.isFinite)) {
    return [(bbox[0] + bbox[3]) / 2, (bbox[1] + bbox[4]) / 2, (bbox[2] + bbox[5]) / 2];
  }
  return null;
}

function PartPreview({ url, selected }: { url: string; selected: any }) {
  const source = useLoader(STLLoader, url);
  const prepared = useMemo(() => {
    const geometry = source.clone();
    geometry.computeBoundingBox();
    const bounds = geometry.boundingBox!;
    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    bounds.getCenter(center);
    bounds.getSize(size);
    const scale = 70 / Math.max(size.x, size.y, size.z, 1);
    geometry.translate(-center.x, -center.y, -center.z);
    geometry.computeVertexNormals();
    return { geometry, center, scale };
  }, [source]);
  const anchor = selectedAnchor(selected);
  const marker = anchor
    ? new THREE.Vector3(anchor[0], anchor[1], anchor[2]).sub(prepared.center).multiplyScalar(prepared.scale)
    : null;
  return <group>
    <mesh geometry={prepared.geometry} scale={prepared.scale}>
      <meshStandardMaterial color="#94a3b8" metalness={0.18} roughness={0.62} />
    </mesh>
    {marker && <mesh position={marker}><sphereGeometry args={[2.1, 20, 20]} /><meshStandardMaterial color="#f59e0b" emissive="#78350f" /></mesh>}
  </group>;
}

function ModelPanel({ url, selected }: { url?: string | null; selected: any }) {
  return <div style={{ height: 390, background: '#0a0a0a', border: '1px solid #303030', position: 'relative' }}>
    {!url ? <div style={centerStyle}>此紀錄沒有可用的 STL 預覽</div> : <Canvas camera={{ position: [85, 65, 85], fov: 38 }}>
      <ambientLight intensity={0.9} />
      <directionalLight position={[60, 90, 40]} intensity={1.35} />
      <directionalLight position={[-45, -20, -40]} intensity={0.45} />
      <Suspense fallback={null}><PartPreview url={`${API_BASE}${url}`} selected={selected} /></Suspense>
      <gridHelper args={[120, 20, '#334155', '#1f2937']} position={[0, -38, 0]} />
      <OrbitControls makeDefault />
    </Canvas>}
    <div style={{ position: 'absolute', left: 12, bottom: 10, color: '#737373', fontSize: 11 }}>拖曳旋轉 · 滾輪縮放 · 有 3D 錨點時以橙色標記</div>
  </div>;
}

export default function AdminOperationReplayPage() {
  const operationId = decodeURIComponent(window.location.pathname.split('/').filter(Boolean).pop() || '');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [enabledOnly, setEnabledOnly] = useState(true);
  const [selected, setSelected] = useState<any>(null);
  const [job, setJob] = useState<any>(null);
  const [startingReplay, setStartingReplay] = useState(false);

  const load = async () => {
    setError('');
    try {
      const response = await axios.get(`${API_BASE}/api/admin/operation-logs/${encodeURIComponent(operationId)}/reproduction`);
      const reproduction = response.data.reproduction;
      setData(reproduction);
      const records = reproduction?.request?.feature_records || [];
      setSelected((current: any) => current || records.find((record: any) => record.enabled) || records[0] || null);
    } catch (reason) { setError(apiErrorMessage(reason, '重現資料載入失敗')); }
  };

  useEffect(() => {
    axios.get(`${API_BASE}/api/auth/me`).then(response => {
      setUser(response.data.user);
      if (response.data.user.role === 'ADMIN') return load();
    }).catch(() => setUser(null)).finally(() => setChecking(false));
  }, [operationId]);

  useEffect(() => {
    if (!job?.job_id || !['queued', 'processing', 'finalizing'].includes(job.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await axios.get(`${API_BASE}/api/annotation/jobs/${job.job_id}`);
        setJob(response.data);
      } catch (reason) {
        setError(apiErrorMessage(reason, '重跑狀態讀取失敗'));
        window.clearInterval(timer);
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [job?.job_id, job?.status]);

  const records = data?.request?.feature_records || [];
  const filteredRecords = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return records.filter((record: any) => {
      if (enabledOnly && !record.enabled) return false;
      if (!normalized) return true;
      return [record.rule_id, record.id, record.name, record.category, record.semantic, record.feature_family]
        .some(value => String(value || '').toLowerCase().includes(normalized));
    });
  }, [records, query, enabledOnly]);

  const startReplay = async () => {
    if (!window.confirm('將使用目前程式版本與保存的完整輸入建立隔離重跑，不會覆蓋原輸出，也不會寫入工程師案例庫。確定繼續？')) return;
    setStartingReplay(true);
    setError('');
    try {
      const response = await axios.post(`${API_BASE}/api/admin/operation-logs/${encodeURIComponent(operationId)}/replay`);
      setJob(response.data);
    } catch (reason) { setError(apiErrorMessage(reason, '建立重跑工作失敗')); }
    finally { setStartingReplay(false); }
  };

  if (checking) return <div style={centerStyle}>正在驗證管理員權限</div>;
  if (!user) return <LoginPage onLogin={loggedIn => { setUser(loggedIn); if (loggedIn.role === 'ADMIN') load(); }} />;
  if (user.role !== 'ADMIN') return <div style={centerStyle}>此頁面僅限管理員使用</div>;

  const runtime = data?.runtime || {};
  const model = data?.model || {};
  const replayTerminal = job && ['completed', 'error'].includes(job.status);
  return <div style={{ minHeight: '100%', background: '#101010', color: '#e5e5e5' }}>
    <header style={{ height: 58, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 22px', background: '#171717', borderBottom: '1px solid #303030' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}><Box size={20} color="#60a5fa" /><strong>標註工作重現檢視</strong><span style={{ color: '#737373', fontSize: 12 }}>{operationId}</span></div>
      <a href="/admin/logs" style={{ ...buttonStyle, background: '#262626', borderColor: '#444', textDecoration: 'none' }}><ArrowLeft size={15} />返回系統紀錄</a>
    </header>
    <main style={{ maxWidth: 1500, margin: '0 auto', padding: 24 }}>
      {error && <div style={{ ...panelStyle, borderColor: '#7f1d1d', color: '#fecaca' }}><AlertCircle size={16} style={{ verticalAlign: -3, marginRight: 7 }} />{error}</div>}
      {!data ? <div style={panelStyle}><Loader2 size={18} className="animate-spin" /> 正在載入重現資料</div> : <>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12, marginBottom: 16 }}>
          <Summary label="原始狀態" value={data.status} tone={data.status === 'error' ? '#fca5a5' : '#86efac'} />
          <Summary label="模型／零件" value={`${model.model_id} / ${model.part_id}`} />
          <Summary label="選取規則" value={`${data.request.enabled_feature_record_count} / ${data.request.feature_record_count}`} />
          <Summary label="引擎" value={data.request.candidate_engine} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.25fr) minmax(390px, .75fr)', gap: 16 }}>
          <div><ModelPanel url={model.preview_url} selected={selected} />
            <div style={{ ...panelStyle, marginTop: 16 }}><h3 style={{ marginTop: 0 }}><Settings2 size={16} style={{ verticalAlign: -3, marginRight: 7 }} />原始輸入與版本指紋</h3>
              <InfoGrid values={[
                ['STEP SHA-256', model.sha256 || '舊紀錄未保存'], ['STEP 大小', model.size_bytes ? `${Math.round(model.size_bytes / 1024)} KB` : '—'],
                ['Git commit', runtime.git_commit || '舊紀錄未保存'], ['Python', runtime.python || '—'],
                ['半剖視圖', data.request.include_half_section ? '啟用' : '停用'], ['Request hash', data.request.request_hash || '—'],
              ]} />
            </div>
            {data.failure && <div style={{ ...panelStyle, borderColor: '#7f1d1d' }}><h3 style={{ marginTop: 0, color: '#fca5a5' }}><AlertCircle size={16} style={{ verticalAlign: -3, marginRight: 7 }} />原始錯誤</h3><strong>{data.failure.error_type}</strong><div style={{ marginTop: 7, color: '#fecaca' }}>{data.failure.full_error || data.failure.public_error}</div><pre style={preStyle}>{data.failure.traceback}</pre></div>}
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={panelStyle}><div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}><div><strong>隔離重跑</strong><div style={subStyle}>使用目前版本；不覆蓋、不學習</div></div><button disabled={!data.replay_available || startingReplay || (job && !replayTerminal)} onClick={startReplay} style={{ ...buttonStyle, opacity: !data.replay_available || startingReplay ? .45 : 1 }}><Play size={15} />{startingReplay ? '建立中' : '建立重現副本'}</button></div>
              {!data.replay_available && <div style={{ color: '#fca5a5', fontSize: 12, marginTop: 10 }}>完整輸入或原始模型已不存在，僅能檢視紀錄。</div>}
              {job && <div style={{ marginTop: 14, borderTop: '1px solid #333', paddingTop: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', border: '1px solid #333', marginBottom: 10 }}>
                  <div style={{ padding: 9, borderRight: '1px solid #333' }}><div style={subStyle}>原始工作</div><strong style={{ color: data.status === 'error' ? '#fca5a5' : '#86efac' }}>{data.status}</strong></div>
                  <div style={{ padding: 9 }}><div style={subStyle}>目前版本重跑</div><strong style={{ color: job.status === 'error' ? '#fca5a5' : job.status === 'completed' ? '#86efac' : '#e5e5e5' }}>{job.status}</strong></div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>{['queued','processing','finalizing'].includes(job.status) ? <Loader2 size={15} className="animate-spin" /> : job.status === 'completed' ? <CheckCircle2 size={15} color="#86efac" /> : <AlertCircle size={15} color="#fca5a5" />}<strong>{job.message || job.error || job.status}</strong></div>
                {replayTerminal && job.job_id && <a href={`/admin/operations/${job.result?.operation_id || job.job_id}`} style={{ ...buttonStyle, display: 'inline-flex', marginTop: 10, textDecoration: 'none' }}><RefreshCw size={14} />開啟重跑結果</a>}
              </div>}
            </div>
            <div style={{ ...panelStyle, padding: 12 }}><div style={{ position: 'relative' }}><Search size={15} style={{ position: 'absolute', left: 11, top: 11, color: '#737373' }} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋規則、語意或特徵" style={{ ...fieldStyle, paddingLeft: 34 }} /></div><label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10, color: '#a3a3a3', fontSize: 12 }}><input type="checkbox" checked={enabledOnly} onChange={event => setEnabledOnly(event.target.checked)} />只顯示當時啟用的規則</label></div>
            <div style={{ maxHeight: 390, overflowY: 'auto', paddingRight: 6 }}>{filteredRecords.map((record: any, index: number) => <button key={record.rule_id || record.id || index} onClick={() => setSelected(record)} style={{ ...rowButtonStyle, borderColor: selected === record ? '#2563eb' : '#303030' }}><div><strong>{record.name || record.rule_id || record.id}</strong><div style={subStyle}>{record.category || record.feature_family || '未分類'} · {record.preferred_view || '未指定視圖'}</div></div><span style={{ color: record.enabled ? '#86efac' : '#737373', fontSize: 11 }}>{record.enabled ? '啟用' : '停用'}</span></button>)}</div>
            {selected && <div style={{ ...panelStyle, marginTop: 12 }}><h3 style={{ marginTop: 0, fontSize: 15 }}><FileText size={15} style={{ verticalAlign: -3, marginRight: 6 }} />選取規則完整參數</h3><pre style={{ ...preStyle, maxHeight: 330 }}>{JSON.stringify(selected, null, 2)}</pre></div>}
          </div>
        </div>
      </>}
    </main>
  </div>;
}

function Summary({ label, value, tone = '#e5e5e5' }: { label: string; value: string; tone?: string }) { return <div style={panelStyle}><div style={{ color: '#737373', fontSize: 11, marginBottom: 7 }}>{label}</div><strong style={{ color: tone, overflowWrap: 'anywhere' }}>{value}</strong></div>; }
function InfoGrid({ values }: { values: string[][] }) { return <dl style={{ display: 'grid', gridTemplateColumns: '140px minmax(0, 1fr)', gap: '9px 12px', margin: 0 }}>{values.map(([label, value]) => <><dt key={`${label}-term`} style={{ color: '#737373', fontSize: 12 }}>{label}</dt><dd key={`${label}-value`} style={{ margin: 0, color: '#d4d4d4', fontSize: 12, overflowWrap: 'anywhere' }}>{value}</dd></>)}</dl>; }

const centerStyle = { minHeight: '100vh', background: '#0a0a0a', color: '#ddd', display: 'grid', placeItems: 'center' } as const;
const panelStyle = { border: '1px solid #333', background: '#171717', padding: 16, marginBottom: 0 } as const;
const subStyle = { color: '#888', fontSize: 12, marginTop: 5 } as const;
const preStyle = { padding: 11, overflow: 'auto', background: '#0a0a0a', border: '1px solid #2b2b2b', color: '#a3a3a3', fontSize: 11, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } as const;
const rowButtonStyle = { width: '100%', border: '1px solid #303030', background: '#171717', color: '#e5e5e5', padding: 12, marginBottom: 7, display: 'flex', justifyContent: 'space-between', gap: 12, textAlign: 'left', cursor: 'pointer' } as const;
