import { bindHeaderLeague, escapeHtml, readStoredLeague, renderHeaderLeague, storeLeague } from './header.mjs';

const DEFAULT_LEAGUE = '411 RK Linz Umg. / MV Mitte';
const DEFAULT_OWN_TEAM = 'Tragwein/Kamig 3';

const state = {
  leagues: [],
  league: DEFAULT_LEAGUE,
  teams: [],
  ownTeam: '',
  opponentTeam: '',
  ownIsHome: true,
  useSpieltyp: false,
  ownPlayers: [],
  opponentPlayers: [],
  selectedOwn: [],
  selectedOpp: [],
  result: null,
  loadingPlayers: false,
  analysisLoading: false,
  uiExpanded: {
    why: false,
    moreInfo: false,
    altLineups: false,
    oppLineups: false,
  },
};
const app = document.querySelector('#app');

async function api(path) {
  const r = await fetch(path);
  const d = await r.json();
  if (!r.ok || d.ok === false) throw new Error(d.message || d.error || 'API error');
  return d;
}

function pickOwnTeam(teams) {
  const exact = teams.find((t) => t.id === DEFAULT_OWN_TEAM);
  if (exact) return exact.id;
  const fuzzy = teams.find((t) => /tragwein/i.test(t.name) && /kamig/i.test(t.name) && /3/.test(t.name));
  return fuzzy?.id || teams[0]?.id || '';
}

function pickOpponentTeam(teams, ownId) {
  return teams.find((t) => t.id !== ownId)?.id || ownId || '';
}

function syncHeader() {
  renderHeaderLeague(state.leagues, state.league, { disabled: state.loadingPlayers || state.analysisLoading });
  bindHeaderLeague(onLeagueChange);
}

async function onLeagueChange(leagueId) {
  state.league = leagueId;
  storeLeague(state.league);
  state.result = null;
  syncHeader();
  await loadTeams();
  state.ownTeam = pickOwnTeam(state.teams);
  state.opponentTeam = pickOpponentTeam(state.teams, state.ownTeam);
  await loadTeamPlayers();
  render();
}

async function init() {
  app.innerHTML = '<section class="card"><p class="muted">Lade Daten …</p></section>';
  try {
    const health = await fetch('/api/db/health').then((r) => r.json()).catch(() => ({ ok: false, error: 'Backend nicht erreichbar' }));
    if (!health.ok) {
      app.innerHTML = `<section class="card"><h2>Datenbank nicht erreichbar</h2><p>${escapeHtml(health.error || 'PostgreSQL antwortet nicht.')}</p><p class="muted">Starte Postgres und danach <code>.\\scripts\\start-dev.ps1</code>.</p></section>`;
      return;
    }

    const leagueData = await api('/api/leagues');
    state.leagues = leagueData.leagues || [];
    const stored = readStoredLeague();
    state.league = state.leagues.some((l) => l.id === stored) ? stored : (state.leagues.find((l) => l.id === DEFAULT_LEAGUE)?.id || state.leagues[0]?.id || DEFAULT_LEAGUE);
    storeLeague(state.league);
    syncHeader();

    await loadTeams();
    state.ownTeam = pickOwnTeam(state.teams);
    state.opponentTeam = pickOpponentTeam(state.teams, state.ownTeam);
    await loadTeamPlayers();
    render();
  } catch (e) {
    app.innerHTML = `<section class="card"><h2>Fehler</h2><p>${escapeHtml(e.message)}</p><p class="muted">Prüfe ob der Server läuft: <code>.\\scripts\\start-dev.ps1</code></p></section>`;
  }
}

async function loadTeams() {
  const q = state.league ? `?league=${encodeURIComponent(state.league)}` : '';
  const d = await api(`/api/teams${q}`);
  state.teams = d.teams || [];
}

async function loadTeamPlayers() {
  state.loadingPlayers = true;
  syncHeader();
  const leagueQ = state.league ? '&league=' + encodeURIComponent(state.league) : '';
  try {
    const [own, opp] = await Promise.all([
      api('/api/teams/players?team=' + encodeURIComponent(state.ownTeam) + leagueQ),
      api('/api/teams/players?team=' + encodeURIComponent(state.opponentTeam) + leagueQ),
    ]);
    state.ownPlayers = own.players;
    state.opponentPlayers = opp.players;
    state.selectedOwn = [];
    state.selectedOpp = [];
    state.result = null;
  } finally {
    state.loadingPlayers = false;
    syncHeader();
  }
}

