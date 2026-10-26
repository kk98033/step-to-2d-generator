import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import AdminAccountPage from './AdminAccountPage.tsx'
import AdminOperationReplayPage from './AdminOperationReplayPage.tsx'
import AdminApiGraphPage from './AdminApiGraphPage.tsx'

const RootComponent = window.location.pathname.startsWith('/admin/operations/')
  ? AdminOperationReplayPage
  : window.location.pathname === '/admin/api-graph'
    ? AdminApiGraphPage
  : window.location.pathname === '/admin/accounts'
    ? AdminAccountPage
    : App

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootComponent />
  </StrictMode>,
)
