import { useEffect, useMemo, useState, type CSSProperties, type FormEvent } from 'react';
import axios from 'axios';
import {
  Activity, AlertCircle, ArchiveRestore, ArrowLeft, CheckCircle2, Clock3, Download,
  FileSpreadsheet, FileText, KeyRound, Pencil, Plus, RefreshCw, Save, Search,
  ShieldCheck, Tags, Trash2, UserPlus, Users, X,
} from 'lucide-react';
import { LoginPage, apiErrorMessage, buttonStyle, fieldStyle, type AuthUser } from './AccountPortal';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
type Tab = 'accounts' | 'import' | 'passwords' | 'tags' | 'trash' | 'logs' | 'settings';
type Toast = { text: string; kind: 'success' | 'error' } | null;

export default function AdminAccountPage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [tab, setTab] = useState<Tab>('accounts');
  const [users, setUsers] = useState<any[]>([]);
  const [tags, setTags] = useState<any[]>([]);
  const [trash, setTrash] = useState<any[]>([]);
  const [operationLogs, setOperationLogs] = useState<any[]>([]);
  const [auditEvents, setAuditEvents] = useState<any[]>([]);
  const [selectedOperationLog, setSelectedOperationLog] = useState<any>(null);
  const [logLoading, setLogLoading] = useState(false);
  const [logStatus, setLogStatus] = useState<'all' | 'completed' | 'error'>('all');
  const [retentionDays, setRetentionDays] = useState(30);
  const [cadConcurrency, setCadConcurrency] = useState({ analysis_max_concurrency: 1, annotation_max_concurrency: 1, hard_limit: 4 });
  const [domains, setDomains] = useState<string[]>([]);
  const [toast, setToast] = useState<Toast>(null);
  const [createdCredential, setCreatedCredential] = useState<any>(null);
  const [importReview, setImportReview] = useState<any>(null);
  const [domainResolutions, setDomainResolutions] = useState<Record<string, string>>({});
  const [resolvingImport, setResolvingImport] = useState(false);
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [search, setSearch] = useState<Record<string, string>>({});
  const [account, setAccount] = useState({ email_local: '', email_domain: '', display_name: '', role: 'ENGINEER' });
  const [tagForm, setTagForm] = useState({ key: '', name: '', dimension: 'CUSTOMER', description: '' });
  const [newDomain, setNewDomain] = useState('');
  const [editingDomain, setEditingDomain] = useState<string | null>(null);
  const [editedDomain, setEditedDomain] = useState('');

  const load = async () => {
    const [userResponse, tagResponse, trashResponse, retentionResponse, domainResponse, concurrencyResponse] = await Promise.all([
      axios.get(`${API_BASE}/api/admin/users`),
      axios.get(`${API_BASE}/api/admin/recommendation-tags`),
      axios.get(`${API_BASE}/api/admin/trash`),
      axios.get(`${API_BASE}/api/admin/settings/trash-retention`),
      axios.get(`${API_BASE}/api/admin/company-email-domains`),
      axios.get(`${API_BASE}/api/admin/settings/cad-concurrency`),
    ]);
    const loadedUsers = userResponse.data.users || [];
    setUsers(loadedUsers);
    setSelectedUserIds(current => current.filter(id => loadedUsers.some((target: any) => target.id === id)));
    setTags(tagResponse.data.tags || []);
    setTrash(trashResponse.data.records || []);
    setRetentionDays(retentionResponse.data.days || 30);
    setCadConcurrency({
      analysis_max_concurrency: concurrencyResponse.data.analysis_max_concurrency || 1,
      annotation_max_concurrency: concurrencyResponse.data.annotation_max_concurrency || 1,
      hard_limit: concurrencyResponse.data.hard_limit || 4,
    });
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

  const loadLogs = async () => {
    setLogLoading(true);
    try {
      const [operationResponse, auditResponse] = await Promise.all([
        axios.get(`${API_BASE}/api/admin/operation-logs?limit=300`),
        axios.get(`${API_BASE}/api/admin/audit-events?limit=300`),
      ]);
      setOperationLogs(operationResponse.data.logs || []);
      setAuditEvents(auditResponse.data.events || []);
    } catch (reason) {
      fail(reason, '系統紀錄載入失敗');
    } finally {
      setLogLoading(false);
    }
  };

  useEffect(() => {
    if (tab === 'logs' && user?.role === 'ADMIN') loadLogs();
  }, [tab, user?.role]);

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
  const selectableFilteredUsers = filteredUsers.filter(target => target.id !== user?.id);
  const allFilteredUsersSelected = selectableFilteredUsers.length > 0
    && selectableFilteredUsers.every(target => selectedUserIds.includes(target.id));
  const filteredTags = useMemo(
    () => tags.filter(tag => matches('tags', tag.name, tag.key, tag.dimension, tag.description, tag.is_active ? '啟用' : '停用')),
    [tags, search],
  );
  const filteredTrash = useMemo(
    () => trash.filter(item => matches('trash', item.record_type, item.record_id, item.owner_user_id, item.deleted_by_user_id)),
    [trash, search],
  );
  const filteredOperationLogs = useMemo(() => operationLogs.filter(item => {
    if (logStatus !== 'all' && item.status !== logStatus) return false;
    return matches('logs', item.operation_id, item.model_id, item.part_id, item.message, item.error, item.error_type, item.engineer?.display_name, item.engineer?.email);
  }), [operationLogs, search, logStatus]);
  const filteredAuditEvents = useMemo(
    () => auditEvents.filter(item => matches('logs', item.action, item.target_type, item.target_id, item.actor_display_name, item.actor_email, JSON.stringify(item.details || {}))),
    [auditEvents, search],
  );

  const openOperationLog = async (operationId: string) => {
    try {
      const response = await axios.get(`${API_BASE}/api/admin/operation-logs/${operationId}`);
      setSelectedOperationLog(response.data.log);
    } catch (reason) { fail(reason, '詳細紀錄載入失敗'); }
  };

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
      if (response.data.users?.length) setCreatedCredential({ batch: response.data.users });
      const unresolved = response.data.unresolved_domain_rows || [];
      const rejected = response.data.rejected_rows || [];
      if (unresolved.length || rejected.length) {
        setImportReview({ unresolved, rejected });
        setDomainResolutions({});
      }
      await load();
      report(`已建立 ${response.data.created_count} 個帳號${unresolved.length ? `，另有 ${unresolved.length} 筆網域待確認` : ''}`);
    } catch (reason) { fail(reason, '批次匯入失敗'); }
  };

  const resolveImportDomains = async () => {
    const unresolved = importReview?.unresolved || [];
    const sourceDomains = [...new Set(unresolved.map((item: any) => item.source_domain))] as string[];
    if (sourceDomains.some(domain => !domainResolutions[domain])) {
      setToast({ text: '請先為每個未知網域選擇處理方式', kind: 'error' });
      return;
    }
    setResolvingImport(true);
    try {
      const response = await axios.post(`${API_BASE}/api/admin/users/import/resolve-domains`, {
        rows: unresolved,
        resolutions: domainResolutions,
      });
      if (response.data.users?.length) {
        setCreatedCredential((current: any) => ({
          batch: [...(current ? (current.batch || [current]) : []), ...response.data.users],
        }));
      }
      const rejected = [...(importReview?.rejected || []), ...(response.data.rejected_rows || [])];
      setImportReview(rejected.length ? { unresolved: [], rejected } : null);
      setDomains(response.data.company_email_domains || domains);
      await load();
      report(`網域處理完成，新增 ${response.data.created_count} 個帳號`);
    } catch (reason) { fail(reason, '網域處理失敗'); }
    finally { setResolvingImport(false); }
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

  const deleteUser = async (target: any) => {
    if (!window.confirm(`確定永久刪除帳號 ${target.email}？\n\n登入工作階段、個人偏好、個人案例與標註紀錄也會一併刪除，且無法復原。`)) return;
    try {
      await axios.delete(`${API_BASE}/api/admin/users/${target.id}`);
      setSelectedUserIds(current => current.filter(id => id !== target.id));
      await load();
      report(`帳號 ${target.email} 已刪除`);
    } catch (reason) { fail(reason, '帳號刪除失敗'); }
  };

  const toggleAllFilteredUsers = () => {
    const visibleIds = selectableFilteredUsers.map(target => target.id);
    setSelectedUserIds(current => allFilteredUsersSelected
      ? current.filter(id => !visibleIds.includes(id))
      : [...new Set([...current, ...visibleIds])]);
  };

  const deleteSelectedUsers = async () => {
    const selected = users.filter(target => selectedUserIds.includes(target.id));
    if (!selected.length) return;
    if (!window.confirm(`確定永久刪除選取的 ${selected.length} 個帳號？\n\n這些帳號的登入工作階段、個人偏好、個人案例與標註紀錄也會一併刪除，且無法復原。`)) return;
    try {
      const response = await axios.post(`${API_BASE}/api/admin/users/bulk-delete`, { user_ids: selected.map(target => target.id) });
      setSelectedUserIds([]);
      await load();
      report(`已刪除 ${response.data.deleted_count} 個帳號`);
    } catch (reason) { fail(reason, '批量刪除帳號失敗'); }
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
  const saveCadConcurrency = async () => {
    const { analysis_max_concurrency, annotation_max_concurrency } = cadConcurrency;
    const unsafeWarning = analysis_max_concurrency > 1
      ? '警告：模型分析仍在主程序內執行，持續壓測的安全值為 1。智慧標註已使用隔離 Worker，不受此警告影響。\n\n'
      : annotation_max_concurrency > 2
        ? '提醒：智慧標註已通過 4 個隔離 Worker 的短時測試，但正式初始建議值仍為 2。\n\n'
        : '';
    if (!window.confirm(`${unsafeWarning}確定將模型分析上限設為 ${analysis_max_concurrency}、智慧標註上限設為 ${annotation_max_concurrency}？進行中的工作不會被中止。`)) return;
    try {
      const response = await axios.put(`${API_BASE}/api/admin/settings/cad-concurrency`, {
        analysis_max_concurrency,
        annotation_max_concurrency,
      });
      setCadConcurrency(response.data);
      report('多人工作容量已更新，新提交工作立即套用');
    } catch (reason) { fail(reason, '多人工作容量更新失敗'); }
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
    ['trash', '全域回收桶', ArchiveRestore], ['logs', '系統紀錄', Activity],
    ['settings', '系統設定', ShieldCheck],
  ];

  return <div style={{ height: '100vh', overflow: 'hidden', background: '#0a0a0a', color: '#e5e5e5' }}>
    {toast && <ToastMessage toast={toast} onClose={() => setToast(null)} />}
    {importReview && <ImportDomainReviewDialog
      review={importReview}
      domains={domains}
      resolutions={domainResolutions}
      resolving={resolvingImport}
      onResolution={(domain: string, value: string) => setDomainResolutions(current => ({ ...current, [domain]: value }))}
      onConfirm={resolveImportDomains}
      onClose={() => setImportReview(null)}
    />}
    <header style={{ height: 58, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 22px', background: '#171717', borderBottom: '1px solid #303030' }}><div style={{ display: 'flex', alignItems: 'center', gap: 12 }}><ShieldCheck size={21} color="#60a5fa" /><strong>帳號與推薦資料管理</strong><span style={{ color: '#737373', fontSize: 12 }}>{user.email}</span></div><a href="/" style={{ ...buttonStyle, background: '#262626', borderColor: '#444', textDecoration: 'none' }}><ArrowLeft size={15} />返回主系統</a></header>
    <div style={{ display: 'grid', gridTemplateColumns: '230px minmax(0, 1fr)', height: 'calc(100vh - 58px)', minHeight: 0, overflow: 'hidden' }}>
      <nav style={{ background: '#111', borderRight: '1px solid #2b2b2b', padding: 16, minHeight: 0, overflowY: 'auto' }}>{nav.map(([id, label, Icon]) => <button key={id} onClick={() => setTab(id)} style={{ ...buttonStyle, width: '100%', justifyContent: 'flex-start', marginBottom: 8, background: tab === id ? '#2563eb' : '#202020', borderColor: tab === id ? '#2563eb' : '#333' }}><Icon size={16} />{label}</button>)}</nav>
      <main style={{ padding: 28, maxWidth: 1180, width: '100%', height: '100%', minHeight: 0, overflowY: 'auto', boxSizing: 'border-box' }}>
        {createdCredential && <CredentialPanel value={createdCredential} onClose={() => setCreatedCredential(null)} />}

        {tab === 'accounts' && <section><Title title="帳號管理" description="Email 是唯一帳號；新帳號會產生隨機初始密碼。" /><form onSubmit={createAccount} style={panelStyle}><h3><UserPlus size={17} style={{ verticalAlign: -3, marginRight: 7 }} />建立單一帳號</h3><div style={{ display: 'grid', gridTemplateColumns: '1fr 190px 1fr 160px', gap: 10 }}><input required placeholder="Email 帳號部分" style={fieldStyle} value={account.email_local} onChange={event => setAccount({ ...account, email_local: event.target.value.replace(/@.*$/, '') })} /><select style={fieldStyle} value={account.email_domain} onChange={event => setAccount({ ...account, email_domain: event.target.value })}>{domains.map(domain => <option key={domain} value={domain}>@{domain}</option>)}</select><input required placeholder="姓名" style={fieldStyle} value={account.display_name} onChange={event => setAccount({ ...account, display_name: event.target.value })} /><select style={fieldStyle} value={account.role} onChange={event => setAccount({ ...account, role: event.target.value })}><option value="ENGINEER">工程師</option><option value="ADMIN">管理員</option></select></div><button style={{ ...buttonStyle, marginTop: 12 }}><Plus size={15} />建立並產生密碼</button></form><SearchField value={search.accounts || ''} onChange={value => setSearch({ ...search, accounts: value })} placeholder="搜尋姓名、Email、角色或狀態" /><div style={{ ...panelStyle, padding: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}><label style={{ display: 'flex', alignItems: 'center', gap: 9, cursor: selectableFilteredUsers.length ? 'pointer' : 'default', color: '#d4d4d4' }}><input type="checkbox" checked={allFilteredUsersSelected} disabled={!selectableFilteredUsers.length} onChange={toggleAllFilteredUsers} />選取目前搜尋結果</label><div style={{ display: 'flex', alignItems: 'center', gap: 12 }}><span style={subStyle}>已選取 {selectedUserIds.length} 個帳號</span><button disabled={!selectedUserIds.length} onClick={deleteSelectedUsers} style={{ ...buttonStyle, background: '#3f1d1d', borderColor: '#7f1d1d', opacity: selectedUserIds.length ? 1 : .45 }}><Trash2 size={15} />批量刪除</button></div></div><UserTable users={filteredUsers} currentUser={user} selectedUserIds={selectedUserIds} onSelect={(id: string) => setSelectedUserIds(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id])} onToggle={toggleUser} onDelete={deleteUser} /></section>}

        {tab === 'import' && <section><Title title="Excel／CSV 批次建立帳號" description="欄位需包含 email、display_name（或 name），role 可省略。" /><div style={panelStyle}><div style={{ display: 'flex', gap: 9, flexWrap: 'wrap', marginBottom: 14 }}><a href={`${API_BASE}/api/admin/users/import-template?format=xlsx`} style={{ ...buttonStyle, textDecoration: 'none' }}><Download size={15} />下載 Excel 範例</a><a href={`${API_BASE}/api/admin/users/import-template?format=csv`} style={{ ...buttonStyle, background: '#262626', borderColor: '#555', textDecoration: 'none' }}><Download size={15} />下載 CSV 範例</a></div><input type="file" accept=".xlsx,.csv" onChange={event => importAccounts(event.target.files?.[0])} style={fieldStyle} /><p style={{ color: '#888', fontSize: 12 }}>合法帳號會先建立；未知公司網域會先跳過，匯入後由管理員決定新增網域或改套現有網域。</p></div></section>}

        {tab === 'passwords' && <section><Title title="密碼管理" description="忘記密碼只能由管理員重設。初始密碼僅顯示到使用者完成改密為止。" /><SearchField value={search.passwords || ''} onChange={value => setSearch({ ...search, passwords: value })} placeholder="搜尋姓名、Email 或角色" /><div style={listStyle}>{filteredUsers.map(target => <div key={target.id} style={rowStyle}><div><strong>{target.display_name}</strong><div style={subStyle}>{target.email}</div>{target.initial_password && <code style={{ display: 'inline-block', marginTop: 7, color: '#fde68a' }}>{target.initial_password}</code>}</div><button onClick={() => resetPassword(target)} style={buttonStyle}><RefreshCw size={15} />重設密碼</button></div>)}</div></section>}

        {tab === 'tags' && <section><Title title="推薦資料標籤" description="用客戶、產品種類、專案、材料、製程等維度切分公司案例與工程師案例。" /><form onSubmit={createTag} style={panelStyle}><div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 180px', gap: 10 }}><input required placeholder="key，例如 customer:acme" style={fieldStyle} value={tagForm.key} onChange={event => setTagForm({ ...tagForm, key: event.target.value.toLowerCase() })} /><input required placeholder="顯示名稱" style={fieldStyle} value={tagForm.name} onChange={event => setTagForm({ ...tagForm, name: event.target.value })} /><select style={fieldStyle} value={tagForm.dimension} onChange={event => setTagForm({ ...tagForm, dimension: event.target.value })}>{['CUSTOMER','PART_TYPE','PROJECT','MATERIAL','PROCESS','COMPANY_DATABASE','ENGINEER','CUSTOM'].map(value => <option key={value}>{value}</option>)}</select></div><button style={{ ...buttonStyle, marginTop: 12 }}><Plus size={15} />建立標籤</button></form><SearchField value={search.tags || ''} onChange={value => setSearch({ ...search, tags: value })} placeholder="搜尋標籤名稱、key、維度或狀態" /><div style={listStyle}>{filteredTags.map(tag => <div key={tag.id} style={rowStyle}><div><strong>{tag.name}</strong><div style={subStyle}>{tag.key} · {tag.dimension}</div></div><button onClick={() => toggleTag(tag)} style={{ ...buttonStyle, background: tag.is_active ? '#3f1d1d' : '#14532d', borderColor: tag.is_active ? '#7f1d1d' : '#166534' }}>{tag.is_active ? '停用' : '啟用'}</button></div>)}</div></section>}

        {tab === 'trash' && <section><Title title="全域回收桶" description={`可查看所有工程師已刪除的資料；目前保留 ${retentionDays} 天。`} /><SearchField value={search.trash || ''} onChange={value => setSearch({ ...search, trash: value })} placeholder="搜尋資料類型、記錄 ID 或擁有者" />{filteredTrash.length === 0 && <div style={panelStyle}>沒有符合的回收桶資料</div>}<div style={listStyle}>{filteredTrash.map(item => <div key={item.id} style={rowStyle}><div><strong>{item.record_type}</strong><div style={subStyle}>擁有者 {item.owner_user_id} · 到期 {new Date(item.purge_after).toLocaleString()}</div></div><div style={{ display: 'flex', gap: 8 }}><button onClick={() => restoreTrash(item)} style={buttonStyle}><ArchiveRestore size={15} />復原</button><button onClick={() => purgeTrash(item)} style={{ ...buttonStyle, background: '#3f1d1d', borderColor: '#7f1d1d' }}><Trash2 size={15} />永久刪除</button></div></div>)}</div></section>}

        {tab === 'logs' && <SystemLogPanel
          loading={logLoading}
          operationLogs={filteredOperationLogs}
          auditEvents={filteredAuditEvents}
          selected={selectedOperationLog}
          status={logStatus}
          searchValue={search.logs || ''}
          onSearch={(value: string) => setSearch({ ...search, logs: value })}
          onStatus={setLogStatus}
          onSelect={openOperationLog}
          onRefresh={loadLogs}
          onCloseDetail={() => setSelectedOperationLog(null)}
        />}

        {tab === 'settings' && <section>
          <Title title="系統設定" description="管理多人工作容量、公司 Email 網域與回收桶保留期限。" />
          <div style={panelStyle}>
            <h3>多人 CAD 工作容量</h3>
            <p style={{ color: '#999', fontSize: 13, lineHeight: 1.6 }}>限制的是同時執行的重型 CAD 工作，不限制登入或一般瀏覽人數。智慧標註已改用隔離 Worker 並通過 4 Worker 短時測試，正式初始建議值為 2；模型分析目前仍建議維持 1。每位使用者同時間只能有一個同類工作。</p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, maxWidth: 620 }}>
              <label style={{ color: '#aaa', fontSize: 12 }}>模型分析同時上限（1–{cadConcurrency.hard_limit}）<input type="number" min={1} max={cadConcurrency.hard_limit} value={cadConcurrency.analysis_max_concurrency} onChange={event => setCadConcurrency({ ...cadConcurrency, analysis_max_concurrency: Number(event.target.value) })} style={{ ...fieldStyle, marginTop: 7 }} /></label>
              <label style={{ color: '#aaa', fontSize: 12 }}>智慧標註同時上限（1–{cadConcurrency.hard_limit}）<input type="number" min={1} max={cadConcurrency.hard_limit} value={cadConcurrency.annotation_max_concurrency} onChange={event => setCadConcurrency({ ...cadConcurrency, annotation_max_concurrency: Number(event.target.value) })} style={{ ...fieldStyle, marginTop: 7 }} /></label>
            </div>
            <button onClick={saveCadConcurrency} style={{ ...buttonStyle, marginTop: 14 }}><Save size={15} />儲存多人容量</button>
          </div>
          <div style={panelStyle}><h3>公司 Email 網域</h3><form onSubmit={addDomain} style={{ display: 'flex', gap: 9, marginBottom: 16 }}><input required placeholder="company.example" value={newDomain} onChange={event => setNewDomain(event.target.value)} style={fieldStyle} /><button style={buttonStyle}><Plus size={15} />新增網域</button></form>{domains.map(domain => <div key={domain} style={rowStyle}>{editingDomain === domain ? <><input value={editedDomain} onChange={event => setEditedDomain(event.target.value)} style={fieldStyle} /><div style={{ display: 'flex', gap: 8 }}><button onClick={() => saveDomain(domain)} style={buttonStyle}><Save size={15} />儲存</button><button onClick={() => setEditingDomain(null)} style={{ ...buttonStyle, background: '#262626', borderColor: '#555' }}><X size={15} />取消</button></div></> : <><strong>@{domain}</strong><div style={{ display: 'flex', gap: 8 }}><button onClick={() => { setEditingDomain(domain); setEditedDomain(domain); }} style={buttonStyle}><Pencil size={15} />修改</button><button onClick={() => deleteDomain(domain)} style={{ ...buttonStyle, background: '#3f1d1d', borderColor: '#7f1d1d' }}><Trash2 size={15} />刪除</button></div></>}</div>)}</div>
          <div style={{ ...panelStyle, maxWidth: 520 }}><h3>回收桶</h3><label style={{ display: 'block', color: '#aaa', fontSize: 12, marginBottom: 7 }}>保留天數（1–3650）</label><input type="number" min={1} max={3650} value={retentionDays} onChange={event => setRetentionDays(Number(event.target.value))} style={fieldStyle} /><button onClick={saveRetention} style={{ ...buttonStyle, marginTop: 12 }}><Save size={15} />儲存設定</button></div>
        </section>}
      </main>
    </div>
  </div>;
}

const centerStyle: CSSProperties = { minHeight: '100vh', background: '#0a0a0a', color: '#ddd', display: 'grid', placeItems: 'center', textAlign: 'center' };
const panelStyle: CSSProperties = { border: '1px solid #333', background: '#171717', padding: 18, marginBottom: 20 };
const rowStyle: CSSProperties = { border: '1px solid #303030', background: '#171717', padding: 14, marginBottom: 9, display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center' };
const listStyle: CSSProperties = { maxHeight: 'min(52vh, 620px)', overflowY: 'auto', overflowX: 'hidden', paddingRight: 7, scrollbarGutter: 'stable' };
const subStyle: CSSProperties = { color: '#888', fontSize: 12, marginTop: 5 };
function Title({ title, description }: { title: string; description: string }) { return <><h1 style={{ margin: '0 0 7px', fontSize: 24 }}>{title}</h1><p style={{ color: '#999', margin: '0 0 22px' }}>{description}</p></>; }
function SearchField({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) { return <div style={{ position: 'relative', marginBottom: 14 }}><Search size={16} style={{ position: 'absolute', left: 12, top: 11, color: '#737373' }} /><input value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} style={{ ...fieldStyle, paddingLeft: 38 }} /></div>; }
function ToastMessage({ toast, onClose }: { toast: Exclude<Toast, null>; onClose: () => void }) { const error = toast.kind === 'error'; return <div role="status" style={{ position: 'fixed', right: 22, top: 72, zIndex: 1000, width: 360, maxWidth: 'calc(100vw - 44px)', padding: '13px 42px 13px 14px', color: error ? '#fecaca' : '#dbeafe', background: error ? '#2a1717' : '#172554', border: `1px solid ${error ? '#7f1d1d' : '#1d4ed8'}`, boxShadow: '0 12px 28px rgba(0,0,0,.45)' }}>{toast.text}<button onClick={onClose} aria-label="關閉訊息" style={{ position: 'absolute', right: 9, top: 9, border: 0, background: 'transparent', color: 'inherit', cursor: 'pointer' }}><X size={17} /></button></div>; }
function ImportDomainReviewDialog({ review, domains, resolutions, resolving, onResolution, onConfirm, onClose }: any) {
  const unresolved = review.unresolved || [];
  const rejected = review.rejected || [];
  const grouped = unresolved.reduce((result: Record<string, any[]>, row: any) => {
    (result[row.source_domain] ||= []).push(row);
    return result;
  }, {});
  return <div role="dialog" aria-modal="true" aria-labelledby="import-review-title" style={{ position: 'fixed', inset: 0, zIndex: 1100, background: 'rgba(0,0,0,.72)', display: 'grid', placeItems: 'center', padding: 24 }}>
    <div style={{ width: 760, maxWidth: '100%', maxHeight: '88vh', overflowY: 'auto', background: '#171717', border: '1px solid #404040', boxShadow: '0 22px 55px rgba(0,0,0,.6)' }}>
      <div style={{ padding: '18px 20px', borderBottom: '1px solid #333', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <AlertCircle size={21} color="#f59e0b" style={{ flexShrink: 0, marginTop: 2 }} />
        <div><h2 id="import-review-title" style={{ margin: 0, fontSize: 19 }}>批次匯入結果與網域確認</h2><p style={{ color: '#a3a3a3', margin: '7px 0 0', fontSize: 13 }}>合法帳號已先建立。下列未知網域尚未建立帳號，請確認是公司新網域或工程師填寫錯誤。</p></div>
      </div>
      <div style={{ padding: 20 }}>
        {Object.entries(grouped).map(([domain, rows]: [string, any]) => <div key={domain} style={{ ...panelStyle, marginBottom: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(250px, 330px)', gap: 16, alignItems: 'center' }}>
            <div><strong>@{domain}</strong><div style={subStyle}>{rows.length} 筆帳號待處理</div></div>
            <select aria-label={`處理網域 ${domain}`} value={resolutions[domain] || ''} onChange={event => onResolution(domain, event.target.value)} style={fieldStyle}>
              <option value="">請選擇處理方式</option>
              <option value="__add__">新增 @{domain} 為公司網域</option>
              {domains.map((target: string) => <option key={target} value={target}>改套用 @{target}</option>)}
            </select>
          </div>
          <div style={{ marginTop: 12, maxHeight: 130, overflowY: 'auto', borderTop: '1px solid #303030' }}>
            {rows.map((row: any) => <div key={`${row.row_number}-${row.email}`} style={{ padding: '8px 0', color: '#cfcfcf', fontSize: 13 }}>第 {row.row_number} 列 · {row.email} · {row.display_name}</div>)}
          </div>
        </div>)}
        {rejected.length > 0 && <div style={{ ...panelStyle, borderColor: '#7f1d1d', marginBottom: 14 }}>
          <strong style={{ color: '#fca5a5' }}>另有 {rejected.length} 筆無法匯入</strong>
          <div style={{ marginTop: 10, maxHeight: 150, overflowY: 'auto' }}>{rejected.map((row: any, index: number) => <div key={`${row.row_number}-${index}`} style={{ padding: '7px 0', color: '#d4d4d4', fontSize: 13 }}>第 {row.row_number} 列 · {row.email || '未填 Email'} · {row.reason}</div>)}</div>
        </div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9 }}>
          <button onClick={onClose} disabled={resolving} style={{ ...buttonStyle, background: '#262626', borderColor: '#555' }}>{unresolved.length ? '稍後處理' : '關閉'}</button>
          {unresolved.length > 0 && <button onClick={onConfirm} disabled={resolving} style={{ ...buttonStyle, opacity: resolving ? .55 : 1 }}><CheckCircle2 size={15} />{resolving ? '處理中' : '確認並建立帳號'}</button>}
        </div>
      </div>
    </div>
  </div>;
}
function CredentialPanel({ value, onClose }: { value: any; onClose: () => void }) { const rows = value.batch || [value]; return <div style={{ ...panelStyle, borderColor: '#92400e', background: '#29200f' }}><div style={{ display: 'flex', justifyContent: 'space-between' }}><strong>初始密碼（請透過受控管道交付）</strong><button onClick={onClose} style={{ background: 'transparent', border: 0, color: '#ddd', cursor: 'pointer' }}>關閉</button></div>{rows.map((item: any) => <div key={item.id || item.email} style={{ marginTop: 9 }}><span>{item.email}</span><code style={{ marginLeft: 14, color: '#fde68a' }}>{item.initial_password}</code></div>)}</div>; }
function UserTable({ users, currentUser, selectedUserIds, onSelect, onToggle, onDelete }: { users: any[]; currentUser: AuthUser; selectedUserIds: string[]; onSelect: (id: string) => void; onToggle: (user: any) => void; onDelete: (user: any) => void }) { return <div style={listStyle}>{users.map(target => { const isCurrent = target.id === currentUser.id; return <div key={target.id} style={rowStyle}><div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}><input type="checkbox" aria-label={`選取 ${target.email}`} checked={selectedUserIds.includes(target.id)} disabled={isCurrent} onChange={() => onSelect(target.id)} /><div style={{ minWidth: 0 }}><strong>{target.display_name}</strong><div style={subStyle}>{target.email} · {target.role}{isCurrent ? ' · 目前帳號' : ''}</div></div></div><div style={{ display: 'flex', gap: 8, flexShrink: 0 }}><button disabled={isCurrent} onClick={() => onToggle(target)} style={{ ...buttonStyle, opacity: isCurrent ? .4 : 1, background: target.is_active ? '#3f1d1d' : '#14532d', borderColor: target.is_active ? '#7f1d1d' : '#166534' }}>{target.is_active ? '停用' : '啟用'}</button><button disabled={isCurrent} onClick={() => onDelete(target)} style={{ ...buttonStyle, opacity: isCurrent ? .4 : 1, background: '#3f1d1d', borderColor: '#7f1d1d' }}><Trash2 size={15} />刪除</button></div></div>; })}</div>; }

const actionLabels: Record<string, string> = {
  USER_CREATED: '建立帳號', USER_UPDATED: '更新帳號', USER_DELETED: '刪除帳號', PASSWORD_RESET: '重設密碼',
  RECOMMENDATION_TAG_CREATED: '建立推薦標籤', RECOMMENDATION_TAG_UPDATED: '更新推薦標籤',
  ANNOTATION_ARTIFACT_CREATED: '建立標註成品', ANNOTATION_ARTIFACT_TRASHED: '標註成品移至回收桶',
  TRASH_RECORD_RESTORED: '復原回收桶資料', TRASH_RECORD_PURGED: '永久刪除回收桶資料',
  CAD_CONCURRENCY_LIMITS_UPDATED: '更新 CAD 工作容量', COMPANY_EMAIL_DOMAIN_CREATED: '新增公司網域',
  COMPANY_EMAIL_DOMAIN_UPDATED: '修改公司網域', COMPANY_EMAIL_DOMAIN_DELETED: '刪除公司網域',
};

function localTime(value?: string) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-TW', { hour12: false });
}

function SystemLogPanel({ loading, operationLogs, auditEvents, selected, status, searchValue, onSearch, onStatus, onSelect, onRefresh, onCloseDetail }: any) {
  const failure = selected?.failure;
  const request = selected?.request || {};
  const execution = selected?.execution || {};
  return <section>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}>
      <Title title="系統紀錄" description="查看智慧標註工作、崩潰診斷與管理操作；模型僅保存識別資訊，不保存 STEP 二進位內容。" />
      <button onClick={onRefresh} disabled={loading} style={{ ...buttonStyle, flexShrink: 0 }}><RefreshCw size={15} />{loading ? '載入中' : '重新整理'}</button>
    </div>
    <SearchField value={searchValue} onChange={onSearch} placeholder="搜尋操作者、模型、零件、工作編號、錯誤或操作類型" />
    <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
      {([['all', '全部'], ['error', '失敗／崩潰'], ['completed', '已完成']] as const).map(([value, label]) => <button key={value} onClick={() => onStatus(value)} style={{ ...buttonStyle, background: status === value ? '#2563eb' : '#262626', borderColor: status === value ? '#2563eb' : '#444' }}>{label}</button>)}
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: selected ? 'minmax(360px, .9fr) minmax(420px, 1.1fr)' : '1fr', gap: 14, minHeight: 0 }}>
      <div style={{ minHeight: 0 }}>
        <h3 style={{ margin: '0 0 10px', fontSize: 14 }}>智慧標註工作 <span style={{ color: '#737373', fontWeight: 400 }}>({operationLogs.length})</span></h3>
        <div style={{ ...listStyle, maxHeight: 430 }}>
          {operationLogs.length === 0 && <div style={panelStyle}>{loading ? '正在載入紀錄' : '沒有符合的標註工作紀錄'}</div>}
          {operationLogs.map((item: any) => {
            const isError = item.status === 'error';
            const StatusIcon = isError ? AlertCircle : item.status === 'completed' ? CheckCircle2 : Clock3;
            return <button key={item.operation_id} onClick={() => onSelect(item.operation_id)} style={{ ...rowStyle, width: '100%', textAlign: 'left', color: '#e5e5e5', cursor: 'pointer', borderColor: selected?.operation_id === item.operation_id ? '#2563eb' : '#303030' }}>
              <StatusIcon size={18} color={isError ? '#f87171' : item.status === 'completed' ? '#4ade80' : '#fbbf24'} style={{ flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}><strong>{item.model_id || '未知模型'} · {item.part_id || '未知零件'}</strong><div style={subStyle}>{item.engineer?.display_name || item.engineer?.email || '未知操作者'} · {localTime(item.created_at)}</div><div style={{ ...subStyle, color: isError ? '#fca5a5' : '#888', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.error || item.message || item.operation_id}</div></div>
              <span style={{ fontSize: 11, color: '#737373', flexShrink: 0 }}>{item.worker_elapsed_seconds ? `${Number(item.worker_elapsed_seconds).toFixed(2)} s` : item.status}</span>
            </button>;
          })}
        </div>
      </div>
      {selected && <div style={{ ...panelStyle, marginBottom: 0, maxHeight: 520, overflowY: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}><div><h3 style={{ margin: 0 }}>工作詳細紀錄</h3><div style={subStyle}>{selected.operation_id}</div></div><button onClick={onCloseDetail} aria-label="關閉詳細紀錄" style={{ background: 'transparent', border: 0, color: '#aaa', cursor: 'pointer' }}><X size={17} /></button></div>
        <dl style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '8px 12px', margin: '18px 0', fontSize: 12 }}>
          <dt style={detailTermStyle}>狀態</dt><dd style={detailValueStyle}>{selected.status}</dd>
          <dt style={detailTermStyle}>時間</dt><dd style={detailValueStyle}>{localTime(selected.created_at)} → {localTime(selected.completed_at)}</dd>
          <dt style={detailTermStyle}>操作者</dt><dd style={detailValueStyle}>{selected.engineer?.display_name || '—'}<div style={subStyle}>{selected.engineer?.email || selected.engineer?.id}</div></dd>
          <dt style={detailTermStyle}>模型／零件</dt><dd style={detailValueStyle}>{request.model_id || '—'}<div style={subStyle}>{request.part_id || '—'}</div></dd>
          <dt style={detailTermStyle}>引擎</dt><dd style={detailValueStyle}>{request.candidate_engine || 'legacy_smart'}</dd>
          <dt style={detailTermStyle}>標註規則</dt><dd style={detailValueStyle}>{request.feature_record_count || 0} 條</dd>
          <dt style={detailTermStyle}>Worker</dt><dd style={detailValueStyle}>PID {execution.worker_pid || '—'} · {execution.worker_elapsed_seconds ? `${Number(execution.worker_elapsed_seconds).toFixed(3)} 秒` : '—'}</dd>
          <dt style={detailTermStyle}>紀錄大小</dt><dd style={detailValueStyle}>{Math.max(1, Math.round((selected.audit_size_bytes || 0) / 1024))} KB（不含模型檔）</dd>
        </dl>
        {(failure || selected.status === 'error') && <div style={{ border: '1px solid #7f1d1d', background: '#2a1717', padding: 12, marginBottom: 14 }}><strong style={{ color: '#fca5a5' }}>{failure?.error_type || '執行失敗'}</strong><div style={{ color: '#fecaca', fontSize: 12, marginTop: 7, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{failure?.full_error || failure?.public_error || selected.message}</div>{failure?.traceback && <pre style={{ margin: '10px 0 0', padding: 10, maxHeight: 180, overflow: 'auto', background: '#110b0b', color: '#fca5a5', fontSize: 11, whiteSpace: 'pre-wrap' }}>{failure.traceback}</pre>}</div>}
        <h4 style={{ marginBottom: 8 }}>事件時間線</h4>
        {(selected.events || []).map((event: any, index: number) => <div key={`${event.at}-${index}`} style={{ borderLeft: '2px solid #2563eb', padding: '3px 0 12px 12px', fontSize: 12 }}><div>{event.message || event.status}</div><div style={subStyle}>{localTime(event.at)} · {event.status}</div></div>)}
        {execution.output_files && <><h4 style={{ marginBottom: 8 }}><FileText size={14} style={{ verticalAlign: -2, marginRight: 6 }} />輸出紀錄</h4><pre style={{ padding: 10, overflow: 'auto', background: '#111', color: '#a3a3a3', fontSize: 11 }}>{JSON.stringify(execution.output_files, null, 2)}</pre></>}
      </div>}
    </div>
    <h3 style={{ margin: '24px 0 10px', fontSize: 14 }}>管理操作 <span style={{ color: '#737373', fontWeight: 400 }}>({auditEvents.length})</span></h3>
    <div style={{ ...listStyle, maxHeight: 300 }}>{auditEvents.length === 0 && <div style={panelStyle}>沒有符合的管理操作紀錄</div>}{auditEvents.map((event: any) => <div key={event.id} style={rowStyle}><Activity size={16} color="#60a5fa" style={{ flexShrink: 0 }} /><div style={{ flex: 1, minWidth: 0 }}><strong>{actionLabels[event.action] || event.action}</strong><div style={subStyle}>{event.actor_display_name || event.actor_email || event.actor_user_id || '系統'} · {localTime(event.created_at)}</div><div style={{ ...subStyle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{event.target_type || 'SYSTEM'} {event.target_id || ''}</div></div></div>)}</div>
  </section>;
}

const detailTermStyle: CSSProperties = { color: '#737373', margin: 0 };
const detailValueStyle: CSSProperties = { color: '#e5e5e5', margin: 0, minWidth: 0, wordBreak: 'break-word' };
