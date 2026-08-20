const DEFAULT_LEAGUE = '411 RK Linz Umg. / MV Mitte';
export const LEAGUE_STORAGE_KEY = 'tt-aufstellung-league';

export function readStoredLeague() {
  try {
    return localStorage.getItem(LEAGUE_STORAGE_KEY) || DEFAULT_LEAGUE;
  } catch {
    return DEFAULT_LEAGUE;
  }
}

export function storeLeague(value) {
  try {
    localStorage.setItem(LEAGUE_STORAGE_KEY, value);
  } catch {
    /* ignore */
  }
}

export function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function renderHeaderLeague(leagues, league, { disabled = false } = {}) {
  const host = document.querySelector('#header-controls');
  if (!host) return;
  const options = (leagues || []).map(
    (l) => `<option value="${escapeHtml(l.id)}" ${league === l.id ? 'selected' : ''}>${escapeHtml(l.name)}${l.season ? ` ${l.season}` : ''} (${l.match_count})</option>`,
  ).join('');
  host.innerHTML = `
    <label class="header-league">
      <span class="header-league-label">Liga</span>
      <select id="header-league" ${disabled ? 'disabled' : ''}>${options}</select>
    </label>
    <a class="header-btn" href="/statistiken.html">Statistiken</a>
  `;
}

export function bindHeaderLeague(onChange) {
  const select = document.querySelector('#header-league');
  if (!select || typeof onChange !== 'function') return;
  select.addEventListener('change', () => onChange(select.value));
}
