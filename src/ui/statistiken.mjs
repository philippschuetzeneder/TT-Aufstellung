import { bindHeaderLeague, escapeHtml, readStoredLeague, renderHeaderLeague, storeLeague } from './header.mjs';

const app = document.querySelector('#app');
const state = { leagues: [], league: '', players: [], sort: 'rc_rating', direction: 'desc' };

async function api(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || 'API-Fehler');
  return data;
}

function syncHeader() {
  renderHeaderLeague(state.leagues, state.league);
  bindHeaderLeague(loadLeague);
}

async function loadLeague(league) {
  state.league = league;
  storeLeague(league);
  syncHeader();
  app.innerHTML = '<section class="card"><p class="muted">Lade Spieler …</p></section>';
  try {
    const data = await api(`/api/analytics/players?league=${encodeURIComponent(league)}`);
    state.players = data.players || [];
    render(data);
  } catch (error) {
    app.innerHTML = `<section class="card"><div class="empty error-box">${escapeHtml(error.message)}</div></section>`;
  }
}

function display(value, suffix = '') {
  return value == null || Number.isNaN(Number(value)) ? '-' : `${escapeHtml(value)}${suffix}`;
}

function trend(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  const amount = Number(value);
  const icon = amount > 0 ? '↑' : amount < 0 ? '↓' : '→';
  const className = amount > 0 ? 'trend-up' : amount < 0 ? 'trend-down' : 'trend-flat';
  return `<span class="${className}">${icon} ${amount > 0 ? '+' : ''}${escapeHtml(amount)}</span>`;
}

function sortedPlayers() {
  const numeric = new Set(['rc_rating', 'rc_trend', 'home_strength', 'away_strength', 'games', 'wins']);
  return [...state.players].sort((a, b) => {
    const key = state.sort;
    if (numeric.has(key)) {
      const aMissing = a[key] == null;
      const bMissing = b[key] == null;
      if (aMissing !== bMissing) return aMissing ? 1 : -1;
      if (!aMissing && Number(a[key]) !== Number(b[key])) {
        return (Number(b[key]) - Number(a[key])) * (state.direction === 'desc' ? 1 : -1);
      }
    } else {
      const result = String(a[key] || '').localeCompare(String(b[key] || ''), 'de');
      if (result) return result * (state.direction === 'desc' ? 1 : -1);
    }
    return String(a.name || '').localeCompare(String(b.name || ''), 'de');
  });
}

function header(label, key) {
  const active = state.sort === key;
  const arrow = active ? (state.direction === 'desc' ? ' ↓' : ' ↑') : '';
  return `<th scope="col"><button type="button" class="table-sort ${active ? 'active' : ''}" data-sort="${key}">${label}${arrow}</button></th>`;
}

function render(data) {
  const rows = sortedPlayers().map((player) => `<tr>
    <th scope="row">${escapeHtml(player.name || '-')}</th>
    <td>${escapeHtml(player.team || '-')}</td>
    <td class="number">${display(player.rc_rating != null ? Math.round(player.rc_rating) : null)}</td>
    <td class="number">${trend(player.rc_trend)}</td>
    <td class="number">${display(player.home_strength, ' %')}</td>
    <td class="number">${display(player.away_strength, ' %')}</td>
    <td class="number">${display(player.games)}</td>
    <td class="number">${display(player.wins)}</td>
  </tr>`).join('');
  app.innerHTML = `<section class="card ranking-card">
    <div class="ranking-heading"><div><h2>Spieler-Rangliste</h2><p class="muted">${escapeHtml(data.latest_league || state.league)} · ${data.count} Spieler</p></div><a class="header-btn ranking-back" href="/">Zurück</a></div>
    ${rows ? `<div class="table-scroll"><table class="ranking-table"><thead><tr>${header('Spieler', 'name')}${header('Verein / Mannschaft', 'team')}${header('Aktueller RC', 'rc_rating')}${header('RC-Trend', 'rc_trend')}${header('Heimstärke', 'home_strength')}${header('Auswärtsstärke', 'away_strength')}${header('Spiele', 'games')}${header('Siege', 'wins')}</tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty">Keine Spieler für diese Liga gefunden.</div>'}
    <p class="muted ranking-note">Stärke = geglättete Einzel-Siegquote aus den verfügbaren Ligaspielen. Fehlende Werte werden als „-“ angezeigt.</p>
  </section>`;
  app.querySelectorAll('[data-sort]').forEach((button) => button.addEventListener('click', () => {
    const key = button.dataset.sort;
    if (state.sort === key) state.direction = state.direction === 'desc' ? 'asc' : 'desc';
    else { state.sort = key; state.direction = 'desc'; }
    render(data);
  }));
}

async function init() {
  app.innerHTML = '<section class="card"><p class="muted">Lade Ligen …</p></section>';
  try {
    const data = await api('/api/leagues');
    state.leagues = data.leagues || [];
    const stored = readStoredLeague();
    state.league = state.leagues.some((league) => league.id === stored) ? stored : (state.leagues[0]?.id || stored);
    syncHeader();
    await loadLeague(state.league);
  } catch (error) {
    app.innerHTML = `<section class="card"><div class="empty error-box">${escapeHtml(error.message)}</div></section>`;
  }
}

init();
