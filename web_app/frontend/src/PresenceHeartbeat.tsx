import { useEffect } from 'react';
import { hasAuthenticatedSession, redirectToLogin } from './authSession';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const HEARTBEAT_MS = 30_000;
const IDLE_MS = 5 * 60_000;

export default function PresenceHeartbeat() {
  useEffect(() => {
    let lastActivity = Date.now();
    let stopped = false;
    const markActivity = () => { lastActivity = Date.now(); };
    const send = (keepalive = false) => {
      if (stopped) return;
      void fetch(`${API_BASE}/api/presence/heartbeat`, {
        method: 'POST',
        credentials: 'include',
        keepalive,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          page: window.location.pathname,
          idle: document.hidden || Date.now() - lastActivity >= IDLE_MS,
        }),
      }).then(response => {
        if (response.status === 401 && hasAuthenticatedSession()) redirectToLogin();
      }).catch(() => undefined);
    };
    const events: Array<keyof WindowEventMap> = ['pointerdown', 'keydown', 'wheel', 'touchstart'];
    events.forEach(event => window.addEventListener(event, markActivity, { passive: true }));
    const visibility = () => { if (!document.hidden) markActivity(); send(); };
    const pageHide = () => send(true);
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', pageHide);
    send();
    const timer = window.setInterval(() => send(), HEARTBEAT_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      events.forEach(event => window.removeEventListener(event, markActivity));
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', pageHide);
    };
  }, []);
  return null;
}