function setupPanelHtml(own, opp, opponents) {
  const analyzeDisabled = state.selectedOwn.length !== 4 || state.analysisLoading || state.loadingPlayers;
  const analyzeLabel = state.analysisLoading ? 'Berechnung läuft …' : 'Optimale Aufstellung berechnen';
  return `<h2>1. Match Setup</h2><div class="setup-team-row">${select('Eigene Mannschaft', 'ownTeam', state.teams)}${venueSelect()}</div>${select('Gegner', 'opponentTeam', opponents)}<label class="option-check"><input type="checkbox" data-field="useSpieltyp" ${state.useSpieltyp ? 'checked' : ''} ${state.loadingPlayers || state.analysisLoading ? 'disabled' : ''}><span>Spielertyp in Gewichtung miteinbeziehen</span></label><p class="muted option-hint">Offensiv/Noppen/Defensiv mit gewichtet (ähnlich Trend).</p><h3>Eigene Spieler <span class="selection-count">${state.selectedOwn.length}/4</span></h3><p class="muted">Wähle genau vier Spieler.</p>${state.loadingPlayers ? '<div class="empty">Spieler werden geladen …</div>' : players(own, state.selectedOwn, 'own')}<button class="primary setup-analyze" data-action="analyze" ${analyzeDisabled ? 'disabled' : ''}>${analyzeLabel}</button><h3>Bekannte Gegner <span class="selection-count">${state.selectedOpp.length}/4</span></h3><p class="muted">Optional: bis zu vier Gegner, die sicher spielen.</p>${state.loadingPlayers ? '<div class="empty">Spieler werden geladen …</div>' : players(opp, state.selectedOpp, 'opp')}`;
}

function venueSelect() {
  return `<label class="venue-label">Spielort<select data-field="ownIsHome" ${state.loadingPlayers || state.analysisLoading ? 'disabled' : ''}><option value="home" ${state.ownIsHome ? 'selected' : ''}>Heim</option><option value="away" ${!state.ownIsHome ? 'selected' : ''}>Gast</option></select></label>`;
}

function render() {
  const own = state.ownPlayers;
  const opp = state.opponentPlayers;
  const opponents = state.teams.filter((t) => t.id !== state.ownTeam);
  app.innerHTML = `<section class="grid two"><div class="card">${setupPanelHtml(own, opp, opponents)}</div><div class="card highlight"><h2>2. Optimale Aufstellung</h2>${resultHtml()}</div></section>`;
  bind();
}

function select(label, field, items) {
  return `<label>${label}<select data-field="${field}" ${state.loadingPlayers || state.analysisLoading ? 'disabled' : ''}>${items.map((x) => `<option value="${escapeHtml(x.id)}" ${state[field] === x.id ? 'selected' : ''}>${escapeHtml(x.name)}</option>`).join('')}</select></label>`;
}

function rcLabel(p) {
  if (!p.rc_matched) return 'unmatched';
  if (p.rc_rating == null || Number.isNaN(Number(p.rc_rating))) return 'unmatched';
  const rating = Math.round(Number(p.rc_rating));
  if (p.rc_deviation != null && !Number.isNaN(Number(p.rc_deviation))) {
    return `${rating} ± ${Math.round(Number(p.rc_deviation))}`;
  }
  return String(rating);
}

function players(list, selected, group) {
  return `<div class="players">${list.map((p) => {
    const label = rcLabel(p);
    const small = label === 'unmatched' ? 'unmatched' : `RC ${label}`;
    return `<button class="player ${selected.includes(p.id) ? 'selected' : ''}" data-player="${p.id}" data-group="${group}" ${state.analysisLoading ? 'disabled' : ''}><span><strong>${escapeHtml(p.name)}</strong></span><small>${escapeHtml(small)}</small></button>`;
  }).join('')}</div>`;
}

function ownNameById(id) {
  const p = state.ownPlayers.find((x) => String(x.id) === String(id));
  return p?.name || `Spieler ${id}`;
}

function opponentNameById(id) {
  const p = state.opponentPlayers.find((x) => String(x.id) === String(id));
  return p?.name || `Spieler ${id}`;
}

