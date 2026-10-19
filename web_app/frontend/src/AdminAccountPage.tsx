import { useEffect, useMemo, useState, type CSSProperties, type FormEvent } from 'react';
import axios from 'axios';
import {
  ArchiveRestore, ArrowLeft, Download, FileSpreadsheet, KeyRound, Pencil,
  Plus, RefreshCw, Save, Search, ShieldCheck, Tags, Trash2, UserPlus, Users, X,
} from 'lucide-react';
import { LoginPage, apiErrorMessage, buttonStyle, fieldStyle, type AuthUser } from './AccountPortal';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
type Tab = 'accounts' | 'import' | 'passwords' | 'tags' | 'trash' | 'settings';
type Toast = { text: string; kind: 'success' | 'error' } | null;

export default function AdminAccountPage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [tab, setTab] = useState<Tab>('accounts');
  const [users, setUsers] = useState<any[]>([]);
  const [tags, setTags] = useState<any[]>([]);
  const [trash, setTrash] = useState<any[]>([]);
  const [retentionDays, setRetentionDays] = useState(30);
  const [domains, setDomains] = useState<string[]>([]);
  const [toast, setToast] = useState<Toast>(null);
  const [createdCredential, setCreatedCredential] = useState<any>(null);
  const [search, setSearch] = useState<Record<string, string>>({});
  const [account, setAccount] = useState({ email_local: '', email_domain: '', display_name: '', role: 'ENGINEER' });
  const [tagForm, setTagForm] = useState({ key: '', name: '', dimension: 'CUSTOMER', description: '' });
  const [newDomain, setNewDomain] = useState('');
  const [editingDomain, setEditingDomain] = useState<string | null>(null);
  const [editedDomain, setEditedDomain] = useState('');

  const load = async () => {
    const [userResponse, tagResponse, trashResponse, retentionResponse, domainResponse] = await Promise.all([
      axios.get(`${API_BASE}/api/admin/users`),
      axios.get(`${API_BASE}/api/admin/recommendation-tags`),
      axios.get(`${API_BASE}/api/admin/trash`),
      axios.get(`${API_BASE}/api/admin/settings/trash-retention`),
      axios.get(`${API_BASE}/api/admin/company-email-domains`),
    ]);
    setUsers(userResponse.data.users || []);
    setTags(tagResponse.data.tags || []);
    setTrash(trashResponse.data.records || []);
    setRetentionDays(retentionResponse.data.days || 30);
    const options = domainResponse.data.domains || [];
    setDomains(options);
    setAccount(value => ({ ...value, email_domain: options.includes(value.email_domain) ? value.email_domain : (options[0] || '') }));
  };

  useEffect(() => {
    axios.get(`${API_BASE}/api/auth/me`).then(response => {
      setUser(response.data.user);
      if (response.data.user.role === 'ADMIN') return load();
    }).catch(() => setUser(null)).finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const report = (text: string) => setToast({ text, kind: 'success' });
  const fail = (reason: any, fallback: string) => setToast({ text: apiErrorMessage(reason, fallback), kind: 'error' });
  const query = (key: string) => (search[key] || '').trim().toLowerCase();
  const matches = (key: string, ...values: unknown[]) => !query(key) || values.some(value => String(value || '').toLowerCase().includes(query(key)));

  const filteredUsers = useMemo(
    () => users.filter(target => matches(tab === 'passwords' ? 'passwords' : 'accounts', target.email, target.display_name, target.role, target.is_active ? '啟用' : '停用')),
    [users, search, tab],
  );
  const filteredTags = useMemo(
    () => tags.filter(tag => matches('tags', tag.name, tag.key, tag.dimension, tag.description, tag.is_active ? '啟用' : '停用')),
    [tags, search],
  );
  const filteredTrash = useMemo(
    () => trash.filter(item => matches('trash', item.record_type, item.record_id, item.owner_user_id, item.deleted_by_user_id)),
    [trash, search],
  );

  const createAccount = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const email = `${account.email_local.trim()}@${account.email_domain}`;
      const response = await axios.post(`${API_BASE}/api/admin/users`, { email, display_name: account.display_name.trim(), role: account.role });
      setCreatedCredential(response.data.user);
      setAccount(value => ({ ...value, email_local: '', display_name: '' }));
      await load();
      report(`帳號 ${email} 已建立`);
    } catch (reason) { fail(reason, '帳號建立失敗'); }
  };

  const importAccounts = async (file?: File) => {
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    try {
      const response = await axios.post(`${API_BASE}/api/admin/users/import`, body);
      setCreatedCredential({ batch: response.data.users });
      await load();
      report(`已建立 ${response.data.created_count} 個帳號`);
    } catch (reason) { fail(reason, '批次匯入失敗'); }
  };

  const resetPassword = async (target: any) => {
    if (!window.confirm(`重設 ${target.email} 的密碼並中止現有登入？`)) return;
    try {
      const response = await axios.post(`${API_BASE}/api/admin/users/${target.id}/reset-password`, {});
      setCreatedCredential({ ...target, initial_password: response.data.initial_password });
      await load();
      report('密碼已重設，使用者下次登入後必須修改密碼');
    } catch (reason) { fail(reason, '密碼重設失敗'); }
  };

  const toggleUser = async (target: any) => {
    const action = target.is_active ? '停用' : '啟用';
    if (!window.confirm(`確定要${action}帳號 ${target.email}？`)) return;
    try {
      await axios.patch(`${API_BASE}/api/admin/users/${target.id}`, { is_active: !target.is_active });
      await load();
      report(`帳號已${action}`);
    } catch (reason) { fail(reason, '帳號狀態更新失敗'); }
  };

  const createTag = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await axios.post(`${API_BASE}/api/admin/recommendation-tags`, tagForm);
      setTagForm({ key: '', name: '', dimension: 'CUSTOMER', description: '' });
      await load();
      report('推薦標籤已建立');
    } catch (reason) { fail(reason, '標籤建立失敗'); }
  };

  const toggleTag = async (tag: any) => {
    const action = tag.is_active ? '停用' : '啟用';
    if (!window.confirm(`確定要${action}推薦標籤「${tag.name}」？`)) return;
    try {
      await axios.patch(`${API_BASE}/api/admin/recommendation-tags/${tag.id}`, { is_active: !tag.is_active });
      await load();
      report(`推薦標籤已${action}`);
    } catch (reason) { fail(reason, '標籤狀態更新失敗'); }
  };

  const restoreTrash = async (item: any) => {
    if (!window.confirm(`確定要復原 ${item.record_type}？`)) return;
    try { await axios.post(`${API_BASE}/api/admin/trash/${item.id}/restore`); await load(); report('資料已復原'); }
    catch (reason) { fail(reason, '復原失敗'); }
  };
  const purgeTrash = async (item: any) => {
    if (!window.confirm(`永久刪除 ${item.record_type} 後無法復原，確定繼續？`)) return;
    try { await axios.delete(`${API_BASE}/api/admin/trash/${item.id}`); await load(); report('資料已永久刪除'); }
    catch (reason) { fail(reason, '永久刪除失敗'); }
  };
  const saveRetention = async () => {
    if (!window.confirm(`確定將回收桶保留期限改為 ${retentionDays} 天？`)) return;
    try { await axios.put(`${API_BASE}/api/admin/settings/trash-retention`, { days: retentionDays }); report('回收桶保留期限已更新'); }
    catch (reason) { fail(reason, '設定更新失敗'); }
  };

  const addDomain = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await axios.post(`${API_BASE}/api/admin/company-email-domains`, { domain: newDomain });
      setNewDomain(''); await load(); report('公司 Email 網域已新增');
    } catch (reason) { fail(reason, '網域新增失敗'); }
  };
  const saveDomain = async (domain: string) => {
    if (!window.confirm(`將 ${domain} 修改為 ${editedDomain}？現有帳號會同步更新。`)) return;
    try {
      await axios.put(`${API_BASE}/api/admin/company-email-domains/${encodeURIComponent(domain)}`, { domain: editedDomain });
      setEditingDomain(null); setEditedDomain(''); await load(); report('公司 Email 網域已修改');
    } catch (reason) { fail(reason, '網域修改失敗'); }
  };
  const deleteDomain = async (domain: string) => {
    if (!window.confirm(`確定刪除公司 Email 網域 ${domain}？`)) return;
    try { await axios.delete(`${API_BASE}/api/admin/company-email-domains/${encodeURIComponent(domain)}`); await load(); report('公司 Email 網域已刪除'); }
    catch (reason) { fail(reason, '網域刪除失敗'); }
  };

  if (checking) return <div style={centerStyle}>正在驗證管理員權限</div>;
  if (!user) return <LoginPage onLogin={loggedIn => { setUser(loggedIn); if (loggedIn.role === 'ADMIN') load().catch(reason => fail(reason, '管理資料載入失敗')); }} />;
  if (user.role !== 'ADMIN') return <div style={centerStyle}><div><h2>權限不足</h2><p style={{ color: '#999' }}>此頁面僅供系統管理員使用。</p><a href="/" style={buttonStyle}>返回主系統</a></div></div>;

  const nav: Array<[Tab, string, typeof Users]> = [
    ['accounts', '帳號管理', Users], ['import', '批次匯入', FileSpreadsheet],
    ['passwords', '密碼管理', KeyRound], ['tags', '推薦標籤', Tags],
    ['trash', '全域回收桶', ArchiveRestore], ['settings', '系統設定', ShieldCheck],
  ];

  return <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#e5e5e5' }}>
    {toast && <ToastMessage toast={toast} onClose={() => setToast(null)} />}
    <header style={{ height: 58, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 22px', background: '#171717', borderBottom: '1px solid #303030' }}><div style={{ display: 'flex', alignItems: 'center', gap: 12 }}><ShieldCheck size={21} color="#60a5fa" /><strong>帳號與推薦資料管理</strong><span style={{ color: '#737373', fontSize: 12 }}>{user.email}</span></div><a href="/" style={{ ...buttonStyle, background: '#262626', borderColor: '#444', textDecoration: 'none' }}><ArrowLeft size={15} />返回主系統</a></header>
    <div style={{ display: 'grid', gridTemplateColumns: '230px minmax(0, 1fr)', minHeight: 'calc(100vh - 59px)' }}>
      <nav style={{ background: '#111', borderRight: '1px solid #2b2b2b', padding: 16 }}>{nav.map(([id, label, Icon]) => <button key={id} onClick={() => setTab(id)} style={{ ...buttonStyle, width: '100%', justifyContent: 'flex-start', marginBottom: 8, background: tab === id ? '#2563eb' : '#202020', borderColor: tab === id ? '#2563eb' : '#333' }}><Icon size={16} />{label}</button>)}</nav>
      <main style={{ padding: 28, maxWidth: 1180, width: '100%', boxSizing: 'border-box' }}>
        {createdCredential && <CredentialPanel value={createdCredential} onClose={() => setCreatedCredential(null)} />}

        {tab === 'accounts' && <section><Title title="帳號管理" description="Email 是唯一帳號；新帳號會產生隨機初始密碼。" /><form onSubmit={createAccount} style={panelStyle}><h3><UserPlus size={17} style={{ verticalAlign: -3, marginRight: 7 }} />建立單一帳號</h3><div style={{ display: 'grid', gridTemplateColumns: '1fr 190px 1fr 160px', gap: 10 }}><input required placeholder="Email 帳號部分" style={fieldStyle} value={account.email_local} onChange={event => setAccount({ ...account, email_local: event.target.value.replace(/@.*$/, '') })} /><select style={fieldStyle} value={account.email_domain} onChange={event => setAccount({ ...account, email_domain: event.target.value })}>{domains.map(domain => <option key={domain} value={domain}>@{domain}</option>)}</select><input required placeholder="姓名" style={fieldStyle} value={account.display_name} onChange={event => setAccount({ ...account, display_name: event.target.value })} /><select style={fieldStyle} value={account.role} onChange={event => setAccount({ ...account, role: event.target.value })}><option value="ENGINEER">工程師</option><option value="ADMIN">管理員</option></select></div><button style={{ ...buttonStyle, marginTop: 12 }}><Plus size={15} />建立並產生密碼</button></form><SearchField value={search.accounts || ''} onChange={value => setSearch({ ...search, accounts: value })} placeholder="搜尋姓名、Email、角色或狀態" /><UserTable users={filteredUsers} currentUser={user} onToggle={toggleUser} /></section>}

        {tab === 'import' && <section><Title title="Excel／CSV 批次建立帳號" description="欄位需包含 email、display_name（或 name），role 可省略。" /><div style={panelStyle}><div style={{ display: 'flex', gap: 9, flexWrap: 'wrap', marginBottom: 14 }}><a href={`${API_BASE}/api/admin/users/import-template?format=xlsx`} style={{ ...buttonStyle, textDecoration: 'none' }}><Download size={15} />下載 Excel 範例</a><a href={`${API_BASE}/api/admin/users/import-template?format=csv`} style={{ ...buttonStyle, background: '#262626', borderColor: '#555', textDecoration: 'none' }}><Download size={15} />下載 CSV 範例</a></div><input type="file" accept=".xlsx,.csv" onChange={event => importAccounts(event.target.files?.[0])} style={fieldStyle} /><p style={{ color: '#888', fontSize: 12 }}>匯入採整批預檢；重複 Email、缺少姓名或錯誤網域會中止該批次。</p></div></section>}

        {tab === 'passwords' && <section><Title title="密碼管理" description="忘記密碼只能由管理員重設。初始密碼僅顯示到使用者完成改密為止。" /><SearchField value={search.passwords || ''} onChange={value => setSearch({ ...search, passwords: value })} placeholder="搜尋姓名、Email 或角色" />{filteredUsers.map(target => <div key={target.id} style={rowStyle}><div><strong>{target.display_name}</strong><div style={subStyle}>{target.email}</div>{target.initial_password && <code style={{ display: 'inline-block', marginTop: 7, color: '#fde68a' }}>{target.initial_password}</code>}</div><button onClick={() => resetPassword(target)} style={buttonStyle}><RefreshCw size={15} />重設密碼</button></div>)}</section>}

        {tab === 'tags' && <section><Title title="推薦資料標籤" description="用客戶、產品種類、專案、材料、製程等維度切分公司案例與工程師案例。" /><form onSubmit={createTag} style={panelStyle}><div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 180px', gap: 10 }}><input required placeholder="key，例如 customer:acme" style={fieldStyle} value={tagForm.key} onChange={event => setTagForm({ ...tagForm, key: event.target.value.toLowerCase() })} /><input required placeholder="顯示名稱" style={fieldStyle} value={tagForm.name} onChange={event => setTagForm({ ...tagForm, name: event.target.value })} /><select style={fieldStyle} value={tagForm.dimension} onChange={event => setTagForm({ ...tagForm, dimension: event.target.value })}>{['CUSTOMER','PART_TYPE','PROJECT','MATERIAL','PROCESS','COMPANY_DATABASE','ENGINEER','CUSTOM'].map(value => <option key={value}>{value}</option>)}</select></div><button style={{ ...buttonStyle, marginTop: 12 }}><Plus size={15} />建立標籤</button></form><SearchField value={search.tags || ''} onChange={value => setSearch({ ...search, tags: value })} placeholder="搜尋標籤名稱、key、維度或狀態" />{filteredTags.map(tag => <div key={tag.id} style={rowStyle}><div><strong>{tag.name}</strong><div style={subStyle}>{tag.key} · {tag.dimension}</div></div><button onClick={() => toggleTag(tag)} style={{ ...buttonStyle, background: tag.is_active ? '#3f1d1d' : '#14532d', borderColor: tag.is_active ? '#7f1d1d' : '#166534' }}>{tag.is_active ? '停用' : '啟用'}</button></div>)}</section>}

        {tab === 'trash' && <section><Title title="全域回收桶" description={`可查看所有工程師已刪除的資料；目前保留 ${retentionDays} 天。`} /><SearchField value={search.trash || ''} onChange={value => setSearch({ ...search, trash: value })} placeholder="搜尋資料類型、記錄 ID 或擁有者" />{filteredTrash.length === 0 && <div style={panelStyle}>沒有符合的回收桶資料</div>}{filteredTrash.map(item => <div key={item.id} style={rowStyle}><div><strong>{item.record_type}</strong><div style={subStyle}>擁有者 {item.owner_user_id} · 到期 {new Date(item.purge_after).toLocaleString()}</div></div><div style={{ display: 'flex', gap: 8 }}><button onClick={() => restoreTrash(item)} style={buttonStyle}><ArchiveRestore size={15} />復原</button><button onClick={() => purgeTrash(item)} style={{ ...buttonStyle, background: '#3f1d1d', borderColor: '#7f1d1d' }}><Trash2 size={15} />永久刪除</button></div></div>)}</section>}

        {tab === 'settings' && <section><Title title="系統設定" description="管理公司 Email 網域與回收桶保留期限。" /><div style={panelStyle}><h3>公司 Email 網域</h3><form onSubmit={addDomain} style={{ display: 'flex', gap: 9, marginBottom: 16 }}><input required placeholder="company.example" value={newDomain} onChange={event => setNewDomain(event.target.value)} style={fieldStyle} /><button style={buttonStyle}><Plus size={15} />新增網域</button></form>{domains.map(domain => <div key={domain} style={rowStyle}>{editingDomain === domain ? <><input value={editedDomain} onChange={event => setEditedDomain(event.target.value)} style={fieldStyle} /><div style={{ display: 'flex', gap: 8 }}><button onClick={() => saveDomain(domain)} style={buttonStyle}><Save size={15} />儲存</button><button onClick={() => setEditingDomain(null)} style={{ ...buttonStyle, background: '#262626', borderColor: '#555' }}><X size={15} />取消</button></div></> : <><strong>@{domain}</strong><div style={{ display: 'flex', gap: 8 }}><button onClick={() => { setEditingDomain(domain); setEditedDomain(domain); }} style={buttonStyle}><Pencil size={15} />修改</button><button onClick={() => deleteDomain(domain)} style={{ ...buttonStyle, background: '#3f1d1d', borderColor: '#7f1d1d' }}><Trash2 size={15} />刪除</button></div></>}</div>)}</div><div style={{ ...panelStyle, maxWidth: 520 }}><h3>回收桶</h3><label style={{ display: 'block', color: '#aaa', fontSize: 12, marginBottom: 7 }}>保留天數（1–3650）</label><input type="number" min={1} max={3650} value={retentionDays} onChange={event => setRetentionDays(Number(event.target.value))} style={fieldStyle} /><button onClick={saveRetention} style={{ ...buttonStyle, marginTop: 12 }}><Save size={15} />儲存設定</button></div></section>}
      </main>
    </div>
  </div>;
}

const centerStyle: CSSProperties = { minHeight: '100vh', background: '#0a0a0a', color: '#ddd', display: 'grid', placeItems: 'center', textAlign: 'center' };
const panelStyle: CSSProperties = { border: '1px solid #333', background: '#171717', padding: 18, marginBottom: 20 };
const rowStyle: CSSProperties = { border: '1px solid #303030', background: '#171717', padding: 14, marginBottom: 9, display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center' };
const subStyle: CSSProperties = { color: '#888', fontSize: 12, marginTop: 5 };
function Title({ title, description }: { title: string; description: string }) { return <><h1 style={{ margin: '0 0 7px', fontSize: 24 }}>{title}</h1><p style={{ color: '#999', margin: '0 0 22px' }}>{description}</p></>; }
function SearchField({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) { return <div style={{ position: 'relative', marginBottom: 14 }}><Search size={16} style={{ position: 'absolute', left: 12, top: 11, color: '#737373' }} /><input value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} style={{ ...fieldStyle, paddingLeft: 38 }} /></div>; }
function ToastMessage({ toast, onClose }: { toast: Exclude<Toast, null>; onClose: () => void }) { const error = toast.kind === 'error'; return <div role="status" style={{ position: 'fixed', right: 22, top: 72, zIndex: 1000, width: 360, maxWidth: 'calc(100vw - 44px)', padding: '13px 42px 13px 14px', color: error ? '#fecaca' : '#dbeafe', background: error ? '#2a1717' : '#172554', border: `1px solid ${error ? '#7f1d1d' : '#1d4ed8'}`, boxShadow: '0 12px 28px rgba(0,0,0,.45)' }}>{toast.text}<button onClick={onClose} aria-label="關閉訊息" style={{ position: 'absolute', right: 9, top: 9, border: 0, background: 'transparent', color: 'inherit', cursor: 'pointer' }}><X size={17} /></button></div>; }
function CredentialPanel({ value, onClose }: { value: any; onClose: () => void }) { const rows = value.batch || [value]; return <div style={{ ...panelStyle, borderColor: '#92400e', background: '#29200f' }}><div style={{ display: 'flex', justifyContent: 'space-between' }}><strong>初始密碼（請透過受控管道交付）</strong><button onClick={onClose} style={{ background: 'transparent', border: 0, color: '#ddd', cursor: 'pointer' }}>關閉</button></div>{rows.map((item: any) => <div key={item.id || item.email} style={{ marginTop: 9 }}><span>{item.email}</span><code style={{ marginLeft: 14, color: '#fde68a' }}>{item.initial_password}</code></div>)}</div>; }
function UserTable({ users, currentUser, onToggle }: { users: any[]; currentUser: AuthUser; onToggle: (user: any) => void }) { return <div>{users.map(target => <div key={target.id} style={rowStyle}><div><strong>{target.display_name}</strong><div style={subStyle}>{target.email} · {target.role}</div></div><button disabled={target.id === currentUser.id} onClick={() => onToggle(target)} style={{ ...buttonStyle, opacity: target.id === currentUser.id ? .4 : 1, background: target.is_active ? '#3f1d1d' : '#14532d', borderColor: target.is_active ? '#7f1d1d' : '#166534' }}>{target.is_active ? '停用' : '啟用'}</button></div>)}</div>; }
