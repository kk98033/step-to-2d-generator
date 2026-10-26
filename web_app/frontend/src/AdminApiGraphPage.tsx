import {
  useEffect, useMemo, useRef, useState,
  type CSSProperties, type PointerEvent as ReactPointerEvent, type WheelEvent,
} from 'react';
import axios from 'axios';
import {
  ArrowLeft, Boxes, ChevronRight, Maximize2, RefreshCw, Search, ShieldCheck,
} from 'lucide-react';
import { LoginPage, apiErrorMessage, buttonStyle, fieldStyle, type AuthUser } from './AccountPortal';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const NODE_WIDTH = 250;
const NODE_HEIGHT = 48;

type SourceRef = { file: string; line: number; symbol: string };
type ApiRoute = { id: string; method: string; path: string; handler: string; source: SourceRef };
type FrontendCall = { method: string; path: string; source: SourceRef };
type WorkerAction = { action: string; handler: string; source: SourceRef };
type GraphEdge = { from: string; to: string; type: string };
type ApiGraph = {
  summary: Record<string, number>;
  routes: ApiRoute[];
  frontend_calls: FrontendCall[];
  worker_actions: WorkerAction[];
  edges: GraphEdge[];
};
type GraphNode = {
  id: string;
  label: string;
  detail: string;
  kind: 'frontend' | 'api' | 'backend' | 'worker';
  source?: SourceRef;
};
type Point = { x: number; y: number };

const kindConfig = {
  frontend: { label: '前端操作', fill: '#172554', stroke: '#2563eb', text: '#bfdbfe' },
  api: { label: 'API 路由', fill: '#262626', stroke: '#737373', text: '#f5f5f5' },
  backend: { label: '後端函數', fill: '#052e16', stroke: '#16a34a', text: '#bbf7d0' },
  worker: { label: 'CAD Worker', fill: '#422006', stroke: '#d97706', text: '#fde68a' },
};

function routeCategory(path: string) {
  return path.replace(/^\/+/, '').split('/')[1] || 'system';
}

function shortFile(path: string) {
  return path.split('/').pop() || path;
}