function ownLineup(ids, backendNames) {
  return (ids || []).map((id, i) => `<div class="optimal-player"><span>${i + 1}</span><strong>${escapeHtml(ownNameById(id) || backendNames?.[i] || `Spieler ${id}`)}</strong></div>`).join('');
}

function opponentLineup(pred) {
  return (pred?.players || []).map((p, i) => {
    const id = typeof p === 'object' ? p.id : p;
    const name = typeof p === 'object' ? p.name : null;
    return `<div class="optimal-player"><span>${i + 1}</span><strong>${escapeHtml(opponentNameById(id) || name || `Spieler ${id}`)}</strong></div>`;
  }).join('');
}

function collapsible(id, title, bodyHtml, hint = '') {
  const open = state.uiExpanded[id];
  const hintHtml = hint ? `<span class="collapsible-hint">${escapeHtml(hint)}</span>` : '';
  return `<details class="collapsible" data-collapse="${id}" ${open ? 'open' : ''}><summary><span class="collapsible-title">${escapeHtml(title)}</span>${hintHtml}</summary><div class="collapsible-body">${bodyHtml}</div></details>`;
}

function explanationBody(explanation) {
  if (!explanation) return '<p class="muted">Keine Erklärung verfügbar.</p>';
  return `<p class="why-headline">${escapeHtml(explanation.headline || 'Die Aufstellung erzielt im Modell die höchste Siegchance.')}</p><ul class="why-list">${(explanation.bullets || []).map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul><div class="why-note">Modellbegründung, keine Garantie für den tatsächlichen Spielausgang.</div>`;
}

function infoSummaryHtml(summary) {
  if (!summary) return '<p class="muted">Keine Zusatzinfos verfügbar.</p>';
  const metaRows = [];
  if (summary.expected_first_doubles_probability != null) {
    metaRows.push(['Doppel (1. / 2.)', `${pct(summary.expected_first_doubles_probability)} / ${pct(summary.expected_second_doubles_probability)}`]);
  }
  if (summary.top_lineup_margin_pp != null && summary.top_lineup_margin_pp >= 0.5) {
    metaRows.push(['Abstand zur 2. Aufstellung', `${summary.top_lineup_margin_pp.toFixed(1).replace('.', ',')} PP`]);
  }
  if (summary.h2h_pairs_with_data != null) {
    metaRows.push(['Direktduelle', `${summary.h2h_pairs_with_data} Paare (${summary.stats_window_years} Jahre)`]);
  }
  metaRows.push([
    'Modell',
    `${summary.opponent_pool_size || 0} Gegner · ${summary.scenario_variants || 0} Szenarien · ${summary.orientation || '—'}`,
  ]);

  const playerCards = (summary.own_players || []).map((p) => {
    const expectedLabel = p.expected_singles_wins != null
      ? `Erw. ${p.expected_singles_wins} Einzel`
      : 'Erw. — Einzel';
    const rawHint = p.expected_singles_wins_raw != null && p.expected_singles_wins_raw !== p.expected_singles_wins
      ? ` <span class="info-expected-raw">(Ø ${String(p.expected_singles_wins_raw).replace('.', ',')})</span>`
      : '';
    const explanation = p.expected_singles_explanation
      ? `<div class="info-player-explanation">${escapeHtml(p.expected_singles_explanation)}</div>`
      : '';

    return `<div class="info-player-card"><div class="info-player-head"><strong>${escapeHtml(p.player_name)}</strong><div class="info-expected-singles">${escapeHtml(expectedLabel)}${rawHint}</div></div>${explanation}</div>`;
  }).join('');

  return `<div class="info-meta-grid">${metaRows.map(([label, value]) => `<div class="info-meta-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}</div><div class="info-player-cards">${playerCards}</div>`;
}

function altLineupsHtml(recommendations) {
  const items = (recommendations || []).slice(1);
  if (!items.length) return '<p class="muted">Keine weiteren Aufstellungen.</p>';
  return `<ol class="prediction-list">${items.map((x) => `<li><div class="recommendation-rank">#${x.rank}</div><div class="recommendation-names">${ownLineup(x.own_player_ids, x.players)}</div><strong>${pct(x.team_win_probability)}</strong></li>`).join('')}</ol>`;
}

