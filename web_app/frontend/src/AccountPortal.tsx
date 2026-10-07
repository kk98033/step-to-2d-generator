import { useEffect, useState, type CSSProperties, type FormEvent } from 'react';
import axios from 'axios';
import {
  LockKeyhole, LogIn, LogOut, Settings, ShieldCheck, UserRound,
  Users, FileBox, Save, X, UserPlus, KeyRound, Trash2,
  History, RotateCcw,
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

const fieldStyle: CSSProperties = {
  width: '100%', boxSizing: 'border-box', background: '#171717', color: '#f5f5f5',
  border: '1px solid #3a3a3a', borderRadius: 4, padding: '10px 12px', outline: 'none',
};

const buttonStyle: CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 7,
  border: 0, borderRadius: 4, padding: '9px 14px', background: '#2563eb',
  color: '#fff', fontWeight: 600, cursor: 'pointer',
};

function apiErrorMessage(reason: any, fallback: string): string {
  const detail = reason?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => item?.msg || String(item)).join('；');
  }
  return fallback;
}

export function LoginPage({ onLogin }: { onLogin: (user: AuthUser) => void }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const response = await axios.post(`${API_BASE}/api/auth/login`, { username, password }, { withCredentials: true });
      onLogin(response.data.user);
    } catch (reason: any) {
      setError(reason?.response?.data?.detail || '登入失敗，請確認帳號與密碼。');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#fff', display: 'grid', placeItems: 'center', padding: 24 }}>
      <form onSubmit={submit} style={{ width: 390, maxWidth: '100%', background: '#171717', border: '1px solid #303030', borderRadius: 8, padding: 28 }}>
        <div style={{ width: 42, height: 42, display: 'grid', placeItems: 'center', background: '#2563eb', borderRadius: 5, marginBottom: 18 }}>
          <LockKeyhole size={22} />
        </div>
        <h1 style={{ fontSize: 22, margin: '0 0 6px' }}>工程師工作站登入</h1>
        <p style={{ color: '#999', fontSize: 13, lineHeight: 1.6, margin: '0 0 22px' }}>登入後只會顯示你的模型、標註成品與個人推薦偏好。</p>
        <label style={{ display: 'block', fontSize: 12, color: '#aaa', marginBottom: 7 }}>帳號</label>
        <input value={username} onChange={event => setUsername(event.target.value)} style={fieldStyle} autoComplete="username" />
        <label style={{ display: 'block', fontSize: 12, color: '#aaa', margin: '16px 0 7px' }}>密碼</label>
        <input value={password} onChange={event => setPassword(event.target.value)} style={fieldStyle} type="password" autoComplete="current-password" />
        {error && <div style={{ marginTop: 14, padding: 10, background: '#2a1717', border: '1px solid #7f1d1d', color: '#fca5a5', fontSize: 13 }}>{error}</div>}
        <button disabled={loading} style={{ ...buttonStyle, width: '100%', marginTop: 20, opacity: loading ? 0.6 : 1 }}>
          <LogIn size={16} /> {loading ? '驗證中' : '登入'}
        </button>
      </form>
    </div>
  );
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