function truncate(value: string, length = 36) {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

function buildNodes(graph: ApiGraph): Map<string, GraphNode> {
  const nodes = new Map<string, GraphNode>();
  graph.routes.forEach(route => nodes.set(route.id, {
    id: route.id,
    label: `${route.method} ${route.path}`,
    detail: route.handler,
    kind: 'api',
    source: route.source,
  }));
  graph.frontend_calls.forEach(call => {
    const id = `frontend:${call.source.file}:${call.source.symbol}`;
    nodes.set(id, {
      id,
      label: call.source.symbol,
      detail: shortFile(call.source.file),
      kind: 'frontend',
      source: call.source,
    });
  });
  graph.worker_actions.forEach(action => {
    const id = `worker:${action.action}`;
    nodes.set(id, {
      id,
      label: action.action,
      detail: action.handler,
      kind: 'worker',
      source: action.source,
    });
  });
  graph.edges.forEach(edge => {
    [edge.from, edge.to].forEach(id => {
      if (nodes.has(id)) return;
      const symbol = id.split(':').pop() || id;
      nodes.set(id, {
        id,
        label: symbol,
        detail: id.startsWith('backend:') ? id.split(':').slice(1, -1).join(':') : id,
        kind: id.startsWith('worker:') ? 'worker' : id.startsWith('frontend:') ? 'frontend' : 'backend',
      });
    });
  });
  return nodes;
}

export default function AdminApiGraphPage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [graph, setGraph] = useState<ApiGraph | null>(null);
  const [generatedAt, setGeneratedAt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [category, setCategory] = useState('all');
  const [query, setQuery] = useState('');
  const [showInternal, setShowInternal] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [positions, setPositions] = useState<Record<string, Point>>({});
  const [viewport, setViewport] = useState({ x: 24, y: 24, scale: 0.75 });
  const canvasRef = useRef<SVGSVGElement | null>(null);
  const interaction = useRef<
    | { type: 'pan'; startX: number; startY: number; originX: number; originY: number }
    | { type: 'node'; id: string; offsetX: number; offsetY: number }
    | null
  >(null);

  const loadGraph = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await axios.get(`${API_BASE}/api/admin/system-api-graph`);
      setGraph(response.data.graph);
      setGeneratedAt(response.data.generated_at);
    } catch (reason) {
      setError(apiErrorMessage(reason, 'API 關係圖載入失敗'));
    } finally {
      setLoading(false);
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
    loadGraph();
    const timer = window.setInterval(loadGraph, 60_000);
    return () => window.clearInterval(timer);
  }, [user?.role]);

  const nodeMap = useMemo(() => graph ? buildNodes(graph) : new Map<string, GraphNode>(), [graph]);
  const categories = useMemo(() => graph
    ? Array.from(new Set(graph.routes.map(route => routeCategory(route.path)))).sort()
    : [], [graph]);

  const visible = useMemo(() => {
    if (!graph) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] };
    const routeIds = new Set(
      graph.routes
        .filter(route => category === 'all' || routeCategory(route.path) === category)
        .map(route => route.id),
    );
    const ids = new Set(routeIds);
    graph.edges.forEach(edge => {
      if (routeIds.has(edge.from) || routeIds.has(edge.to)) {
        ids.add(edge.from);
        ids.add(edge.to);
      }
    });
    if (showInternal) {
      let frontier = new Set(ids);
      for (let depth = 0; depth < 4; depth += 1) {
        const next = new Set<string>();
        graph.edges.forEach(edge => {
          if (frontier.has(edge.from) && !ids.has(edge.to)) {
            ids.add(edge.to);
            next.add(edge.to);
          }
        });
        frontier = next;
      }
    }
    const normalizedQuery = query.trim().toLowerCase();
    if (normalizedQuery) {
      const matches = new Set(
        Array.from(ids).filter(id => {
          const node = nodeMap.get(id);
          return node && `${node.label} ${node.detail} ${node.source?.file || ''}`.toLowerCase().includes(normalizedQuery);
        }),
      );
      const context = new Set(matches);
      graph.edges.forEach(edge => {
        if (matches.has(edge.from) || matches.has(edge.to)) {
          context.add(edge.from);
          context.add(edge.to);
        }
      });
      Array.from(ids).forEach(id => { if (!context.has(id)) ids.delete(id); });
    }
    const nodes = Array.from(ids).map(id => nodeMap.get(id)).filter((node): node is GraphNode => Boolean(node));
    const edges = graph.edges.filter(edge => ids.has(edge.from) && ids.has(edge.to));
    return { nodes, edges };
  }, [category, graph, nodeMap, query, showInternal]);

  const visibleSignature = visible.nodes.map(node => node.id).sort().join('|');
  useEffect(() => {
    const columns: GraphNode['kind'][] = ['frontend', 'api', 'backend', 'worker'];
    const next: Record<string, Point> = {};
    let maximumRows = 1;
    columns.forEach((kind, column) => {
      const group = visible.nodes.filter(node => node.kind === kind).sort((a, b) => a.label.localeCompare(b.label));
      maximumRows = Math.max(maximumRows, group.length);
      group.forEach((node, row) => {
        next[node.id] = { x: 40 + column * 330, y: 42 + row * 66 };
      });
    });
    setPositions(current => {
      Object.keys(next).forEach(id => { if (current[id]) next[id] = current[id]; });
      return next;
    });
    const rect = canvasRef.current?.getBoundingClientRect();
    if (rect) {
      const worldWidth = 40 + columns.length * 330;
      const worldHeight = 80 + maximumRows * 66;
      const scale = Math.max(0.08, Math.min(1, (rect.width - 32) / worldWidth, (rect.height - 32) / worldHeight));
      setViewport({ x: 16, y: 16, scale });
    }
  }, [visibleSignature]);

  const selected = selectedId ? nodeMap.get(selectedId) : undefined;
  const selectedEdges = useMemo(() => graph && selectedId
    ? graph.edges.filter(edge => edge.from === selectedId || edge.to === selectedId)
    : [], [graph, selectedId]);

  const toWorld = (clientX: number, clientY: number) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    return {
      x: (clientX - (rect?.left || 0) - viewport.x) / viewport.scale,
      y: (clientY - (rect?.top || 0) - viewport.y) / viewport.scale,
    };
  };

  const startPan = (event: ReactPointerEvent<SVGSVGElement>) => {
    interaction.current = {
      type: 'pan', startX: event.clientX, startY: event.clientY,
      originX: viewport.x, originY: viewport.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const startNodeDrag = (event: ReactPointerEvent<SVGGElement>, id: string) => {
    event.stopPropagation();
    const point = toWorld(event.clientX, event.clientY);
    const current = positions[id] || { x: 0, y: 0 };
    interaction.current = { type: 'node', id, offsetX: point.x - current.x, offsetY: point.y - current.y };
    canvasRef.current?.setPointerCapture(event.pointerId);
    setSelectedId(id);
  };

  const movePointer = (event: ReactPointerEvent<SVGSVGElement>) => {
    const active = interaction.current;
    if (!active) return;
    if (active.type === 'pan') {
      setViewport(current => ({
        ...current,
        x: active.originX + event.clientX - active.startX,
        y: active.originY + event.clientY - active.startY,
      }));
      return;
    }
    const point = toWorld(event.clientX, event.clientY);
    setPositions(current => ({
      ...current,
      [active.id]: { x: point.x - active.offsetX, y: point.y - active.offsetY },
    }));
  };

  const zoom = (event: WheelEvent<SVGSVGElement>) => {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const cursorX = event.clientX - rect.left;
    const cursorY = event.clientY - rect.top;
    const nextScale = Math.max(0.08, Math.min(2.5, viewport.scale * (event.deltaY > 0 ? 0.88 : 1.12)));
    const worldX = (cursorX - viewport.x) / viewport.scale;
    const worldY = (cursorY - viewport.y) / viewport.scale;
    setViewport({
      x: cursorX - worldX * nextScale,
      y: cursorY - worldY * nextScale,
      scale: nextScale,
    });
  };

  if (checking) return <div style={centerStyle}>正在確認管理員權限…</div>;
  if (!user) return <LoginPage onLogin={setUser} />;
  if (user.role !== 'ADMIN') return <div style={centerStyle}><div><h2>權限不足</h2><p>此頁面僅供系統管理員使用。</p><a href="/" style={buttonStyle}>返回主系統</a></div></div>;

  return <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#0a0a0a', color: '#e5e5e5' }}>
    <header style={{ height: 58, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 18px', background: '#171717', borderBottom: '1px solid #303030' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Boxes size={20} color="#60a5fa" />
        <strong>系統 API 關係圖</strong>
        <span style={{ color: '#737373', fontSize: 12 }}>僅限管理員 · 每 60 秒同步</span>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <a href="/admin/accounts" style={{ ...buttonStyle, textDecoration: 'none', background: '#262626', borderColor: '#444' }}><ShieldCheck size={15} />管理介面</a>
        <a href="/" style={{ ...buttonStyle, textDecoration: 'none', background: '#262626', borderColor: '#444' }}><ArrowLeft size={15} />返回主系統</a>
      </div>
    </header>

    <div style={{ padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap', background: '#111', borderBottom: '1px solid #2b2b2b' }}>
      <div style={{ position: 'relative', width: 290 }}>
        <Search size={15} style={{ position: 'absolute', left: 10, top: 10, color: '#737373' }} />
        <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋 API、函數或來源檔案" style={{ ...fieldStyle, width: '100%', paddingLeft: 34 }} />
      </div>
      <select value={category} onChange={event => setCategory(event.target.value)} style={{ ...fieldStyle, width: 180 }}>
        <option value="all">全部 API 分類</option>
        {categories.map(value => <option key={value} value={value}>{value}</option>)}
      </select>
      <label style={{ display: 'flex', alignItems: 'center', gap: 7, color: '#bdbdbd', fontSize: 13 }}>
        <input type="checkbox" checked={showInternal} onChange={event => setShowInternal(event.target.checked)} />顯示內部函數鏈
      </label>
      <button onClick={() => loadGraph()} disabled={loading} style={buttonStyle}><RefreshCw size={15} />{loading ? '同步中' : '立即同步'}</button>
      <button onClick={() => setViewport({ x: 24, y: 24, scale: 0.75 })} style={{ ...buttonStyle, background: '#262626', borderColor: '#444' }}><Maximize2 size={15} />重設視圖</button>
      {graph && <span style={{ marginLeft: 'auto', color: '#888', fontSize: 12 }}>
        {graph.summary.backend_routes} 路由 · {graph.summary.frontend_http_calls} 前端呼叫 · {visible.nodes.length} 顯示節點<br />
        最後同步 {generatedAt ? new Date(generatedAt).toLocaleTimeString() : '—'}
      </span>}
    </div>

    {error && <div style={{ padding: '8px 14px', color: '#fecaca', background: '#450a0a', borderBottom: '1px solid #7f1d1d' }}>{error}</div>}
    <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: selected ? 'minmax(0, 1fr) 350px' : 'minmax(0, 1fr)' }}>
      <div style={{ position: 'relative', minWidth: 0, minHeight: 0 }}>
        {!graph && !loading && <div style={centerStyle}>尚未取得關係資料</div>}
        <svg
          ref={canvasRef}
          width="100%" height="100%"
          onPointerDown={startPan}
          onPointerMove={movePointer}
          onPointerUp={() => { interaction.current = null; }}
          onPointerCancel={() => { interaction.current = null; }}
          onWheel={zoom}
          style={{ display: 'block', background: '#0a0a0a', cursor: interaction.current?.type === 'pan' ? 'grabbing' : 'grab', touchAction: 'none' }}
        >
          <defs>
            <pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="#1f1f1f" strokeWidth="1" /></pattern>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#525252" /></marker>
          </defs>
          <rect width="100%" height="100%" fill="url(#grid)" />
          <g transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.scale})`}>
            {visible.edges.map((edge, index) => {
              const from = positions[edge.from];
              const to = positions[edge.to];
              if (!from || !to) return null;
              const active = selectedId === edge.from || selectedId === edge.to;
              return <line key={`${edge.from}-${edge.to}-${index}`} x1={from.x + NODE_WIDTH} y1={from.y + NODE_HEIGHT / 2} x2={to.x} y2={to.y + NODE_HEIGHT / 2} stroke={active ? '#60a5fa' : '#404040'} strokeWidth={active ? 2.2 : 1.1} markerEnd="url(#arrow)" />;
            })}
            {visible.nodes.map(node => {
              const point = positions[node.id];
              if (!point) return null;
              const style = kindConfig[node.kind];
              const active = selectedId === node.id;
              return <g key={node.id} transform={`translate(${point.x} ${point.y})`} onPointerDown={event => startNodeDrag(event, node.id)} style={{ cursor: 'move' }}>
                <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx="5" fill={style.fill} stroke={active ? '#f5f5f5' : style.stroke} strokeWidth={active ? 2.5 : 1.3} />
                <text x="10" y="20" fill={style.text} fontSize="12" fontWeight="700">{truncate(node.label)}</text>
                <text x="10" y="37" fill="#a3a3a3" fontSize="10">{truncate(node.detail, 42)}</text>
              </g>;
            })}
          </g>
        </svg>
        <div style={{ position: 'absolute', left: 12, bottom: 12, display: 'flex', gap: 12, padding: '8px 10px', background: '#171717', border: '1px solid #333', borderRadius: 4, fontSize: 11 }}>
          {(Object.keys(kindConfig) as GraphNode['kind'][]).map(kind => <span key={kind} style={{ color: kindConfig[kind].text }}>■ {kindConfig[kind].label}</span>)}
          <span style={{ color: '#737373' }}>拖曳節點 · 拖曳空白處平移 · 滾輪縮放</span>
        </div>
      </div>

      {selected && <aside style={{ minHeight: 0, overflowY: 'auto', padding: 18, background: '#171717', borderLeft: '1px solid #303030' }}>
        <div style={{ color: kindConfig[selected.kind].text, fontSize: 12, marginBottom: 8 }}>{kindConfig[selected.kind].label}</div>
        <h3 style={{ margin: '0 0 8px', overflowWrap: 'anywhere' }}>{selected.label}</h3>
        <div style={{ color: '#a3a3a3', fontSize: 13, overflowWrap: 'anywhere' }}>{selected.detail}</div>
        {selected.source && <div style={{ marginTop: 12, padding: 10, background: '#0f0f0f', border: '1px solid #303030', fontSize: 12, color: '#a3a3a3', overflowWrap: 'anywhere' }}>
          {selected.source.file}:{selected.source.line}
        </div>}
        <h4 style={{ margin: '22px 0 10px' }}>直接關係 ({selectedEdges.length})</h4>
        <div style={{ display: 'grid', gap: 8 }}>
          {selectedEdges.map((edge, index) => {
            const outgoing = edge.from === selected.id;
            const related = nodeMap.get(outgoing ? edge.to : edge.from);
            return <button key={`${edge.from}-${edge.to}-${index}`} onClick={() => setSelectedId(related?.id || null)} style={{ textAlign: 'left', padding: 10, background: '#202020', border: '1px solid #383838', borderRadius: 4, color: '#ddd', cursor: 'pointer' }}>
              <div style={{ color: '#737373', fontSize: 10 }}>{outgoing ? '輸出' : '輸入'} · {edge.type}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 4, overflowWrap: 'anywhere' }}>{related?.label || (outgoing ? edge.to : edge.from)}<ChevronRight size={13} /></div>
            </button>;
          })}
        </div>
      </aside>}
    </div>
  </div>;
}

const centerStyle: CSSProperties = {
  minHeight: '100vh', display: 'grid', placeItems: 'center', textAlign: 'center',
  background: '#0a0a0a', color: '#e5e5e5',
};