function opponentLineupsHtml(predictions, skipFirst = true) {
  const items = skipFirst ? (predictions || []).slice(1) : (predictions || []);
  if (!items.length) return '<p class="muted">Keine weiteren Gegner-Aufstellungen im Modell.</p>';
  return `<ol class="prediction-list opponent-lineup-list">${items.map((p, i) => `<li><div class="recommendation-rank">#${skipFirst ? i + 2 : i + 1}</div><div class="recommendation-names">${opponentLineup(p)}</div><strong>${pct(p.probability)}</strong></li>`).join('')}</ol>`;
}

function resultContextText() {
  const venue = state.ownIsHome ? 'Heimspiel' : 'Auswärts';
  if (state.selectedOpp.length === 0) {
    return `${venue}: Gegnerische Aufstellungen werden aus historischen XTTV-Daten gewichtet.`;
  }
  if (state.selectedOpp.length === 4) {
    return `${venue}: Berechnung mit den vier ausgewählten Gegnern.`;
  }
  return `${venue}: Berechnung mit ${state.selectedOpp.length} bekannten Gegner(n); fehlende Positionen aus historischen Szenarien ergänzt.`;
}

function explanationHtml(explanation) {
  if (!explanation) return '';
  return collapsible('why', 'Warum diese Aufstellung?', explanationBody(explanation), 'Modellbegründung');
}

function resultHtml() {
  if (state.analysisLoading) return '<div class="analysis-loading" role="status" aria-live="polite"><div class="loading-title">Berechnung läuft …</div><div class="loading-track"><div class="loading-bar"></div></div><div class="loading-text">Die Analyse berücksichtigt Spielstärken, direkte Duelle und historische gegnerische Positionierungen.</div></div>';
  if (!state.result) return '<div class="empty">Wähle vier eigene Spieler und starte die Berechnung.</div>';
  if (!state.result.recommendation) {
    const warning = (state.result.warnings || []).join(' | ');
    return `<div class="empty error-box"><strong>Berechnung nicht abgeschlossen.</strong><br>${escapeHtml(state.result.message || state.result.error || warning || 'Unbekannter Fehler')}</div>`;
  }
  const b = state.result.recommendation;
  const opp = state.result.most_likely_opponent;
  const altCount = Math.max(0, (state.result.recommendations?.length || 1) - 1);
  const oppAltCount = Math.max(0, (state.result.opponent_predictions?.length || 1) - 1);
  const summary = state.result.info_summary;
  const rcHint = summary?.own_rc_sum != null && summary?.opponent_top_lineup_rc_sum != null
    ? `RC ${summary.own_rc_sum} vs ${summary.opponent_top_lineup_rc_sum}`
    : 'RC & Modelldetails';

  return `<p class="muted">${escapeHtml(resultContextText())}</p><div class="optimal-result"><div class="optimal-label">Empfohlene eigene Aufstellung</div><div class="optimal-players">${ownLineup(b.own_player_ids, b.players)}</div>${resultMetricsHtml(b)}<div class="probability-breakdown"><span>Sieg ${pct(b.team_win_probability)}</span><span>Unentschieden ${pct(b.team_draw_probability)}</span><span>Niederlage ${pct(b.team_loss_probability)}</span></div></div>${explanationHtml(state.result.explanation)}${collapsible('moreInfo', 'Mehr Info', infoSummaryHtml(summary), rcHint)}${opp ? `<div class="opponent-prediction"><div class="optimal-label">Wahrscheinlichste gegnerische Aufstellung</div><div class="muted small-text">${pct(opp.probability)} Wahrscheinlichkeit</div><div class="optimal-players">${opponentLineup(opp)}</div></div>` : ''}${oppAltCount ? collapsible('oppLineups', 'Nächstmögliche gegnerische Aufstellungen', opponentLineupsHtml(state.result.opponent_predictions), `${oppAltCount} weitere`) : ''}${altCount ? collapsible('altLineups', 'Weitere eigene Aufstellungen', altLineupsHtml(state.result.recommendations), `${altCount} Alternativen`) : ''}`;
}

function pct(v) {
  return `${(Number(v) * 100).toFixed(1).replace('.', ',')} %`;
}

