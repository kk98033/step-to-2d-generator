import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import AdminAccountPage from './AdminAccountPage.tsx'

const RootComponent = window.location.pathname === '/admin/accounts' ? AdminAccountPage : App

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootComponent />
  </StrictMode>,
)
