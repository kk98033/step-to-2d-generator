export const auditActionLabels: Record<string, string> = {
  LOGIN: '登入成功', LOGIN_FAILED: '登入失敗', LOGOUT: '登出', LOGOUT_FAILED: '登出失敗',
  SESSION_EXPIRED: '工作階段到期', AUTH_REJECTED: '登入狀態失效／未登入', ACCESS_DENIED: '權限拒絕',
  PASSWORD_CHANGED: '變更密碼', PASSWORD_CHANGE_FAILED: '變更密碼失敗',
  API_OPERATION: '系統操作', FILE_DOWNLOAD: '下載檔案', MODEL_UPLOAD: '上傳模型',
};

export function requestInfo(details: any) {
  return { ...(details?.request || {}), ...(details || {}) };
}

export function AuditRequestDetails({ details }: { details: any }) {
  const info = requestInfo(details);
  return <div style={{ fontSize: 12, color: '#a3a3a3', overflowWrap: 'anywhere' }}>
    <div>IP：{info.client_ip || info.login_ip || '未記錄（舊紀錄或系統工作）'}
      {info.status_code != null && ` · HTTP ${info.status_code}`}
      {info.duration_ms != null && ` · ${info.duration_ms} ms`}
      {info.result && ` · ${info.result === 'SUCCESS' ? '成功' : '失敗'}`}</div>
    {info.method && <div>{info.method} {info.path}</div>}
    {info.user_agent && <div>瀏覽器：{info.user_agent}</div>}
    {info.request_id && <div>請求編號：{info.request_id}</div>}
    <details style={{ marginTop: 6 }}><summary style={{ cursor: 'pointer', color: '#60a5fa' }}>完整紀錄資訊</summary>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', background: '#111', padding: 10, maxHeight: 260, overflow: 'auto' }}>{JSON.stringify(details || {}, null, 2)}</pre>
    </details>
  </div>;
}
