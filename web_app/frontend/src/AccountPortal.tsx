import { useEffect, useState, type CSSProperties, type FormEvent } from 'react';
import axios from 'axios';
import {
  ArchiveRestore, FileBox, History, KeyRound, LockKeyhole, LogIn,
  LogOut, RotateCcw, Save, Settings, ShieldCheck, Trash2, UserRound, X,
} from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export type AuthUser = {
  id: string;
  username: string;
  display_name: string;
  email?: string | null;
  role: 'ADMIN' | 'ENGINEER';
  is_active: boolean;
  must_change_password: boolean;
};

export const fieldStyle: CSSProperties = {
  width: '100%', boxSizing: 'border-box', background: '#171717', color: '#f5f5f5',
  border: '1px solid #3a3a3a', borderRadius: 4, padding: '10px 12px', outline: 'none',
};

export const buttonStyle: CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 7,
  border: '1px solid #2563eb', borderRadius: 4, padding: '9px 14px',
  background: '#2563eb', color: '#fff', fontWeight: 600, cursor: 'pointer',
};

export function apiErrorMessage(reason: any, fallback: string): string {
  const detail = reason?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(item => item?.msg || String(item)).join('；');
  return fallback;
}

function cookieValue(name: string): string {
  const prefix = `${name}=`;
  const item = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : '';
}

export function LoginPage({ onLogin }: { onLogin: (user: AuthUser) => void }) {
  const [emailLocal, setEmailLocal] = useState('admin');
  const [domains, setDomains] = useState<string[]>([]);
  const [domain, setDomain] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    axios.get(`${API_BASE}/api/auth/config`).then(response => {
      const options = response.data.company_email_domains || [];
      setDomains(options);
      const remembered = cookieValue('cad_email_domain');
      setDomain(options.includes(remembered) ? remembered : (options[0] || ''));
    }).catch(() => setError('無法讀取公司登入設定'));
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const response = await axios.post(`${API_BASE}/api/auth/login`, {
        email_local: emailLocal.trim(), email_domain: domain, password,
      }, { withCredentials: true });
      document.cookie = `cad_email_domain=${encodeURIComponent(domain)}; Max-Age=31536000; Path=/; SameSite=Strict`;
      onLogin(response.data.user);
    } catch (reason: any) {
      setError(apiErrorMessage(reason, '登入失敗，請確認公司 Email 與密碼'));
    } finally {
      setLoading(false);
    }
  };

  return <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#fff', display: 'grid', placeItems: 'center', padding: 24 }}>
    <form onSubmit={submit} style={{ width: 430, maxWidth: '100%', background: '#171717', border: '1px solid #303030', borderRadius: 8, padding: 28 }}>
      <div style={{ width: 42, height: 42, display: 'grid', placeItems: 'center', background: '#2563eb', borderRadius: 5, marginBottom: 18 }}><LockKeyhole size={22} /></div>
      <h1 style={{ fontSize: 22, margin: '0 0 6px' }}>工程師帳號登入</h1>
      <p style={{ color: '#999', fontSize: 13, lineHeight: 1.6, margin: '0 0 22px' }}>輸入公司 Email 的帳號部分，再選擇所屬公司域名。</p>
      <label style={{ display: 'block', fontSize: 12, color: '#aaa', marginBottom: 7 }}>公司 Email</label>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 8 }}>
        <input required value={emailLocal} onChange={event => setEmailLocal(event.target.value.replace(/@.*$/, ''))} style={fieldStyle} autoComplete="username" aria-label="Email 帳號部分" />
        <select required value={domain} onChange={event => setDomain(event.target.value)} style={{ ...fieldStyle, width: 'auto', minWidth: 170 }} aria-label="公司 Email 域名">
          {domains.map(item => <option key={item} value={item}>@{item}</option>)}
        </select>
      </div>
      <label style={{ display: 'block', fontSize: 12, color: '#aaa', margin: '16px 0 7px' }}>密碼</label>
      <input required value={password} onChange={event => setPassword(event.target.value)} style={fieldStyle} type="password" autoComplete="current-password" />
      <p style={{ color: '#737373', fontSize: 12, lineHeight: 1.6 }}>忘記密碼時請聯絡系統管理員重設；系統不提供自行重設入口。</p>
      {error && <div role="alert" style={{ marginTop: 14, padding: 10, background: '#2a1717', border: '1px solid #7f1d1d', color: '#fca5a5', fontSize: 13 }}>{error}</div>}
      <button disabled={loading || !domain} style={{ ...buttonStyle, width: '100%', marginTop: 12, opacity: loading ? .6 : 1 }}><LogIn size={16} />{loading ? '登入中' : '登入'}</button>
    </form>
  </div>;
}

