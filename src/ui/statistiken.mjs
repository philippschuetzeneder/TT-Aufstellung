import { bindHeaderLeague, bindHeaderRefresh, escapeHtml, fetchRefreshApi, readStoredLeague, renderHeaderLeague, renderRefreshReport, storeLeague } from './header.mjs';

const app = document.querySelector('#app');
const DESKTOP_VIEW_KEY = 'tt-statistiken-desktop-view';
const state = {
  leagues: [], league: '', players: [], search: '', team: '',
  sort: 'rc_rating', direction: 'desc',
  profile: null,
  dataRefreshRunning: false,
  forceDesktopView: false,
};

function readDesktopViewPreference() {
  try {
    return localStorage.getItem(DESKTOP_VIEW_KEY) === '1';
  } catch {
    return false;
  }
}

function storeDesktopViewPreference(value) {
  try {
    localStorage.setItem(DESKTOP_VIEW_KEY, value ? '1' : '0');
  } catch {
    /* ignore */
  }
}

state.forceDesktopView = readDesktopViewPreference();

async function api(path, { timeoutMs = 60000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { signal: controller.signal });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'API-Fehler');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('Zeitüberschreitung — Server antwortet nicht. Bitte kurz warten und erneut versuchen.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function syncHeader() {
  renderHeaderLeague(state.leagues, state.league, {
    backHref: '/',
    backLabel: 'Zurück',
    showDataRefresh: true,
    dataRefreshRunning: state.dataRefreshRunning,
  });
  bindHeaderLeague(loadLeague);
  bindHeaderRefresh(runDataRefresh);
}

async function runDataRefresh() {
  if (state.dataRefreshRunning) return;
  const confirmed = window.confirm(
    'Daten-Refresh starten? Sucht neue XTTV-Spielberichte und aktualisiert RC nur bei neuen Daten. In der Sommerpause meist schnell.',
  );
  if (!confirmed) return;
  state.dataRefreshRunning = true;
  syncHeader();
  app.innerHTML = '<section class="card"><p class="muted">Daten-Refresh läuft … Bitte warten.</p></section>';
  try {
    const data = await fetchRefreshApi(true);
    state.dataRefreshRunning = false;
    syncHeader();
    renderRefreshReport(app, data, {
      onBack: () => {
        window.location.href = '/';
      },
    });
  } catch (error) {
    state.dataRefreshRunning = false;
    syncHeader();
    if (String(error?.message || '').includes('Failed to fetch') || error?.name === 'TypeError') {
      renderRefreshReport(app, {
        ok: false,
        error: 'Server-Neustart — bitte in ein paar Sekunden erneut zur Hauptseite wechseln.',
      }, {
        onBack: () => { window.location.href = '/'; },
      });
      return;
    }
    renderRefreshReport(app, {
      ok: false,
      error: error.message,
    }, {
      onBack: () => {
        window.location.href = '/';
      },
    });
  }
}