function formatFromExpectedWins(win, own, opp) {
  if (own >= 7.5 || (win > 0.55 && own >= opp)) {
    const oppR = Math.round(opp);
    if (oppR <= 0 && opp < 0.5) return '10:0';
    if (oppR <= 1 && opp < 1.5) return '9:1';
    return `8:${Math.max(2, Math.min(6, oppR))}`;
  }
  if (opp >= 7.5 || (win < 0.45 && opp >= own)) {
    const ownR = Math.round(own);
    if (ownR >= 2) return `${Math.max(2, Math.min(7, ownR))}:8`;
    if (ownR <= 0 && own < 0.5) return '0:10';
    if (ownR <= 1 && own < 1.5) return '1:9';
    return `${Math.max(2, Math.min(6, ownR))}:8`;
  }
  return '7:7';
}

function formatMatchScoreDisplay(recommendation) {
  const win = Number(recommendation?.team_win_probability ?? 0);
  const own = Number(recommendation?.expected_own_wins);
  const opp = Number(recommendation?.expected_opponent_wins);
  if (Number.isFinite(own) && Number.isFinite(opp)) {
    return formatFromExpectedWins(win, own, opp);
  }
  if (recommendation?.expected_score_display) return recommendation.expected_score_display;
  return formatFromExpectedWins(win, 0, 0);
}

function scoreText(recommendation) {
  return formatMatchScoreDisplay(recommendation);
}

function resultMetricsHtml(recommendation) {
  const expectedHtml = recommendation
    ? `<div class="expected-score">${escapeHtml(scoreText(recommendation))} <span>Erwartetes Ergebnis</span></div>`
    : '';
  return `<div class="result-metrics"><div class="win-probability">${pct(recommendation.team_win_probability)} <span>Mannschafts-Siegwahrscheinlichkeit</span></div>${expectedHtml}</div>`;
}

function bind() {
  app.querySelectorAll('select').forEach((el) => el.addEventListener('change', async (e) => {
    const field = e.target.dataset.field;
    if (field === 'ownIsHome') {
      state.ownIsHome = e.target.value === 'home';
      state.result = null;
      render();
      return;
    }
    state[field] = e.target.value;
    state.result = null;
    if (field === 'ownTeam' && state.opponentTeam === state.ownTeam) {
      state.opponentTeam = pickOpponentTeam(state.teams, state.ownTeam);
    }
    await loadTeamPlayers();
    render();
  }));
  app.querySelectorAll('input[type="checkbox"][data-field]').forEach((el) => el.addEventListener('change', (e) => {
    const field = e.currentTarget.dataset.field;
    if (field === 'useSpieltyp') {
      state.useSpieltyp = e.currentTarget.checked;
      state.result = null;
      render();
    }
  }));
  app.querySelectorAll('[data-player]').forEach((el) => el.addEventListener('click', (e) => {
    const group = e.currentTarget.dataset.group;
    const key = group === 'own' ? 'selectedOwn' : 'selectedOpp';
    const id = e.currentTarget.dataset.player;
    state[key] = state[key].includes(id) ? state[key].filter((x) => x !== id) : state[key].length < 4 ? [...state[key], id] : state[key];
    state.result = null;
    render();
  }));
  app.querySelector('[data-action="analyze"]')?.addEventListener('click', runAnalysis);
  app.querySelectorAll('[data-collapse]').forEach((el) => {
    el.addEventListener('toggle', () => {
      state.uiExpanded[el.dataset.collapse] = el.open;
    });
  });
}

async function runAnalysis() {
  if (state.selectedOwn.length !== 4 || new Set(state.selectedOwn).size !== 4 || state.analysisLoading) return;
  state.analysisLoading = true;
  state.result = null;
  syncHeader();
  render();
  try {
    const known = state.selectedOpp.length ? '&actual_opponent_ids=' + encodeURIComponent(state.selectedOpp.join(',')) : '';
    const home = '&own_is_home=' + (state.ownIsHome ? 'true' : 'false');
    const spieltyp = state.useSpieltyp ? '&use_spieltyp=true' : '';
    state.result = await api(`/api/analysis?own_player_ids=${encodeURIComponent(state.selectedOwn.join(','))}&opponent_team=${encodeURIComponent(state.opponentTeam)}${home}${known}${spieltyp}`);
  } catch (e) {
    state.result = { recommendations: [], message: e.message };
  } finally {
    state.analysisLoading = false;
    syncHeader();
    render();
  }
}

init();
