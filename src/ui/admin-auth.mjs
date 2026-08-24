const ADMIN_TOKEN_KEY = 'tt-admin-token';
const ADMIN_TOKEN_URL_PARAM = 'admin_token';

export function readAdminToken() {
  try {
    return sessionStorage.getItem(ADMIN_TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

export function storeAdminToken(value) {
  try {
    sessionStorage.setItem(ADMIN_TOKEN_KEY, String(value || '').trim());
  } catch {
    /* ignore */
  }
}

export function adminRequestHeaders(extra = {}) {
  const token = readAdminToken();
  if (!token) return { ...extra };
  return { ...extra, 'X-Admin-Token': token };
}

/** Store token from ?admin_token=… and remove it from the visible URL. */
export function applyAdminTokenFromUrl() {
  try {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get(ADMIN_TOKEN_URL_PARAM);
    if (!fromUrl) return false;
    storeAdminToken(fromUrl);
    params.delete(ADMIN_TOKEN_URL_PARAM);
    const query = params.toString();
    const next = `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`;
    window.history.replaceState(null, '', next);
    return true;
  } catch {
    return false;
  }
}

export function bindAdminTokenInput(input) {
  if (!input) return;
  const persist = () => storeAdminToken(input.value);
  input.addEventListener('input', persist);
  input.addEventListener('change', persist);
  input.addEventListener('blur', persist);
}