async function loadLeague(league) {
  state.league = league;
  state.search = '';
  state.team = '';
  state.profile = null;
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

async function loadProfile(playerId, opponentId = '') {
  const cached = state.players.find((p) => p.id === playerId);
  app.innerHTML = `<section class="card"><p class="muted">Lade Spielerprofil …</p>${cached ? `<p><strong>${escapeHtml(cached.name)}</strong></p>` : ''}</section>`;
  try {
    state.profile = await api(`/api/analytics/player-profile?league=${encodeURIComponent(state.league)}&player_id=${encodeURIComponent(playerId)}${opponentId ? `&opponent_id=${encodeURIComponent(opponentId)}` : ''}`);
    renderProfile(state.profile);
  } catch (error) {
    app.innerHTML = `<section class="card"><div class="empty error-box">${escapeHtml(error.message)}</div></section>`;
  }
}

function display(value, suffix = '') {
  return value == null || Number.isNaN(Number(value)) ? '-' : `${escapeHtml(value)}${suffix}`;
}

function trend(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  // Keep a small but real directional signal visible. Very small trends
  // must not become an apparently neutral zero in the statistics view.
  const numeric = Number(value);
  const rounded = Math.round(numeric);
  const amount = rounded === 0 && numeric !== 0 ? Math.sign(numeric) : rounded;
  const icon = amount > 0 ? '↑' : amount < 0 ? '↓' : '→';
  const className = amount > 0 ? 'trend-up' : amount < 0 ? 'trend-down' : 'trend-flat';
  return `<span class="${className}">${icon} ${amount > 0 ? '+' : ''}${escapeHtml(amount)}</span>`;
}

function filteredPlayers() {
  const query = state.search.trim().toLocaleLowerCase('de');
  return state.players.filter((player) => {
    const team = String(player.team || '');
    const matchesTeam = !state.team || team === state.team;
    const matchesSearch = !query
      || `${player.name || ''} ${team}`.toLocaleLowerCase('de').includes(query);
    return matchesTeam && matchesSearch;
  });
}

function sortedPlayers() {
  const numeric = new Set(['rc_rating', 'rc_trend', 'home_strength', 'away_strength', 'games', 'wins']);
  return filteredPlayers().sort((a, b) => {
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

function teamOptions() {
  return [...new Set(state.players.map((player) => player.team).filter(Boolean))]
    .sort((a, b) => String(a).localeCompare(String(b), 'de'));
}

function header(label, key) {
  const active = state.sort === key;
  const arrow = active ? (state.direction === 'desc' ? ' ↓' : ' ↑') : '';
  return `<th scope="col"><button type="button" class="table-sort ${active ? 'active' : ''}" data-sort="${key}">${label}${arrow}</button></th>`;
}

function sortOptionsHtml() {
  const options = [
    ['rc_rating', 'RC'],
    ['rc_trend', 'RC-Trend (letzte 10 Spiele)'],
    ['home_strength', 'Heimstärke'],
    ['away_strength', 'Auswärtsstärke'],
    ['games', 'Spiele'],
    ['wins', 'Siege'],
    ['name', 'Name'],
    ['team', 'Mannschaft'],
  ];
  return options.map(([key, label]) => `<option value="${key}"${state.sort === key ? ' selected' : ''}>${label}</option>`).join('');
}

function playerMobileCard(player) {
  return `<article class="ranking-mobile-card">
    <button type="button" class="player-link ranking-mobile-name" data-player-id="${escapeHtml(player.id)}">${escapeHtml(player.name || '-')}</button>
    <div class="ranking-mobile-team muted">${escapeHtml(player.team || '-')}</div>
    <dl class="ranking-mobile-stats">
      <div><dt>RC</dt><dd>${display(player.rc_rating != null ? Math.round(player.rc_rating) : null)}</dd></div>
      <div><dt>Trend</dt><dd>${trend(player.rc_trend)}</dd></div>
      <div><dt>Heim</dt><dd>${display(player.home_strength, ' %')}</dd></div>
      <div><dt>Ausw.</dt><dd>${display(player.away_strength, ' %')}</dd></div>
      <div><dt>Spiele</dt><dd>${display(player.games)}</dd></div>
      <div><dt>Siege</dt><dd>${display(player.wins)}</dd></div>
    </dl>
  </article>`;
}

function render(data) {
  const sorted = sortedPlayers();
  const rows = sorted.map((player) => `<tr>
    <th scope="row"><button type="button" class="player-link" data-player-id="${escapeHtml(player.id)}">${escapeHtml(player.name || '-')}</button></th>
    <td>${escapeHtml(player.team || '-')}</td>
    <td class="number">${display(player.rc_rating != null ? Math.round(player.rc_rating) : null)}</td>
    <td class="number">${trend(player.rc_trend)}</td>
    <td class="number">${display(player.home_strength, ' %')}</td>
    <td class="number">${display(player.away_strength, ' %')}</td>
    <td class="number">${display(player.games)}</td>
    <td class="number">${display(player.wins)}</td>
  </tr>`).join('');
  const mobileCards = sorted.map((player) => playerMobileCard(player)).join('');
  const teams = teamOptions().map((team) => `<option value="${escapeHtml(team)}"${state.team === team ? ' selected' : ''}>${escapeHtml(team)}</option>`).join('');
  const visibleCount = filteredPlayers().length;
  const desktopToggle = state.forceDesktopView
    ? '<button type="button" class="ranking-view-link" data-toggle-desktop-view>Mobile Version</button>'
    : '<button type="button" class="ranking-view-link" data-toggle-desktop-view>Desktop Version</button>';
  app.innerHTML = `<section class="card ranking-card${state.forceDesktopView ? ' ranking-force-desktop' : ''}">
    <div class="ranking-heading"><div><div class="ranking-title-row"><h2>Spieler-Rangliste</h2>${desktopToggle}</div><p class="muted ranking-meta">${escapeHtml(data.latest_league || state.league)} · ${visibleCount} von ${data.count} Spielern</p></div></div>
    <div class="ranking-filters">
      <label>Spieler suchen<input type="search" data-player-search value="${escapeHtml(state.search)}" placeholder="Name oder Verein/Mannschaft"></label>
      <label>Mannschaft/Verein<select data-team-filter><option value="">Alle Mannschaften/Vereine</option>${teams}</select></label>
    </div>
    ${rows ? `<div class="ranking-desktop"><div class="table-scroll"><table class="ranking-table"><thead><tr>${header('Spieler', 'name')}${header('Verein / Mannschaft', 'team')}${header('Aktueller RC', 'rc_rating')}${header('RC-Trend (letzte 10 Spiele)', 'rc_trend')}${header('Heimstärke', 'home_strength')}${header('Auswärtsstärke', 'away_strength')}${header('Spiele', 'games')}${header('Siege', 'wins')}</tr></thead><tbody>${rows}</tbody></table></div></div><div class="ranking-mobile"><label class="ranking-mobile-sort">Sortierung<select data-mobile-sort>${sortOptionsHtml()}</select><select data-mobile-direction><option value="desc"${state.direction === 'desc' ? ' selected' : ''}>Absteigend</option><option value="asc"${state.direction === 'asc' ? ' selected' : ''}>Aufsteigend</option></select></label><div class="ranking-mobile-list">${mobileCards}</div></div>` : '<div class="empty">Keine Spieler für diese Liga gefunden.</div>'}
    <p class="muted ranking-note">RC-Trend (letzte 10 Spiele) = robuste mittlere RC-Veränderung der letzten bis zu 10 RC-Snapshots; einzelne Ausreißer dominieren nicht. Wertebereich: −100 bis +100 RC-Punkte. Stärke = geglättete Einzel-Siegquote aus den verfügbaren Ligaspielen. Fehlende Werte werden als „-“ angezeigt.</p>
  </section>`;
  app.querySelector('[data-toggle-desktop-view]')?.addEventListener('click', () => {
    state.forceDesktopView = !state.forceDesktopView;
    storeDesktopViewPreference(state.forceDesktopView);
    render(data);
  });
  app.querySelector('[data-player-search]').addEventListener('input', (event) => {
    state.search = event.target.value;
    render(data);
    const input = app.querySelector('[data-player-search]');
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
  app.querySelector('[data-team-filter]').addEventListener('change', (event) => {
    state.team = event.target.value;
    render(data);
  });
  app.querySelectorAll('[data-sort]').forEach((button) => button.addEventListener('click', () => {
    const key = button.dataset.sort;
    if (state.sort === key) state.direction = state.direction === 'desc' ? 'asc' : 'desc';
    else { state.sort = key; state.direction = 'desc'; }
    render(data);
  }));
  const mobileSort = app.querySelector('[data-mobile-sort]');
  const mobileDir = app.querySelector('[data-mobile-direction]');
  if (mobileSort) {
    mobileSort.addEventListener('change', (event) => {
      state.sort = event.target.value;
      render(data);
    });
  }
  if (mobileDir) {
    mobileDir.addEventListener('change', (event) => {
      state.direction = event.target.value;
      render(data);
    });
  }
  app.querySelectorAll('[data-player-id]').forEach((button) => button.addEventListener('click', () => loadProfile(button.dataset.playerId)));
}

function profileValue(value, suffix = '') {
  return value == null || Number.isNaN(Number(value)) ? '-' : `${escapeHtml(value)}${suffix}`;
}

function statCard(label, value) {
  return `<div class="profile-stat"><span class="muted">${escapeHtml(label)}</span><strong>${value}</strong></div>`;
}

function historySvg(history) {
  const values = (history || []).map((item) => Number(item.rc_rating)).filter(Number.isFinite);
  if (values.length < 2) return '<p class="muted">Keine ausreichenden RC-Snapshots vorhanden.</p>';
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const points = values.map((value, index) => `${(index / (values.length - 1) * 100).toFixed(1)},${(38 - ((value - min) / range) * 32).toFixed(1)}`).join(' ');
  return `<svg class="rc-chart" viewBox="0 0 100 42" role="img" aria-label="RC-Verlauf"><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg>`;
}

function formatProfileDate(iso) {
  if (!iso) return '-';
  const parts = String(iso).split('-');
  if (parts.length !== 3) return escapeHtml(iso);
  return `${parts[2]}.${parts[1]}.${parts[0]}`;
}

function resultBadge(row) {
  const label = row.win ? 'Sieg' : row.draw ? 'Unentschieden' : 'Niederlage';
  const cls = row.win ? 'result-win' : row.draw ? 'result-draw' : 'result-loss';
  return `<span class="result-badge ${cls}">${label}</span>`;
}

function formGamesMobile(games) {
  if (!games?.length) return '<p class="muted">Keine gültigen Spiele vorhanden.</p>';
  return `<div class="form-games-mobile">${games.map((row) => `<article class="form-game-card">
    <div class="form-game-head"><strong>${escapeHtml(row.opponent || '-')}</strong>${resultBadge(row)}</div>
    <div class="form-game-meta muted">${formatProfileDate(row.date)} · ${row.side === 'home' ? 'Heim' : 'Auswärts'} · <span class="score-cell">${profileValue(row.own_score)}:${profileValue(row.opp_score)}</span></div>
  </article>`).join('')}</div>`;
}

function formGamesTable(games) {
  if (!games?.length) return '<p class="muted">Keine gültigen Spiele vorhanden.</p>';
  const rows = games.map((row) => `<tr>
    <td>${formatProfileDate(row.date)}</td>
    <td>${escapeHtml(row.opponent || '-')}</td>
    <td>${resultBadge(row)}</td>
    <td class="number score-cell">${profileValue(row.own_score)}:${profileValue(row.opp_score)}</td>
    <td>${row.side === 'home' ? 'Heim' : 'Auswärts'}</td>
  </tr>`).join('');
  return `<div class="form-games-desktop"><div class="table-scroll"><table class="form-games-table"><thead><tr><th>Datum</th><th>Gegner</th><th>Ergebnis</th><th>Score</th><th>Ort</th></tr></thead><tbody>${rows}</tbody></table></div></div><div class="form-games-mobile-wrap">${formGamesMobile(games)}</div>`;
}

function leagueHistoryList(leagues) {
  if (!leagues?.length) return '<p class="muted">Keine Ligadaten in den letzten 3 Jahren.</p>';
  return `<ul class="profile-league-list">${leagues.map((entry) => {
    if (typeof entry === 'string') return `<li><span>${escapeHtml(entry)}</span></li>`;
    return `<li><strong>${escapeHtml(entry.season || '-')}</strong><span>${escapeHtml(entry.name || '')}</span></li>`;
  }).join('')}</ul>`;
}

function matchupList(rows, tone = 'difficult') {
  const linkClass = tone === 'best' ? 'matchup-opponent-link-best' : 'matchup-opponent-link-difficult';
  return rows?.length
    ? rows.map((row) => `<li><button type="button" class="player-link ${linkClass}" data-opponent-id="${escapeHtml(row.opponent_id)}">${escapeHtml(row.opponent)}</button><span>${profileValue(row.matches)} Spiele · ${profileValue(row.wins)}:${profileValue(row.losses)} · ${profileValue(row.win_rate == null ? null : Math.round(row.win_rate * 100), ' %')}</span></li>`).join('')
    : '<li class="muted">Keine passenden Matchups vorhanden.</li>';
}

function renderProfile(data) {
  const player = data.player;
  const form = data.form || {};
  const detail = data.opponent_detail;
  const resultList = detail?.results?.length
    ? formGamesTable(detail.results)
    : '';
  const seasonStats = data.current_season || {};
  app.innerHTML = `<section class="card profile-card">
    <div class="profile-heading"><div><button type="button" class="ranking-back" data-back>← Rangliste</button><h2>${escapeHtml(player.name || '-')}</h2><p class="muted">${escapeHtml(player.team || '-')} · ${escapeHtml(data.latest_league || state.league)}</p></div></div>
    <div class="profile-stats">${statCard('Aktueller RC', profileValue(player.rc_rating == null ? null : Math.round(player.rc_rating)))}${statCard('RC-Trend', trend(player.rc_trend))}${statCard('Rang in der Liga', profileValue(player.rank))}</div>
    <h3>Grundstatistik</h3><div class="profile-stats">${statCard('Spiele', profileValue(player.matches))}${statCard('Siege', profileValue(player.wins))}${statCard('Niederlagen', profileValue(player.losses))}${statCard('Unentschieden', profileValue(player.draws))}${statCard('Siegquote', profileValue(player.win_rate == null ? null : Math.round(player.win_rate * 100), ' %'))}</div>
    <div class="profile-season-block">
      <h3>Aktuelle Saison ${escapeHtml(data.season || '-')}</h3>
      <div class="profile-stats">${statCard('Spiele', profileValue(seasonStats.games))}${statCard('Siege', profileValue(seasonStats.wins))}${statCard('Niederlagen', profileValue(seasonStats.losses))}${statCard('Siegquote', profileValue(seasonStats.win_rate == null ? null : Math.round(seasonStats.win_rate * 100), ' %'))}</div>
    </div>
    <div class="profile-season-block">
      <h3>Ligen der letzten 3 Jahre</h3>
      ${leagueHistoryList(data.leagues_last_3_years)}
    </div>
    <h3>Heim / Auswärts</h3><div class="profile-stats">${['home','away'].map((side) => { const value = data.home_away?.[side] || {}; return statCard(side === 'home' ? 'Heimspiele' : 'Auswärtsspiele', profileValue(value.games)) + statCard(`${side === 'home' ? 'Heim' : 'Auswärts'}-Siege`, profileValue(value.wins)) + statCard('Quote', profileValue(value.win_rate == null ? null : Math.round(value.win_rate * 100), ' %')) + statCard('Stärke', profileValue(value.strength, ' %')); }).join('')}</div>
    <h3>Form und Entwicklung</h3><div class="rc-chart-wrap">${historySvg(data.rc_history)}<span class="muted">Trend: ${trend(player.rc_trend)}</span></div>
    <div class="profile-stats">${statCard('Letzte 5 Spiele', `${profileValue(form.last_5?.wins)} / ${profileValue(form.last_5?.games)}`)}${statCard('Quote letzte 5', profileValue(form.last_5?.win_rate == null ? null : Math.round(form.last_5.win_rate * 100), ' %'))}${statCard('Letzte 10 Spiele', `${profileValue(form.last_10?.wins)} / ${profileValue(form.last_10?.games)}`)}${statCard('Quote letzte 10', profileValue(form.last_10?.win_rate == null ? null : Math.round(form.last_10.win_rate * 100), ' %'))}</div>
    <h4 class="profile-subheading">Letzte Spiele</h4>
    ${formGamesTable(form.games)}
    <h3>Matchups <span class="muted">(mindestens 3 Begegnungen)</span></h3>
    ${detail ? `<div class="matchup-detail"><button type="button" class="ranking-back" data-back>← Matchups</button><h4>Gegen ${escapeHtml(detail.opponent)}</h4><p>${profileValue(detail.matches)} Spiele · ${profileValue(detail.wins)} Siege · ${profileValue(detail.losses)} Niederlagen · ${profileValue(detail.win_rate == null ? null : Math.round(detail.win_rate * 100), ' %')}</p>${resultList}</div>` : `<div class="matchup-columns"><div><h4>Beste Matchups</h4><ul>${matchupList(data.matchups.best, 'best')}</ul></div><div><h4>Schwierige Matchups</h4><ul>${matchupList(data.matchups.difficult, 'difficult')}</ul></div></div>`}
  </section>`;
  app.querySelectorAll('[data-back]').forEach((button) => button.addEventListener('click', () => {
    if (detail) loadProfile(player.id);
    else { state.profile = null; render({ players: state.players, latest_league: data.latest_league, count: state.players.length }); }
  }));
  app.querySelectorAll('[data-opponent-id]').forEach((button) => button.addEventListener('click', () => loadProfile(player.id, button.dataset.opponentId)));
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