export function AccountDialog({
  user, onClose, onLogout,
}: {
  user: AuthUser;
  onClose: () => void;
  onLogout: () => void;
}) {
  const [tab, setTab] = useState<'preferences' | 'cases' | 'artifacts' | 'users' | 'security'>('preferences');
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [personalCases, setPersonalCases] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [message, setMessage] = useState('');
  const [newUser, setNewUser] = useState({ username: '', display_name: '', email: '', role: 'ENGINEER', password: '' });
  const [createUserError, setCreateUserError] = useState('');
  const [isCreatingUser, setIsCreatingUser] = useState(false);
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' });

  const load = async () => {
    const [preferenceResponse, artifactResponse, caseResponse] = await Promise.all([
      axios.get(`${API_BASE}/api/engineer/preferences`, { withCredentials: true }),
      axios.get(`${API_BASE}/api/engineer/artifacts`, { withCredentials: true }),
      axios.get(`${API_BASE}/api/engineer/tolerance-cases`, { withCredentials: true }),
    ]);
    setPreferences(preferenceResponse.data.preferences);
    setArtifacts(artifactResponse.data.artifacts || []);
    setPersonalCases(caseResponse.data.cases || []);
    if (user.role === 'ADMIN') {
      const userResponse = await axios.get(`${API_BASE}/api/admin/users`, { withCredentials: true });
      setUsers(userResponse.data.users || []);
    }
  };

  useEffect(() => { load().catch(() => setMessage('資料載入失敗。')); }, []);

  const savePreferences = async () => {
    if (!preferences) return;
    const response = await axios.put(`${API_BASE}/api/engineer/preferences`, preferences, { withCredentials: true });
    setPreferences(response.data.preferences);
    setMessage('偏好設定已儲存。');
  };

  const resetPreferences = async () => {
    if (!window.confirm('確定恢復所有推薦與尺寸位置預設？個人學習案例與標註成品不會被刪除。')) return;
    const response = await axios.delete(`${API_BASE}/api/engineer/preferences`, { withCredentials: true });
    setPreferences(response.data.preferences);
    setMessage('偏好設定已恢復預設值。');
  };

  const deletePersonalCase = async (caseRecordId: string) => {
    if (!window.confirm('確定刪除此個人學習案例？後續推薦將不再引用它。')) return;
    await axios.delete(`${API_BASE}/api/engineer/tolerance-cases/${caseRecordId}`, { withCredentials: true });
    setPersonalCases(items => items.filter(item => item.id !== caseRecordId));
    setMessage('個人學習案例已刪除。');
  };

  const clearPersonalCases = async () => {
    if (!window.confirm(`確定刪除全部 ${personalCases.length} 筆個人學習案例？此操作不會刪除標註成品。`)) return;
    const response = await axios.delete(`${API_BASE}/api/engineer/tolerance-cases`, { withCredentials: true });
    setPersonalCases([]);
    setMessage(`已刪除 ${response.data.deleted_count || 0} 筆個人學習案例。`);
  };

  const createUser = async (event: FormEvent) => {
    event.preventDefault();
    setCreateUserError('');
    setMessage('');
    const payload = {
      ...newUser,
      username: newUser.username.trim(),
      display_name: newUser.display_name.trim(),
      email: newUser.email.trim(),
    };
    if (payload.username.length < 2 || !payload.display_name || payload.password.length < 10) {
      setCreateUserError('請填寫至少 2 字元的帳號、顯示名稱，以及至少 10 字元的初始密碼。');
      return;
    }
    setIsCreatingUser(true);
    try {
      await axios.post(`${API_BASE}/api/admin/users`, payload, { withCredentials: true });
      setNewUser({ username: '', display_name: '', email: '', role: 'ENGINEER', password: '' });
      await load();
      setMessage(`帳號 ${payload.username} 已建立，首次登入必須修改密碼。`);
    } catch (reason: any) {
      setCreateUserError(apiErrorMessage(reason, '建立帳號失敗，請稍後再試。'));
    } finally {
      setIsCreatingUser(false);
    }
  };

  const toggleUser = async (target: any) => {
    await axios.patch(`${API_BASE}/api/admin/users/${target.id}`, { is_active: !target.is_active }, { withCredentials: true });
    await load();
  };

  const deleteArtifact = async (id: string) => {
    await axios.delete(`${API_BASE}/api/engineer/artifacts/${id}`, { withCredentials: true });
    setArtifacts(items => items.filter(item => item.id !== id));
  };

  const changePassword = async () => {
    await axios.put(`${API_BASE}/api/auth/password`, passwords, { withCredentials: true });
    onLogout();
  };

  const tabs = [
    { id: 'preferences', label: '推薦與標註偏好', icon: Settings },
    { id: 'cases', label: '個人學習案例', icon: History },
    { id: 'artifacts', label: '我的標註成品', icon: FileBox },
    ...(user.role === 'ADMIN' ? [{ id: 'users', label: '帳號管理', icon: Users }] : []),
    { id: 'security', label: '登入與安全', icon: ShieldCheck },
  ];

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.72)', zIndex: 5000, display: 'grid', placeItems: 'center', padding: 20 }}>
      <div style={{ width: 980, maxWidth: '100%', height: 680, maxHeight: '92vh', background: '#171717', border: '1px solid #333', borderRadius: 8, display: 'flex', overflow: 'hidden' }}>
        <aside style={{ width: 230, background: '#111', borderRight: '1px solid #2b2b2b', padding: 18, boxSizing: 'border-box' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 24 }}>
            <div style={{ width: 36, height: 36, borderRadius: 4, background: '#2563eb', display: 'grid', placeItems: 'center' }}><UserRound size={19} /></div>
            <div><div style={{ fontWeight: 650 }}>{user.display_name}</div><div style={{ color: '#777', fontSize: 12 }}>{user.username} · {user.role}</div></div>
          </div>
          {tabs.map(item => <button key={item.id} onClick={() => setTab(item.id as any)} style={{ width: '100%', ...buttonStyle, justifyContent: 'flex-start', marginBottom: 7, background: tab === item.id ? '#2563eb' : '#202020', fontWeight: 500 }}><item.icon size={16} />{item.label}</button>)}
          <button onClick={onLogout} style={{ width: '100%', ...buttonStyle, justifyContent: 'flex-start', marginTop: 18, background: '#2a2a2a' }}><LogOut size={16} />登出</button>
        </aside>
        <main style={{ flex: 1, padding: 26, overflowY: 'auto' }}>
          <button onClick={onClose} title="關閉" style={{ position: 'absolute', right: 28, top: 28, background: '#292929', border: 0, color: '#ddd', width: 34, height: 34, borderRadius: 4, cursor: 'pointer' }}><X size={17} /></button>
          {message && <div style={{ padding: 10, border: '1px solid #1d4ed8', color: '#bfdbfe', background: '#172554', marginBottom: 18, fontSize: 13 }}>{message}</div>}

          {tab === 'preferences' && preferences && <section>
            <h2 style={{ marginTop: 0 }}>個人推薦與尺寸標註偏好</h2>
            <p style={{ color: '#999', lineHeight: 1.6 }}>公司驗證案例保持共享唯讀；你的標註歷史只會用於你的推薦，其他工程師無法查閱。</p>
            <label style={{ display: 'block', margin: '22px 0 7px', color: '#aaa', fontSize: 12 }}>推薦模式</label>
            <select style={fieldStyle} value={preferences.recommendation_mode} onChange={event => setPreferences({ ...preferences, recommendation_mode: event.target.value as any })}>
              <option value="BALANCED">平衡：公司案例為主，顯示個人建議</option>
              <option value="PERSONAL_FIRST">個人優先：高相似個人案例可取代公司建議</option>
              <option value="COMPANY_ONLY">僅公司驗證案例</option>
            </select>
            <label style={{ display: 'block', margin: '18px 0 7px', color: '#aaa', fontSize: 12 }}>個人案例最低相似度：{Math.round(preferences.personal_case_min_similarity * 100)}%</label>
            <input type="range" min="0.5" max="0.98" step="0.01" value={preferences.personal_case_min_similarity} onChange={event => setPreferences({ ...preferences, personal_case_min_similarity: Number(event.target.value) })} style={{ width: '100%' }} />
            <label style={{ display: 'block', margin: '18px 0 7px', color: '#aaa', fontSize: 12 }}>個人案例權重：{Math.round(preferences.personal_case_weight * 100)}%</label>
            <input type="range" min="0" max="1" step="0.05" value={preferences.personal_case_weight} onChange={event => setPreferences({ ...preferences, personal_case_weight: Number(event.target.value) })} style={{ width: '100%' }} />
            <h3 style={{ marginTop: 26 }}>尺寸位置預設</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <div><label style={{ fontSize: 12, color: '#aaa' }}>偏好視圖</label><select style={{ ...fieldStyle, marginTop: 7 }} value={preferences.dimension_placement?.preferred_view || 'front'} onChange={event => setPreferences({ ...preferences, dimension_placement: { ...preferences.dimension_placement, preferred_view: event.target.value } })}><option value="front">Front</option><option value="top">Top</option><option value="right">Right</option><option value="left">Left</option></select></div>
              <div><label style={{ fontSize: 12, color: '#aaa' }}>偏好側邊</label><select style={{ ...fieldStyle, marginTop: 7 }} value={preferences.dimension_placement?.side || 'BOTTOM'} onChange={event => setPreferences({ ...preferences, dimension_placement: { ...preferences.dimension_placement, side: event.target.value } })}><option>BOTTOM</option><option>TOP</option><option>LEFT</option><option>RIGHT</option></select></div>
              <div><label style={{ fontSize: 12, color: '#aaa' }}>基準</label><select style={{ ...fieldStyle, marginTop: 7 }} value={preferences.dimension_placement?.baseline || 'LEFT'} onChange={event => setPreferences({ ...preferences, dimension_placement: { ...preferences.dimension_placement, baseline: event.target.value } })}><option>LEFT</option><option>RIGHT</option><option>CENTER</option></select></div>
              <div><label style={{ fontSize: 12, color: '#aaa' }}>標註偏移量（mm）</label><input type="number" style={{ ...fieldStyle, marginTop: 7 }} value={preferences.dimension_placement?.offset ?? 12} onChange={event => setPreferences({ ...preferences, dimension_placement: { ...preferences.dimension_placement, offset: Number(event.target.value) } })} /></div>
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 24 }}>
              <button onClick={savePreferences} style={buttonStyle}><Save size={16} />儲存偏好</button>
              <button onClick={resetPreferences} style={{ ...buttonStyle, background: '#2a2a2a', border: '1px solid #444' }}><RotateCcw size={16} />恢復預設</button>
            </div>
          </section>}

          {tab === 'cases' && <section>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
              <div>
                <h2 style={{ marginTop: 0, marginBottom: 6 }}>個人學習案例</h2>
                <p style={{ color: '#999', lineHeight: 1.6, marginTop: 0 }}>這些案例來自你完成的智慧標註，只影響你的公差與標註位置推薦。</p>
              </div>
              <button disabled={personalCases.length === 0} onClick={clearPersonalCases} style={{ ...buttonStyle, background: '#3f1d1d', border: '1px solid #7f1d1d', opacity: personalCases.length === 0 ? .45 : 1, whiteSpace: 'nowrap' }}><Trash2 size={15} />清空全部</button>
            </div>
            {personalCases.length === 0 && <div style={{ padding: 30, border: '1px dashed #444', color: '#777', textAlign: 'center' }}>目前沒有個人學習案例</div>}
            {personalCases.map(item => <div key={item.id} style={{ border: '1px solid #303030', background: '#1e1e1e', padding: 14, marginBottom: 10, display: 'flex', justifyContent: 'space-between', gap: 18, alignItems: 'center' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <strong style={{ color: '#dbeafe' }}>{item.inferred_role || item.feature_type}</strong>
                  <span style={{ color: '#60a5fa', fontSize: 11, border: '1px solid #1e3a8a', padding: '2px 5px' }}>{item.feature_type}</span>
                  <span style={{ color: '#fbbf24', fontSize: 11 }}>{item.tolerance_config?.mode || 'NONE'}</span>
                </div>
                <div style={{ fontSize: 12, color: '#999', marginTop: 7 }}>{item.model_id} / {item.part_id} / {item.rule_id}</div>
                <div style={{ fontSize: 11, color: '#777', marginTop: 5 }}>
                  尺寸：{item.nominal?.value ?? item.nominal?.nominal_value ?? '—'}　位置：{[item.placement?.preferred_view, item.placement?.side, item.placement?.baseline].filter(Boolean).join(' / ') || '未記錄'}
                </div>
              </div>
              <button title="刪除此個人學習案例" onClick={() => deletePersonalCase(item.id)} style={{ background: '#3f1d1d', color: '#fca5a5', border: '1px solid #7f1d1d', padding: 8, cursor: 'pointer', flexShrink: 0 }}><Trash2 size={16} /></button>
            </div>)}
          </section>}

          {tab === 'artifacts' && <section><h2 style={{ marginTop: 0 }}>我的標註成品</h2><p style={{ color: '#999' }}>每次完成智慧標註後會自動建立版本，並從有公差的尺寸學習個人案例。</p>{artifacts.length === 0 && <div style={{ padding: 30, border: '1px dashed #444', color: '#777', textAlign: 'center' }}>尚無標註成品</div>}{artifacts.map(item => <div key={item.id} style={{ border: '1px solid #303030', background: '#1e1e1e', padding: 14, marginBottom: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}><div><div style={{ fontWeight: 650 }}>{item.title}</div><div style={{ fontSize: 12, color: '#888', marginTop: 5 }}>{item.model_id} / {item.part_id} · {new Date(item.created_at).toLocaleString()}</div><div style={{ display: 'flex', gap: 8, marginTop: 9 }}>{item.output_files?.pdf_url && <a href={item.output_files.pdf_url} target="_blank" style={{ color: '#60a5fa' }}>PDF</a>}{item.output_files?.dxf_url && <a href={item.output_files.dxf_url} style={{ color: '#60a5fa' }}>DXF</a>}</div></div><button title="刪除作品資料" onClick={() => deleteArtifact(item.id)} style={{ background: '#3f1d1d', color: '#fca5a5', border: '1px solid #7f1d1d', padding: 8, cursor: 'pointer' }}><Trash2 size={16} /></button></div>)}</section>}

          {tab === 'users' && user.role === 'ADMIN' && <section>
            <h2 style={{ marginTop: 0 }}>工程師帳號管理</h2>
            <form onSubmit={createUser} style={{ border: '1px solid #333', padding: 16, marginBottom: 20 }}>
              <h3 style={{ marginTop: 0 }}><UserPlus size={17} style={{ verticalAlign: -3, marginRight: 7 }} />建立帳號</h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <input required minLength={2} placeholder="帳號" autoComplete="off" style={fieldStyle} value={newUser.username} onChange={event => setNewUser({ ...newUser, username: event.target.value })} />
                <input required placeholder="顯示名稱" autoComplete="off" style={fieldStyle} value={newUser.display_name} onChange={event => setNewUser({ ...newUser, display_name: event.target.value })} />
                <input placeholder="Email（選填）" type="email" autoComplete="off" style={fieldStyle} value={newUser.email} onChange={event => setNewUser({ ...newUser, email: event.target.value })} />
                <select style={fieldStyle} value={newUser.role} onChange={event => setNewUser({ ...newUser, role: event.target.value })}><option value="ENGINEER">工程師</option><option value="ADMIN">管理員</option></select>
                <input required minLength={10} placeholder="初始密碼（至少 10 字元）" type="password" autoComplete="new-password" style={{ ...fieldStyle, gridColumn: '1 / -1' }} value={newUser.password} onChange={event => setNewUser({ ...newUser, password: event.target.value })} />
              </div>
              {createUserError && <div role="alert" style={{ marginTop: 12, padding: 9, background: '#2a1717', border: '1px solid #7f1d1d', color: '#fca5a5', fontSize: 12 }}>{createUserError}</div>}
              <button type="submit" disabled={isCreatingUser} style={{ ...buttonStyle, marginTop: 12, opacity: isCreatingUser ? .6 : 1 }}><UserPlus size={16} />{isCreatingUser ? '建立中' : '建立'}</button>
            </form>
            {users.map(target => <div key={target.id} style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr .8fr auto', gap: 10, alignItems: 'center', borderBottom: '1px solid #2d2d2d', padding: '12px 4px' }}><div><strong>{target.display_name}</strong><div style={{ color: '#777', fontSize: 12 }}>{target.username}</div></div><span>{target.email || '—'}</span><span style={{ color: target.role === 'ADMIN' ? '#93c5fd' : '#bbb' }}>{target.role}</span><button disabled={target.id === user.id} onClick={() => toggleUser(target)} style={{ ...buttonStyle, background: target.is_active ? '#3f1d1d' : '#14532d', opacity: target.id === user.id ? .4 : 1 }}>{target.is_active ? '停用' : '啟用'}</button></div>)}
          </section>}

          {tab === 'security' && <section><h2 style={{ marginTop: 0 }}>登入與安全</h2>{user.must_change_password && <div style={{ padding: 12, background: '#422006', color: '#fde68a', border: '1px solid #92400e', marginBottom: 18 }}>此帳號使用初始或重設密碼，請立即更換。</div>}<label style={{ display: 'block', margin: '12px 0 7px', color: '#aaa' }}>目前密碼</label><input type="password" style={fieldStyle} value={passwords.current_password} onChange={event => setPasswords({ ...passwords, current_password: event.target.value })} /><label style={{ display: 'block', margin: '16px 0 7px', color: '#aaa' }}>新密碼（至少 10 字元）</label><input type="password" style={fieldStyle} value={passwords.new_password} onChange={event => setPasswords({ ...passwords, new_password: event.target.value })} /><button onClick={changePassword} style={{ ...buttonStyle, marginTop: 18 }}><KeyRound size={16} />更新密碼並重新登入</button></section>}
        </main>
      </div>
    </div>
  );
}