type Preferences = {
  recommendation_mode: 'BALANCED' | 'PERSONAL_FIRST' | 'COMPANY_ONLY';
  personal_case_weight: number;
  personal_case_min_similarity: number;
  default_tolerance: Record<string, any>;
  dimension_placement: Record<string, any>;
  annotation_style: Record<string, any>;
  ui_settings: Record<string, any>;
};

export function AccountDialog({ user, onClose, onLogout }: { user: AuthUser; onClose: () => void; onLogout: () => void }) {
  const requiresPasswordChange = user.role !== 'ADMIN' && user.must_change_password;
  const [tab, setTab] = useState<'preferences' | 'cases' | 'artifacts' | 'trash' | 'security'>(requiresPasswordChange ? 'security' : 'preferences');
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [personalCases, setPersonalCases] = useState<any[]>([]);
  const [trash, setTrash] = useState<any[]>([]);
  const [retentionDays, setRetentionDays] = useState(30);
  const [message, setMessage] = useState('');
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' });

  const load = async () => {
    const [preferenceResponse, artifactResponse, caseResponse, trashResponse] = await Promise.all([
      axios.get(`${API_BASE}/api/engineer/preferences`),
      axios.get(`${API_BASE}/api/engineer/artifacts`),
      axios.get(`${API_BASE}/api/engineer/tolerance-cases`),
      axios.get(`${API_BASE}/api/engineer/trash`),
    ]);
    setPreferences(preferenceResponse.data.preferences);
    setArtifacts(artifactResponse.data.artifacts || []);
    setPersonalCases(caseResponse.data.cases || []);
    setTrash(trashResponse.data.records || []);
    setRetentionDays(trashResponse.data.retention_days || 30);
  };

  useEffect(() => {
    if (!requiresPasswordChange) load().catch(reason => setMessage(apiErrorMessage(reason, '資料載入失敗')));
  }, []);

  const savePreferences = async () => {
    if (!preferences) return;
    const response = await axios.put(`${API_BASE}/api/engineer/preferences`, preferences);
    setPreferences(response.data.preferences);
    setMessage('個人偏好已儲存');
  };
  const resetPreferences = async () => {
    if (!window.confirm('確定重設所有個人推薦與尺寸位置偏好？')) return;
    const response = await axios.delete(`${API_BASE}/api/engineer/preferences`);
    setPreferences(response.data.preferences);
    setMessage('個人偏好已重設');
  };
  const deleteCase = async (id: string) => {
    if (!window.confirm('移至回收桶？保留期限內仍可復原。')) return;
    await axios.delete(`${API_BASE}/api/engineer/tolerance-cases/${id}`); await load();
  };
  const deleteArtifact = async (id: string) => {
    if (!window.confirm('將成品與其學習案例移至回收桶？')) return;
    await axios.delete(`${API_BASE}/api/engineer/artifacts/${id}`); await load();
  };
  const restore = async (id: string) => {
    await axios.post(`${API_BASE}/api/engineer/trash/${id}/restore`); await load();
    setMessage('資料已復原');
  };
  const changePassword = async () => {
    try {
      await axios.put(`${API_BASE}/api/auth/password`, passwords);
      onLogout();
    } catch (reason) { setMessage(apiErrorMessage(reason, '密碼修改失敗')); }
  };

  const tabs = [
    { id: 'preferences', label: '推薦偏好', icon: Settings },
    { id: 'cases', label: '個人公差案例', icon: History },
    { id: 'artifacts', label: '標註成品', icon: FileBox },
    { id: 'trash', label: '回收桶', icon: ArchiveRestore },
    { id: 'security', label: '登入安全', icon: ShieldCheck },
  ];

  return <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.72)', zIndex: 5000, display: 'grid', placeItems: 'center', padding: 20 }}>
    <div style={{ position: 'relative', width: 1040, maxWidth: '100%', height: 700, maxHeight: '92vh', background: '#171717', border: '1px solid #333', borderRadius: 8, display: 'flex', overflow: 'hidden' }}>
      <aside style={{ width: 240, flexShrink: 0, background: '#111', borderRight: '1px solid #2b2b2b', padding: 18, boxSizing: 'border-box', overflowY: 'auto' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 24 }}><div style={{ width: 36, height: 36, borderRadius: 4, background: '#2563eb', display: 'grid', placeItems: 'center' }}><UserRound size={19} /></div><div style={{ minWidth: 0 }}><div style={{ fontWeight: 650 }}>{user.display_name}</div><div style={{ color: '#777', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis' }}>{user.email}</div></div></div>
        {tabs.map(item => <button key={item.id} onClick={() => setTab(item.id as any)} style={{ width: '100%', ...buttonStyle, justifyContent: 'flex-start', marginBottom: 7, background: tab === item.id ? '#2563eb' : '#202020', borderColor: tab === item.id ? '#2563eb' : '#333', fontWeight: 500 }}><item.icon size={16} />{item.label}</button>)}
        {user.role === 'ADMIN' && <a href="/admin/accounts" style={{ width: '100%', ...buttonStyle, justifyContent: 'flex-start', marginTop: 12, boxSizing: 'border-box', textDecoration: 'none', background: '#262626', borderColor: '#444' }}><ShieldCheck size={16} />系統管理後台</a>}
        <button onClick={onLogout} style={{ width: '100%', ...buttonStyle, justifyContent: 'flex-start', marginTop: 8, background: '#262626', borderColor: '#444' }}><LogOut size={16} />登出</button>
      </aside>
      <main style={{ flex: 1, minWidth: 0, padding: 26, overflowY: 'auto', color: '#e5e5e5' }}>
        {!requiresPasswordChange && <button onClick={onClose} title="關閉" style={{ position: 'absolute', right: 18, top: 18, background: '#292929', border: '1px solid #444', color: '#ddd', width: 34, height: 34, borderRadius: 4, cursor: 'pointer' }}><X size={17} /></button>}
        {message && <div style={{ padding: 10, border: '1px solid #1d4ed8', color: '#bfdbfe', background: '#172554', marginBottom: 18, fontSize: 13 }}>{message}</div>}

        {tab === 'preferences' && preferences && <section><h2 style={{ marginTop: 0 }}>工程師公差推薦偏好</h2><p style={{ color: '#999' }}>設定公司案例與個人歷史的使用方式，以及常用的尺寸位置。</p>
          <label style={{ display: 'block', margin: '22px 0 7px', color: '#aaa', fontSize: 12 }}>推薦模式</label>
          <select style={fieldStyle} value={preferences.recommendation_mode} onChange={event => setPreferences({ ...preferences, recommendation_mode: event.target.value as any })}><option value="BALANCED">平衡：顯示公司與個人證據</option><option value="PERSONAL_FIRST">個人優先：達門檻時採用個人案例</option><option value="COMPANY_ONLY">僅使用公司資料庫</option></select>
          <label style={{ display: 'block', margin: '18px 0 7px', color: '#aaa', fontSize: 12 }}>個人案例最低相似度：{Math.round(preferences.personal_case_min_similarity * 100)}%</label><input type="range" min="0.5" max="0.98" step="0.01" value={preferences.personal_case_min_similarity} onChange={event => setPreferences({ ...preferences, personal_case_min_similarity: Number(event.target.value) })} style={{ width: '100%' }} />
          <label style={{ display: 'block', margin: '18px 0 7px', color: '#aaa', fontSize: 12 }}>個人案例權重：{Math.round(preferences.personal_case_weight * 100)}%</label><input type="range" min="0" max="1" step="0.05" value={preferences.personal_case_weight} onChange={event => setPreferences({ ...preferences, personal_case_weight: Number(event.target.value) })} style={{ width: '100%' }} />
          <h3>尺寸位置偏好</h3><div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}><select style={fieldStyle} value={preferences.dimension_placement?.preferred_view || 'front'} onChange={event => setPreferences({ ...preferences, dimension_placement: { ...preferences.dimension_placement, preferred_view: event.target.value } })}><option value="front">Front</option><option value="top">Top</option><option value="right">Right</option><option value="left">Left</option></select><select style={fieldStyle} value={preferences.dimension_placement?.side || 'BOTTOM'} onChange={event => setPreferences({ ...preferences, dimension_placement: { ...preferences.dimension_placement, side: event.target.value } })}><option>BOTTOM</option><option>TOP</option><option>LEFT</option><option>RIGHT</option></select></div>
          <div style={{ display: 'flex', gap: 10, marginTop: 24 }}><button onClick={savePreferences} style={buttonStyle}><Save size={16} />儲存偏好</button><button onClick={resetPreferences} style={{ ...buttonStyle, background: '#262626', borderColor: '#444' }}><RotateCcw size={16} />重設</button></div>
        </section>}

        {tab === 'cases' && <ListSection title="個人公差案例" empty="目前沒有個人案例">{personalCases.map(item => <RecordRow key={item.id} title={item.inferred_role || item.feature_type} detail={`${item.model_id} / ${item.part_id} / ${item.rule_id}`} tags={item.tag_ids} onDelete={() => deleteCase(item.id)} />)}</ListSection>}
        {tab === 'artifacts' && <ListSection title="標註成品" empty="目前沒有標註成品">{artifacts.map(item => <RecordRow key={item.id} title={item.title} detail={`${item.model_id} / ${item.part_id} · ${new Date(item.created_at).toLocaleString()}`} tags={item.tag_ids} links={item.output_files} onDelete={() => deleteArtifact(item.id)} />)}</ListSection>}
        {tab === 'trash' && <section><h2 style={{ marginTop: 0 }}>回收桶</h2><p style={{ color: '#999' }}>刪除資料保留 {retentionDays} 天。期限內可自行復原，永久刪除由管理員處理。</p>{trash.length === 0 && <Empty text="回收桶目前是空的" />}{trash.map(item => <div key={item.id} style={rowStyle}><div><strong>{item.record_type === 'ANNOTATION_ARTIFACT' ? '標註成品' : '公差案例'}</strong><div style={{ color: '#888', fontSize: 12, marginTop: 6 }}>刪除：{new Date(item.deleted_at).toLocaleString()} · 清除：{new Date(item.purge_after).toLocaleString()}</div></div><button onClick={() => restore(item.id)} style={buttonStyle}><ArchiveRestore size={15} />復原</button></div>)}</section>}
        {tab === 'security' && <section><h2 style={{ marginTop: 0 }}>登入安全</h2>{requiresPasswordChange && <div style={{ padding: 12, background: '#422006', color: '#fde68a', border: '1px solid #92400e', marginBottom: 18 }}>此帳號使用初始或重設密碼，必須先修改密碼。</div>}<p style={{ color: '#999' }}>若忘記現有密碼，請由管理員在帳號管理頁重設。</p><label style={{ display: 'block', margin: '12px 0 7px', color: '#aaa' }}>目前密碼</label><input type="password" style={fieldStyle} value={passwords.current_password} onChange={event => setPasswords({ ...passwords, current_password: event.target.value })} /><label style={{ display: 'block', margin: '16px 0 7px', color: '#aaa' }}>新密碼（至少 10 字元）</label><input type="password" style={fieldStyle} value={passwords.new_password} onChange={event => setPasswords({ ...passwords, new_password: event.target.value })} /><button onClick={changePassword} style={{ ...buttonStyle, marginTop: 18 }}><KeyRound size={16} />修改密碼並重新登入</button></section>}
      </main>
    </div>
  </div>;
}

