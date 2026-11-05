import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './authSession'
import PresenceHeartbeat from './PresenceHeartbeat.tsx'

const App = lazy(() => import('./App.tsx'))
const AdminCenter = lazy(() => import('./AdminCenter.tsx'))
const RootComponent = window.location.pathname === '/admin' || window.location.pathname.startsWith('/admin/') ? AdminCenter : App

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PresenceHeartbeat />
    <Suspense fallback={<div style={{ padding: 24, color: '#ddd', background: '#171717', height: '100vh' }}>正在載入系統…</div>}><RootComponent /></Suspense>
  </StrictMode>,
)
