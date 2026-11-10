import { useEffect, useState, useRef } from 'react';
import axios from 'axios';
import { X, FolderPlus, Database, ExternalLink, Check, Archive } from 'lucide-react';
import './caseLibrary.css';

const API = import.meta.env.VITE_API_BASE_URL || '';
const errorMessage = (error: any) => typeof error?.response?.data?.detail === 'string' ? error.response.data.detail : error.message || '操作失敗';

export default function CaseLibraryPanel({ onClose, isAdmin, initialArtifactId, onChanged, embedded = false, mode = 'personal' }: {
  onClose?: () => void; isAdmin: boolean; initialArtifactId?: string; onChanged?: () => void;
  embedded?: boolean; mode?: 'personal' | 'review';
}) {
  const reviewing = mode === 'review' && isAdmin;
  const [tab, setTab] = useState<'artifacts' | 'collections' | 'cases' | 'review'>(reviewing ? 'review' : initialArtifactId ? 'artifacts' : 'collections');
  const [data, setData] = useState<any>({ collections: [], customers: [], artifacts: [], cases: [], tags: [], requests: [] });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [collectionName, setCollectionName] = useState('');
  const [customerLink, setCustomerLink] = useState('');
  const [artifactId, setArtifactId] = useState(initialArtifactId || '');
  const [ruleIds, setRuleIds] = useState<string[]>([]);
  const [collectionIds, setCollectionIds] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [reviewTags, setReviewTags] = useState<Record<string, string[]>>({});
  const [compatibility, setCompatibility] = useState<any>(null);
  const initializedArtifact = useRef('');

  const load = async () => {
    if (reviewing) {
      const [requests, tags] = await Promise.all([axios.get(`${API}/api/case-library/share-requests`), axios.get(`${API}/api/recommendation-tags`)]);
      setData((previous: any) => ({ ...previous, ...requests.data, ...tags.data }));
      return;
    }
    const responses = await Promise.all([
      axios.get(`${API}/api/case-library/collections`), axios.get(`${API}/api/case-library/artifacts`),
      axios.get(`${API}/api/case-library/cases`), axios.get(`${API}/api/recommendation-tags`),
    ]);
    setData((previous: any) => ({ ...previous, ...responses[0].data, ...responses[1].data, ...responses[2].data, ...responses[3].data }));
  };
  useEffect(() => { load().catch(error => setMessage(errorMessage(error))); }, []);
  useEffect(() => {
    const artifact = data.artifacts.find((item: any) => item.id === artifactId);
    if (!artifact || initializedArtifact.current === artifactId) return;
    initializedArtifact.current = artifactId;
    setRuleIds((artifact?.features || []).filter((item: any) => item.tolerance || (item.tolerance_config?.mode && item.tolerance_config.mode !== 'NONE')).map((item: any) => item.rule_id || item.id));
    setConfirmed(false);
  }, [artifactId, data.artifacts]);

  const execute = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true); setMessage('');
    try { await action(); await load(); onChanged?.(); setMessage(success); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const toggle = (values: string[], value: string) => values.includes(value) ? values.filter(item => item !== value) : [...values, value];
  const artifact = data.artifacts.find((item: any) => item.id === artifactId);

  const content = <section className={`case-library${embedded ? ' case-library-embedded' : ''}`} role={embedded ? 'region' : 'dialog'} aria-modal={embedded ? undefined : true} aria-label={reviewing ? '公司共享審核' : '我的推薦案例庫'}>
    <header><div><Database size={19} /><strong>{reviewing ? '公司共享審核' : embedded ? '私人客戶與案例集' : '我的推薦案例庫'}</strong></div>{!embedded && <button aria-label="關閉案例庫" onClick={onClose}><X size={18} /></button>}</header>
    {!reviewing && <><p className="case-library-note">工程圖成果自動保存。只有你確認並加入的新版尺寸案例才會進入此推薦案例庫；既有圖面與舊個人紀錄不會被刪除或自動轉換。</p>
    <nav>{([['collections', '案例集與客戶'], ['artifacts', '從成果加入'], ['cases', '已加入案例']] as const).map(([id, label]) => <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>{label}</button>)}</nav></>}
    {message && <div className="case-library-message" role="status">{message}</div>}
    <div className="case-library-content">
      {tab === 'collections' && <>
        <h3>私人客戶</h3><p className="case-library-note">僅自己可見，不會自動連結或公開至公司客戶。</p>
        <form onSubmit={event => { event.preventDefault(); execute(async () => { const response = await axios.post(`${API}/api/case-library/customers`, { name: customerName }); setCustomerName(''); setCustomerLink(`private:${response.data.id}`); }, '私人客戶已建立'); }}>
          <input aria-label="私人客戶名稱" value={customerName} onChange={event => setCustomerName(event.target.value)} placeholder="私人客戶名稱" required maxLength={160} /><button disabled={busy}>建立私人客戶</button>
        </form>
        <div className="case-library-chips">{data.customers.map((item: any) => <span key={item.id}>{item.name}</span>)}</div>
        <h3>建立案例集</h3><form className="case-library-collection-form" onSubmit={event => { event.preventDefault(); execute(async () => {
          const [kind, id] = customerLink.split(':');
          await axios.post(`${API}/api/case-library/collections`, { name: collectionName, private_customer_id: kind === 'private' ? id : null, customer_tag_id: kind === 'global' ? id : null }); setCollectionName('');
        }, '案例集已建立'); }}>
          <label>案例集名稱<input aria-label="案例集名稱" value={collectionName} onChange={event => setCollectionName(event.target.value)} placeholder="案例集／專案名稱" required maxLength={160} /></label>
          <label>指定客戶<select aria-label="案例集所屬客戶" value={customerLink} onChange={event => setCustomerLink(event.target.value)}><option value="">不指定客戶</option>
            <optgroup label="我的私人客戶">{data.customers.map((item: any) => <option key={item.id} value={`private:${item.id}`}>{item.name}</option>)}</optgroup>
            <optgroup label="公司全局客戶分類">{data.tags.filter((item: any) => item.dimension === 'CUSTOMER').map((item: any) => <option key={item.id} value={`global:${item.id}`}>{item.name}</option>)}</optgroup>
          </select></label><button disabled={busy}><FolderPlus size={15} />建立案例集</button>
        </form>
        {!data.customers.length && !data.tags.some((item: any) => item.dimension === 'CUSTOMER') && <p className="case-library-note">尚無可選客戶。請先在上方建立私人客戶，或選擇不指定客戶；公司客戶分類由管理員建立。</p>}
        {data.collections.map((item: any) => <article key={item.id}><div><strong>{item.name}</strong><small>{item.case_count} 個尺寸案例 · {item.private_customer_id ? '私人客戶' : item.customer_tag_id ? '引用全局客戶分類，資料仍私人' : '私人案例集'}</small></div>
          <button disabled={busy} onClick={() => execute(() => axios.patch(`${API}/api/case-library/collections/${item.id}`, { is_active: false }), '案例集已封存，原始成果保留')}><Archive size={14} />封存</button></article>)}
      </>}
      {tab === 'artifacts' && <>
        <h3>從已保存成果加入推薦案例</h3>
        <select aria-label="選擇產圖成果" value={artifactId} onChange={event => setArtifactId(event.target.value)}><option value="">請選擇產圖成果</option>
          {data.artifacts.map((item: any) => <option key={item.id} value={item.id}>{item.title || item.part_id} · {new Date(item.created_at).toLocaleString()} {item.can_curate ? '' : '（需新版重新產圖）'}</option>)}
        </select>
        {artifact && <>
          {!artifact.can_curate && <p className="case-library-warning">此成果缺少新版特徵與來源版本證據，可保留查看；加入推薦前請以新版重新產圖。</p>}
          <div className="case-library-list">{artifact.features.map((item: any) => <label key={item.rule_id || item.id}><input type="checkbox" disabled={!artifact.can_curate || busy} checked={ruleIds.includes(item.rule_id || item.id)} onChange={() => setRuleIds(toggle(ruleIds, item.rule_id || item.id))} />
            <span>{item.name || item.rule_id || item.id}<small>{item.nominal_value} · {item.measurement_role || ''} · {item.tolerance || item.tolerance_config?.fit_class || item.tolerance_config?.mode || '未設定公差'}</small></span></label>)}</div>
          <h4>加入哪些案例集</h4><div className="case-library-list">{data.collections.map((item: any) => <label key={item.id}><input type="checkbox" checked={collectionIds.includes(item.id)} onChange={() => setCollectionIds(toggle(collectionIds, item.id))} />{item.name}</label>)}</div>
          {!data.collections.length && <p>請先到「案例集與客戶」建立自己的案例集。</p>}
          <p className="case-library-note">同一來源、尺寸與公差重複加入會沿用同一筆案例。不同公差保留為不同版本，不會重複投票。</p>
          <label className="case-library-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我已核對選取尺寸與圖面公差，同意作為未來推薦依據。</label>
          <button className="case-library-primary" disabled={busy || !artifact.can_curate || !confirmed || !ruleIds.length || !collectionIds.length}
            onClick={() => execute(() => axios.post(`${API}/api/case-library/artifacts/${artifactId}/cases`, { confirmed, rule_ids: ruleIds, collection_ids: collectionIds }), '已加入推薦案例庫；重複尺寸沿用原案例')}><Check size={15} />{busy ? '重新驗證新版幾何…' : '驗證並加入我的案例庫'}</button>
        </>}
      </>}
      {tab === 'cases' && <>
        <h3>已明確加入的新版尺寸案例</h3><p className="case-library-note">每筆案例沿用歷史 ToleranceCase 格式。移出推薦不會刪除原始工程圖或已核准的共享快照。</p>
        <a className="case-library-link" href={`${API}/api/case-library/export`}><ExternalLink size={14} />匯出為歷史案例庫相容 JSON</a>
        {!data.cases.length && <p>尚未加入推薦案例。</p>}
        {data.cases.map((item: any) => <article key={item.id}><div><strong>{item.case.source_metadata.part_id} · {item.case.source_metadata.measurement_role}</strong>
          <small>{JSON.stringify(item.case.nominal_dimensions)} · {JSON.stringify(item.case.tolerance_config)}</small>
          <small>{item.collection_ids.map((id: string) => data.collections.find((collection: any) => collection.id === id)?.name || id).join('、')}</small></div>
          <div className="case-library-actions"><a href={`${API}/api/case-library/sources/${item.case.case_id}/pdf`} target="_blank" rel="noreferrer">來源圖面</a>
          <button disabled={busy} onClick={() => execute(() => axios.post(`${API}/api/case-library/cases/${item.id}/share`), '已送出共享審核；核准前仍只有自己可用')}>申請公司共享</button>
          <button disabled={busy} onClick={() => execute(() => axios.delete(`${API}/api/case-library/cases/${item.id}`), '案例已移出私人推薦範圍')}>移出推薦</button></div></article>)}
      </>}
      {tab === 'cases' && data.has_more && <button disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          const response = await axios.get(`${API}/api/case-library/cases`, { params: { offset: data.cases.length } });
          setData((previous: any) => ({ ...previous, cases: [...previous.cases, ...response.data.cases], has_more: response.data.has_more }));
        } catch (error) { setMessage(errorMessage(error)); } finally { setBusy(false); }
      }}>載入更多案例</button>}
      {tab === 'review' && reviewing && <>
        <h3>公司共享申請</h3><p className="case-library-warning">核准會公開此尺寸案例及其來源圖面。私人客戶分類不會自動公開；請明確選擇公司分類標籤。</p>
        <button disabled={busy} onClick={() => execute(async () => { setCompatibility((await axios.get(`${API}/api/case-library/compatibility`)).data); }, '歷史相容性檢查完成，原檔未修改')}>檢查既有歷史庫相容性</button>
        {compatibility && <p>歷史 {compatibility.historical_count} 筆；原可推薦 {compatibility.eligible_before} 筆；新版契約可推薦 {compatibility.eligible_after} 筆。無法確認者僅作待覆核證據，原檔保留。</p>}
        {!data.requests.length && <p>沒有待審核申請。</p>}
        {data.requests.map((item: any) => <article key={item.id}><div><strong>{item.case.source_metadata.part_id} · {item.case.source_metadata.measurement_role}</strong><small>{JSON.stringify(item.case.tolerance_config)}</small>
          <a href={`${API}/api/case-library/sources/${item.case.case_id}/pdf`} target="_blank" rel="noreferrer">原作者來源圖面</a>
          <div className="case-library-chips">{data.tags.map((tag: any) => <label key={tag.id}><input type="checkbox" checked={(reviewTags[item.id] || []).includes(tag.id)} onChange={() => setReviewTags({ ...reviewTags, [item.id]: toggle(reviewTags[item.id] || [], tag.id) })} />{tag.name}</label>)}</div></div>
          <div className="case-library-actions"><button disabled={busy} onClick={() => execute(() => axios.post(`${API}/api/case-library/share-requests/${item.id}/review`, { approve: true, tag_ids: reviewTags[item.id] || [] }), '案例已核准並發布公司共用快照')}>核准發布</button>
          <button disabled={busy} onClick={() => execute(() => axios.post(`${API}/api/case-library/share-requests/${item.id}/review`, { approve: false }), '已拒絕申請，私人案例保留')}>拒絕</button></div></article>)}
      </>}
    </div>
  </section>;
  return embedded ? content : <div className="case-library-backdrop">{content}</div>;
}