const rowStyle: CSSProperties = { border: '1px solid #303030', background: '#1e1e1e', padding: 14, marginBottom: 10, display: 'flex', justifyContent: 'space-between', gap: 18, alignItems: 'center' };
function Empty({ text }: { text: string }) { return <div style={{ padding: 30, border: '1px dashed #444', color: '#777', textAlign: 'center' }}>{text}</div>; }
function ListSection({ title, empty, children }: { title: string; empty: string; children: any }) { const count = Array.isArray(children) ? children.length : 0; return <section><h2 style={{ marginTop: 0 }}>{title}</h2>{count === 0 ? <Empty text={empty} /> : children}</section>; }
function RecordRow({ title, detail, tags, links, onDelete }: { title: string; detail: string; tags?: string[]; links?: any; onDelete: () => void }) { return <div style={rowStyle}><div style={{ minWidth: 0 }}><strong>{title}</strong><div style={{ color: '#888', fontSize: 12, marginTop: 6 }}>{detail}</div>{tags?.length ? <div style={{ color: '#60a5fa', fontSize: 11, marginTop: 6 }}>{tags.length} 個推薦標籤</div> : null}<div style={{ display: 'flex', gap: 9, marginTop: 7 }}>{links?.pdf_url && <a href={links.pdf_url} target="_blank" rel="noreferrer" style={{ color: '#60a5fa' }}>PDF</a>}{links?.dxf_url && <a href={links.dxf_url} style={{ color: '#60a5fa' }}>DXF</a>}</div></div><button title="移至回收桶" onClick={onDelete} style={{ background: '#3f1d1d', color: '#fca5a5', border: '1px solid #7f1d1d', padding: 8, cursor: 'pointer' }}><Trash2 size={16} /></button></div>; }
