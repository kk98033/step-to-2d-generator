import axios from 'axios';

const PUBLIC_AUTH_PATHS = new Set([
  '/api/auth/config',
  '/api/auth/login',
]);
const AUTHENTICATED_MARKER = 'cad-authenticated-session';

let redirectingToLogin = false;

export function redirectToLogin(): void {
  const params = new URLSearchParams(window.location.search);
  if (window.location.pathname === '/' && params.get('session') === 'expired') return;
  if (redirectingToLogin) return;
  redirectingToLogin = true;
  window.sessionStorage.removeItem(AUTHENTICATED_MARKER);
  window.sessionStorage.setItem('cad-session-expired', '1');
  window.dispatchEvent(new CustomEvent('cad:session-expired'));
  window.location.replace('/?session=expired');
}

export function hasAuthenticatedSession(): boolean {
  return window.sessionStorage.getItem(AUTHENTICATED_MARKER) === '1';
}

function requestPath(urlValue?: string): string {
  if (!urlValue) return '';
  try {
    return new URL(urlValue, window.location.origin).pathname;
  } catch {
    return urlValue.split('?')[0];
  }
}

axios.defaults.withCredentials = true;

axios.interceptors.response.use(
  response => {
    const path = requestPath(response?.config?.url);
    if (
      ((path === '/api/auth/me' || path === '/api/auth/login') && response?.data?.user)
      || (path.startsWith('/api/') && !PUBLIC_AUTH_PATHS.has(path) && path !== '/api/health')
    ) {
      window.sessionStorage.setItem(AUTHENTICATED_MARKER, '1');
    }
    return response;
  },
  error => {
    const status = error?.response?.status;
    const path = requestPath(error?.config?.url);
    if (
      status === 401
      && !PUBLIC_AUTH_PATHS.has(path)
      && (path !== '/api/auth/me' || hasAuthenticatedSession())
    ) {
      redirectToLogin();
    }
    return Promise.reject(error);
  },
);
