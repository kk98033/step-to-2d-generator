import { useEffect, useState, type FormEvent } from 'react';
import axios from 'axios';
import {
  ArchiveRestore, ArrowLeft, FileSpreadsheet, KeyRound, Plus, RefreshCw,
  Save, ShieldCheck, Tags, Trash2, UserPlus, Users,
} from 'lucide-react';
import { LoginPage, apiErrorMessage, buttonStyle, fieldStyle, type AuthUser } from './AccountPortal';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
type Tab = 'accounts' | 'import' | 'passwords' | 'tags' | 'trash' | 'settings';

export default function AdminAccountPage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [tab, setTab] = useState<Tab>('accounts');
  const [users, setUsers] = useState<any[]>([]);
  const [tags, setTags] = useState<any[]>([]);
  const [trash, setTrash] = useState<any[]>([]);
  const [retentionDays, setRetentionDays] = useState(30);
  const [domains, setDomains] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [createdCredential, setCreatedCredential] = useState<any>(null);
  const [account, setAccount] = useState({ email_local: '', email_domain: '', display_name: '', role: 'ENGINEER' });
  const [tagForm, setTagForm] = useState({ key: '', name: '', dimension: 'CUSTOMER', description: '' });

  const load = async () => {
    const [userResponse, tagResponse, trashResponse, retentionResponse, configResponse] = await Promise.all([
      axios.get(`${API_BASE}/api/admin/users`),
      axios.get(`${API_BASE}/api/admin/recommendation-tags`),
      axios.get(`${API_BASE}/api/admin/trash`),
      axios.get(`${API_BASE}/api/admin/settings/trash-retention`),
      axios.get(`${API_BASE}/api/auth/config`),
    ]);
    setUsers(userResponse.data.users || []);
    setTags(tagResponse.data.tags || []);
    setTrash(trashResponse.data.records || []);
    setRetentionDays(retentionResponse.data.days || 30);
    const options = configResponse.data.company_email_domains || [];
    setDomains(options);
    setAccount(value => ({ ...value, email_domain: value.email_domain || options[0] || '' }));
  };

  useEffect(() => {
    axios.get(`${API_BASE}/api/auth/me`).then(response => {
      setUser(response.data.user);
      if (response.data.user.role === 'ADMIN') return load();
    }).catch(() => setUser(null)).finally(() => setChecking(false));
  }, []);

  const report = (text: string) => { setMessage(text); setError(''); };
  const fail = (reason: any, fallback: string) => { setError(apiErrorMessage(reason, fallback)); setMessage(''); };

  const createAccount = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const email = `${account.email_local.trim()}@${account.email_domain}`;
      const response = await axios.post(`${API_BASE}/api/admin/users`, { email, display_name: account.display_name.trim(), role: account.role });
      setCreatedCredential(response.data.user);
      setAccount(value => ({ ...value, email_local: '', display_name: '' }));
      await load(); report(`帳號 ${email} 已建立`);
    } catch (reason) { fail(reason, '帳號建立失敗'); }
  };

  const importAccounts = async (file?: File) => {
    if (!file) return;
    const body = new FormData(); body.append('file', file);
    try {
      const response = await axios.post(`${API_BASE}/api/admin/users/import`, body);
      setCreatedCredential({ batch: response.data.users });
      await load(); report(`已建立 ${response.data.created_count} 個帳號`);
    } catch (reason) { fail(reason, '批次匯入失敗'); }
  };

  const resetPassword = async (target: any) => {
    if (!window.confirm(`重設 ${target.email} 的密碼並中止現有登入？`)) return;
    try {
      const response = await axios.post(`${API_BASE}/api/admin/users/${target.id}/reset-password`, {});
      setCreatedCredential({ ...target, initial_password: response.data.initial_password });
      await load(); report('密碼已重設，使用者下次登入後必須修改密碼');
    } catch (reason) { fail(reason, '密碼重設失敗'); }
  };

  const toggleUser = async (target: any) => {
    try { await axios.patch(`${API_BASE}/api/admin/users/${target.id}`, { is_active: !target.is_active }); await load(); }
    catch (reason) { fail(reason, '帳號狀態更新失敗'); }
  };

  const createTag = async (event: FormEvent) => {
    event.preventDefault();
    try { await axios.post(`${API_BASE}/api/admin/recommendation-tags`, tagForm); setTagForm({ key: '', name: '', dimension: 'CUSTOMER', description: '' }); await load(); report('推薦標籤已建立'); }
    catch (reason) { fail(reason, '標籤建立失敗'); }
  };

  const toggleTag = async (tag: any) => {
    try { await axios.patch(`${API_BASE}/api/admin/recommendation-tags/${tag.id}`, { is_active: !tag.is_active }); await load(); }
    catch (reason) { fail(reason, '標籤狀態更新失敗'); }
  };

  const restoreTrash = async (id: string) => { try { await axios.post(`${API_BASE}/api/admin/trash/${id}/restore`); await load(); report('資料已復原'); } catch (reason) { fail(reason, '復原失敗'); } };
  const purgeTrash = async (id: string) => { if (!window.confirm('永久刪除後無法復原，確定繼續？')) return; try { await axios.delete(`${API_BASE}/api/admin/trash/${id}`); await load(); report('資料已永久刪除'); } catch (reason) { fail(reason, '永久刪除失敗'); } };
  const saveRetention = async () => { try { await axios.put(`${API_BASE}/api/admin/settings/trash-retention`, { days: retentionDays }); report('回收桶保留期限已更新'); } catch (reason) { fail(reason, '設定更新失敗'); } };

  if (checking) return <div style={centerStyle}>正在驗證管理員權限</div>;
  if (!user) return <LoginPage onLogin={loggedIn => { setUser(loggedIn); if (loggedIn.role === 'ADMIN') load().catch(reason => fail(reason, '管理資料載入失敗')); }} />;
  if (user.role !== 'ADMIN') return <div style={centerStyle}><div><h2>權限不足</h2><p style={{ color: '#999' }}>此頁面僅供系統管理員使用。</p><a href="/" style={buttonStyle}>返回主系統</a></div></div>;

  const nav = [
    ['accounts', '帳號管理', Users], ['import', '批次匯入', FileSpreadsheet],
    ['passwords', '密碼管理', KeyRound], ['tags', '推薦標籤', Tags],
    ['trash', '全域回收桶', ArchiveRestore], ['settings', '系統設定', ShieldCheck],
  ] as const;

  return <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#e5e5e5' }}>
    <header style={{ height: 58, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 22px', background: '#171717', borderBottom: '1px solid #303030' }}><div style={{ display: 'flex', alignItems: 'center', gap: 12 }}><ShieldCheck size={21} color="#60a5fa" /><strong>帳號與推薦資料管理</strong><span style={{ color: '#737373', fontSize: 12 }}>{user.email}</span></div><a href="/" style={{ ...buttonStyle, background: '#262626', borderColor: '#444', textDecoration: 'none' }}><ArrowLeft size={15} />返回主系統</a></header>
    <div style={{ display: 'grid', gridTemplateColumns: '230px minmax(0, 1fr)', minHeight: 'calc(100vh - 59px)' }}>
      <nav style={{ background: '#111', borderRight: '1px solid #2b2b2b', padding: 16 }}>{nav.map(([id, label, Icon]) => <button key={id} onClick={() => setTab(id)} style={{ ...buttonStyle, width: '100%', justifyContent: 'flex-start', marginBottom: 8, background: tab === id ? '#2563eb' : '#202020', borderColor: tab === id ? '#2563eb' : '#333' }}><Icon size={16} />{label}</button>)}</nav>
      <main style={{ padding: 28, maxWidth: 1180, width: '100%', boxSizing: 'border-box' }}>
        {message && <Notice color="#1d4ed8" background="#172554" text={message} />}{error && <Notice color="#7f1d1d" background="#2a1717" text={error} />}
        {createdCredential && <CredentialPanel value={createdCredential} onClose={() => setCreatedCredential(null)} />}

        {tab === 'accounts' && <section><Title title="帳號管理" description="Email 是唯一帳號；新帳號會產生隨機初始密碼。" /><form onSubmit={createAccount} style={panelStyle}><h3><UserPlus size={17} style={{ verticalAlign: -3, marginRight: 7 }} />建立單一帳號</h3><div style={{ display: 'grid', gridTemplateColumns: '1fr 190px 1fr 160px', gap: 10 }}><input required placeholder="Email 帳號部分" style={fieldStyle} value={account.email_local} onChange={event => setAccount({ ...account, email_local: event.target.value.replace(/@.*$/, '') })} /><select style={fieldStyle} value={account.email_domain} onChange={event => setAccount({ ...account, email_domain: event.target.value })}>{domains.map(domain => <option key={domain} value={domain}>@{domain}</option>)}</select><input required placeholder="姓名" style={fieldStyle} value={account.display_name} onChange={event => setAccount({ ...account, display_name: event.target.value })} /><select style={fieldStyle} value={account.role} onChange={event => setAccount({ ...account, role: event.target.value })}><option value="ENGINEER">工程師</option><option value="ADMIN">管理員</option></select></div><button style={{ ...buttonStyle, marginTop: 12 }}><Plus size={15} />建立並產生密碼</button></form><UserTable users={users} currentUser={user} onToggle={toggleUser} /></section>}

        {tab === 'import' && <section><Title title="Excel／CSV 批次建立帳號" description="欄位需包含 email、display_name（或 name），role 可省略。所有 Email 都必須屬於允許的公司域名。" /><div style={panelStyle}><input type="file" accept=".xlsx,.csv" onChange={event => importAccounts(event.target.files?.[0])} style={fieldStyle} /><p style={{ color: '#888', fontSize: 12 }}>匯入採整批預檢；重複 Email、缺少姓名或錯誤域名會中止該批次。</p></div></section>}

        {tab === 'passwords' && <section><Title title="密碼管理" description="忘記密碼只能由管理員重設。初始密碼僅顯示到使用者完成改密為止。" />{users.map(target => <div key={target.id} style={rowStyle}><div><strong>{target.display_name}</strong><div style={subStyle}>{target.email}</div>{target.initial_password && <code style={{ display: 'inline-block', marginTop: 7, color: '#fde68a' }}>{target.initial_password}</code>}</div><button onClick={() => resetPassword(target)} style={buttonStyle}><RefreshCw size={15} />重設密碼</button></div>)}</section>}

        {tab === 'tags' && <section><Title title="推薦資料標籤" description="用客戶、產品種類、專案、材料、製程等維度切分公司案例與工程師案例。" /><form onSubmit={createTag} style={panelStyle}><div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 180px', gap: 10 }}><input required placeholder="key，例如 customer:acme" style={fieldStyle} value={tagForm.key} onChange={event => setTagForm({ ...tagForm, key: event.target.value.toLowerCase() })} /><input required placeholder="顯示名稱" style={fieldStyle} value={tagForm.name} onChange={event => setTagForm({ ...tagForm, name: event.target.value })} /><select style={fieldStyle} value={tagForm.dimension} onChange={event => setTagForm({ ...tagForm, dimension: event.target.value })}>{['CUSTOMER','PART_TYPE','PROJECT','MATERIAL','PROCESS','COMPANY_DATABASE','ENGINEER','CUSTOM'].map(value => <option key={value}>{value}</option>)}</select></div><button style={{ ...buttonStyle, marginTop: 12 }}><Plus size={15} />建立標籤</button></form>{tags.map(tag => <div key={tag.id} style={rowStyle}><div><strong>{tag.name}</strong><div style={subStyle}>{tag.key} · {tag.dimension}</div></div><button onClick={() => toggleTag(tag)} style={{ ...buttonStyle, background: tag.is_active ? '#3f1d1d' : '#14532d', borderColor: tag.is_active ? '#7f1d1d' : '#166534' }}>{tag.is_active ? '停用' : '啟用'}</button></div>)}</section>}

        {tab === 'trash' && <section><Title title="全域回收桶" description={`可查看所有工程師已刪除的資料；目前保留 ${retentionDays} 天。`} />{trash.length === 0 && <div style={panelStyle}>回收桶目前是空的</div>}{trash.map(item => <div key={item.id} style={rowStyle}><div><strong>{item.record_type}</strong><div style={subStyle}>擁有者 {item.owner_user_id} · 到期 {new Date(item.purge_after).toLocaleString()}</div></div><div style={{ display: 'flex', gap: 8 }}><button onClick={() => restoreTrash(item.id)} style={buttonStyle}><ArchiveRestore size={15} />復原</button><button onClick={() => purgeTrash(item.id)} style={{ ...buttonStyle, background: '#3f1d1d', borderColor: '#7f1d1d' }}><Trash2 size={15} />永久刪除</button></div></div>)}</section>}

        {tab === 'settings' && <section><Title title="系統設定" description="設定工程師刪除資料的可復原期限。" /><div style={{ ...panelStyle, maxWidth: 520 }}><label style={{ display: 'block', color: '#aaa', fontSize: 12, marginBottom: 7 }}>回收桶保留天數（1–3650）</label><input type="number" min={1} max={3650} value={retentionDays} onChange={event => setRetentionDays(Number(event.target.value))} style={fieldStyle} /><button onClick={saveRetention} style={{ ...buttonStyle, marginTop: 12 }}><Save size={15} />儲存設定</button></div></section>}
      </main>
    </div>
  </div>;
}

const centerStyle = { minHeight: '100vh', background: '#0a0a0a', color: '#ddd', display: 'grid', placeItems: 'center', textAlign: 'center' as const };
const panelStyle = { border: '1px solid #333', background: '#171717', padding: 18, marginBottom: 20 };
const rowStyle = { border: '1px solid #303030', background: '#171717', padding: 14, marginBottom: 9, display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center' };
const subStyle = { color: '#888', fontSize: 12, marginTop: 5 };
function Title({ title, description }: { title: string; description: string }) { return <><h1 style={{ margin: '0 0 7px', fontSize: 24 }}>{title}</h1><p style={{ color: '#999', margin: '0 0 22px' }}>{description}</p></>; }
function Notice({ color, background, text }: { color: string; background: string; text: string }) { return <div style={{ border: `1px solid ${color}`, background, padding: 11, marginBottom: 16 }}>{text}</div>; }
function CredentialPanel({ value, onClose }: { value: any; onClose: () => void }) { const rows = value.batch || [value]; return <div style={{ ...panelStyle, borderColor: '#92400e', background: '#29200f' }}><div style={{ display: 'flex', justifyContent: 'space-between' }}><strong>初始密碼（請透過受控管道交付）</strong><button onClick={onClose} style={{ background: 'transparent', border: 0, color: '#ddd', cursor: 'pointer' }}>關閉</button></div>{rows.map((item: any) => <div key={item.id || item.email} style={{ marginTop: 9 }}><span>{item.email}</span><code style={{ marginLeft: 14, color: '#fde68a' }}>{item.initial_password}</code></div>)}</div>; }
function UserTable({ users, currentUser, onToggle }: { users: any[]; currentUser: AuthUser; onToggle: (user: any) => void }) { return <div>{users.map(target => <div key={target.id} style={rowStyle}><div><strong>{target.display_name}</strong><div style={subStyle}>{target.email} · {target.role}</div></div><button disabled={target.id === currentUser.id} onClick={() => onToggle(target)} style={{ ...buttonStyle, opacity: target.id === currentUser.id ? .4 : 1, background: target.is_active ? '#3f1d1d' : '#14532d', borderColor: target.is_active ? '#7f1d1d' : '#166534' }}>{target.is_active ? '停用' : '啟用'}</button></div>)}</div>; }
