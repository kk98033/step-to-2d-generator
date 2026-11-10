import type { CSSProperties } from 'react';

type Source = 'COMPANY' | 'PERSONAL' | 'BOTH';
type Props = {
  source: Source; tags: any[]; customers: any[]; collections: any[];
  tagIds: Set<string>; customerId: string; collectionIds: string[]; match: 'ANY' | 'ALL';
  onSource: (value: Source) => void; onTags: (value: Set<string>) => void;
  onCustomer: (value: string) => void; onCollections: (value: string[]) => void;
  onMatch: (value: 'ANY' | 'ALL') => void; onManage: () => void;
};
const field: CSSProperties = { width: '100%', background: '#262626', color: '#ddd', border: '1px solid #404040', borderRadius: 3, padding: 6, fontSize: 11 };
const note: CSSProperties = { fontSize: 10, color: '#a3a3a3', lineHeight: 1.6, margin: '5px 0' };
const dimensions: Record<string, string> = { CUSTOMER: '公司客戶', PROJECT: '專案', PART_TYPE: '零件類型', MATERIAL: '材料', PROCESS: '製程', ENGINEER: '工程師', CUSTOM: '其他分類' };

export default function RecommendationScopePanel(props: Props) {
  const company = props.source !== 'PERSONAL', personal = props.source !== 'COMPANY';
  const groups = Object.entries(dimensions).map(([dimension, title]) => ({ title, tags: props.tags.filter(tag => tag.dimension === dimension) })).filter(group => group.tags.length);
  const collections = props.collections.filter(collection => !props.customerId || collection.private_customer_id === props.customerId);
  const toggleSource = (scope: 'COMPANY' | 'PERSONAL', checked: boolean) => {
    const nextCompany = scope === 'COMPANY' ? checked : company;
    const nextPersonal = scope === 'PERSONAL' ? checked : personal;
    if (nextCompany || nextPersonal) props.onSource(nextCompany && nextPersonal ? 'BOTH' : nextCompany ? 'COMPANY' : 'PERSONAL');
  };
  return <section aria-label="推薦資料範圍" style={{ border: '1px solid #333', background: '#171717', padding: 10, borderRadius: 4 }}>
    <strong style={{ fontSize: 11 }}>推薦資料範圍</strong>
    <p style={note}>勾選要查詢的來源，再選分類；可同時搜尋兩邊。</p>
    <div style={{ borderTop: '1px solid #333', paddingTop: 8 }}>
      <label style={{ fontSize: 11 }}><input aria-label="搜尋公司案例" type="checkbox" checked={company} disabled={company && !personal} onChange={event => toggleSource('COMPANY', event.target.checked)} />公司主資料庫</label>
      <p style={note}>歷史案例與已核准的共享案例。</p>
      {company && <details><summary style={{ fontSize: 10, cursor: 'pointer' }}>公司案例分類{props.tagIds.size ? `（已選 ${props.tagIds.size} 項）` : '（全部）'}</summary>
        {!groups.length && <p style={note}>尚無公司分類，搜尋全部可用公司案例。</p>}
        {groups.map(group => <div key={group.title} style={{ marginTop: 8 }}><div style={{ fontSize: 10, color: '#a3a3a3', marginBottom: 4 }}>{group.title}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>{group.tags.map(tag => <label key={tag.id} style={{ fontSize: 10 }}><input aria-label={`公司分類 ${tag.name}`} type="checkbox" checked={props.tagIds.has(tag.id)} onChange={event => { const next = new Set(props.tagIds); event.target.checked ? next.add(tag.id) : next.delete(tag.id); props.onTags(next); }} />{tag.name}</label>)}</div>
        </div>)}
        {!!groups.length && <select aria-label="公司分類符合方式" value={props.match} onChange={event => props.onMatch(event.target.value as 'ANY' | 'ALL')} style={{ ...field, marginTop: 8 }}><option value="ANY">符合任一選取分類</option><option value="ALL">符合全部選取分類</option></select>}
        <p style={note}>未選分類時搜尋全部公司案例；這些分類只限制公司來源。</p>
      </details>}
    </div>
    <div style={{ borderTop: '1px solid #333', paddingTop: 8, marginTop: 10 }}>
      <label style={{ fontSize: 11 }}><input aria-label="搜尋我的案例" type="checkbox" checked={personal} disabled={personal && !company} onChange={event => toggleSource('PERSONAL', event.target.checked)} />我的案例庫</label>
      <p style={note}>自己確認加入的案例，依私人客戶與案例集分類。</p>
      <select aria-label="推薦私人客戶" value={props.customerId} onChange={event => { props.onCustomer(event.target.value); props.onCollections([]); if (!personal) props.onSource('PERSONAL'); }} style={field}>
        <option value="">全部私人客戶與未指定客戶</option>{props.customers.map(customer => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
      </select>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 7 }}>{collections.map(collection => <label key={collection.id} style={{ fontSize: 10 }}><input aria-label={`推薦案例集 ${collection.name}`} type="checkbox" checked={personal && props.collectionIds.includes(collection.id)} onChange={event => { const previous = personal ? props.collectionIds : []; props.onCollections(event.target.checked ? [...previous, collection.id] : previous.filter(id => id !== collection.id)); if (!personal) props.onSource('PERSONAL'); }} />{collection.name}（{collection.case_count || 0} 筆）</label>)}</div>
      {!collections.length && <p style={note}>此分類尚無案例集，請先建立案例集並加入成果。</p>}
      <p style={note}>{personal ? '未勾案例集時搜尋所選客戶的全部已加入案例；空分類不擴大搜尋。' : '選擇客戶或案例集會切換至我的案例庫。'}</p>
      <button type="button" onClick={props.onManage} style={{ ...field, width: 'auto', cursor: 'pointer' }}>管理私人客戶與案例集</button>
    </div>
    <p role="status" style={{ ...note, marginBottom: 0 }}>目前搜尋：{company && personal ? '公司與我的案例' : company ? '公司案例' : '我的案例'}。至少選擇一個來源。</p>
  </section>;
}
