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
  opponentDirection: '',
  useSpieltyp: false,
  doublePair1Ids: [],
  strongerDoublePair: 1,
  doublesSuggestion: null,
  ownPlayers: [],
  opponentPlayers: [],
  selectedOwn: [],
  selectedOpp: [],
  result: null,
  optimalResult: null,
  editMode: false,
  editOwnOrder: [],
  editDoubles: null,
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
let doublesSuggestionLoad = null;

async function api(path, { timeoutMs = 5000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(path, { signal: controller.signal });
    const d = await r.json();
    if (!r.ok || d.ok === false) throw new Error(d.message || d.error || 'API error');
    return d;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('Zeitüberschreitung (5 s) — Berechnung zu langsam.');
    throw e;
  } finally {
    clearTimeout(timer);
  }
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
  state.optimalResult = null;
  state.editMode = false;
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
    state.opponentDirection = '';
    state.doublePair1Ids = [];
    state.doublesSuggestion = null;
    state.result = null;
    state.optimalResult = null;
    state.editMode = false;
  } finally {
    state.loadingPlayers = false;
    syncHeader();
  }
}

function defaultDoublePair1Ids(selectedIds) {
  return [...selectedIds].map(String).slice(0, 2);
}

function ensureDoublePairs() {
  if (state.selectedOwn.length !== 4) {
    state.doublePair1Ids = [];
    return;
  }
  const selected = state.selectedOwn.map(String);
  const valid = state.doublePair1Ids.length === 2 && state.doublePair1Ids.every((id) => selected.includes(id));
  if (!valid) {
    if (state.doublesSuggestion?.suggested_pair_a?.length === 2) {
      state.doublePair1Ids = state.doublesSuggestion.suggested_pair_a.map(String);
    } else {
      state.doublePair1Ids = defaultDoublePair1Ids(selected);
    }
  }
}

async function loadDoublesSuggestion() {
  if (state.selectedOwn.length !== 4) {
    state.doublesSuggestion = null;
    return;
  }
  const leagueQ = state.league ? `&league=${encodeURIComponent(state.league)}` : '';
  const teamQ = `&team=${encodeURIComponent(state.ownTeam)}`;
  try {
    state.doublesSuggestion = await api(`/api/doubles/suggest?player_ids=${encodeURIComponent(state.selectedOwn.join(','))}${teamQ}${leagueQ}`);
    if (state.doublesSuggestion?.ok) {
      state.doublePair1Ids = (state.doublesSuggestion.suggested_pair_a || []).map(String);
      state.strongerDoublePair = state.doublesSuggestion.suggested_stronger_pair === 2 ? 2 : 1;
    }
  } catch {
    state.doublesSuggestion = null;
  }
}

function scheduleDoublesSuggestion() {
  if (state.selectedOwn.length !== 4 || state.loadingPlayers || state.doublesSuggestion?.ok || doublesSuggestionLoad) return;
  doublesSuggestionLoad = loadDoublesSuggestion()
    .finally(() => {
      doublesSuggestionLoad = null;
      render();
    });
}

function toggleDoublePair(playerId) {
  ensureDoublePairs();
  const p1 = [...state.doublePair1Ids];
  const p2 = state.selectedOwn.filter((id) => !p1.includes(String(id)));
  const id = String(playerId);
  if (p1.includes(id)) {
    state.doublePair1Ids = p1.filter((x) => x !== id).concat(String(p2[0]));
  } else {
    state.doublePair1Ids = p1.filter((x) => x !== String(p1[0])).concat(id);
  }
  state.result = null;
  render();
}

function doublesSetupHtml() {
  if (state.selectedOwn.length !== 4) return '';
  ensureDoublePairs();
  const p1 = state.doublePair1Ids;
  const p2 = state.selectedOwn.filter((id) => !p1.includes(String(id)));
  const chip = (id, label) => `<button type="button" class="double-chip" data-double-toggle="${escapeHtml(id)}" ${state.analysisLoading ? 'disabled' : ''}><span>${escapeHtml(ownNameById(id))}</span><small>${label}</small></button>`;
  return `<h3>Doppel</h3><div class="double-pair-row" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px"><div class="double-pair-col"><strong>Doppel 1</strong><div class="double-chips">${p1.map((id) => chip(id, 'D1')).join('')}</div></div><div class="double-pair-col"><strong>Doppel 2</strong><div class="double-chips">${p2.map((id) => chip(id, 'D2')).join('')}</div></div></div><div class="double-placement" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px"><span class="double-placement-label" style="grid-column:1/-1">Stärkeres Paar</span><label class="option-check"><input type="radio" name="strongerDoublePair" value="1" ${state.strongerDoublePair === 1 ? 'checked' : ''} ${state.analysisLoading ? 'disabled' : ''}><span>Doppel 1</span></label><label class="option-check"><input type="radio" name="strongerDoublePair" value="2" ${state.strongerDoublePair === 2 ? 'checked' : ''} ${state.analysisLoading ? 'disabled' : ''}><span>Doppel 2</span></label></div>`;
}

function pairNames(players, nameFn) {
  return (players || []).map((p) => escapeHtml(nameFn(p.id) || p.name || `Spieler ${p.id}`)).join(' / ');
}

function buildOwnDoublesFromSetup() {
  if (state.selectedOwn.length !== 4) return null;
  ensureDoublePairs();
  const p1 = state.doublePair1Ids.map(String);
  const p2 = state.selectedOwn.map(String).filter((id) => !p1.includes(id));
  if (p1.length !== 2 || p2.length !== 2) return null;
  const placement = state.result?.recommendation?.recommended_doubles_on
    ?? state.result?.doubles_advice?.stronger_on_recommended
    ?? 5;
  const strong = state.strongerDoublePair === 2 ? p2 : p1;
  const weak = state.strongerDoublePair === 2 ? p1 : p2;
  const g5 = placement === 10 ? weak : strong;
  const g10 = placement === 10 ? strong : weak;
  const mk = (ids) => ({ players: ids.map((id) => ({ id, name: ownNameById(id) })) });
  return { game5: mk(g5), game10: mk(g10) };
}

function resolveOwnDoubles() {
  const rec = state.result?.recommendation?.doubles;
  if (rec?.game5?.players?.length && rec?.game10?.players?.length) return rec;
  const advice = state.result?.doubles_advice;
  if (advice?.game5?.players?.length && advice?.game10?.players?.length) return advice;
  return buildOwnDoublesFromSetup();
}

function doublesPlayerRows(doubles, nameFn) {
  if (!doubles?.game5?.players?.length || !doubles?.game10?.players?.length) return '';
  const row = (badge, game) => `<div class="optimal-player"><span>${badge}</span><strong>${pairNames(game.players, nameFn)}</strong></div>`;
  return `<div class="doubles-lineup" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px">${row('5', doubles.game5)}${row('10', doubles.game10)}</div>`;
}

function ownDoublesLineupHtml() {
  return doublesPlayerRows(resolveOwnDoubles(), ownNameById);
}

function setupPanelHtml(own, opp, opponents) {
  const directionRequired = state.selectedOpp.length === 4 && !state.opponentDirection;
  const analyzeDisabled = state.selectedOwn.length !== 4 || directionRequired || state.analysisLoading || state.loadingPlayers;
  const analyzeLabel = state.analysisLoading ? 'Berechnung läuft …' : 'Optimale Aufstellung berechnen';
  return `<h2>1. Match Setup</h2><div class="setup-team-row">${select('Eigene Mannschaft', 'ownTeam', state.teams)}${venueSelect()}</div>${select('Gegner', 'opponentTeam', opponents)}<label class="option-check"><input type="checkbox" data-field="useSpieltyp" ${state.useSpieltyp ? 'checked' : ''} ${state.loadingPlayers || state.analysisLoading ? 'disabled' : ''}><span>Spielertyp in Gewichtung miteinbeziehen</span></label><p class="muted option-hint">Offensiv/Noppen/Defensiv/Normal</p><h3>Eigene Spieler <span class="selection-count">${state.selectedOwn.length}/4</span></h3><p class="muted">Wähle genau vier Spieler.</p>${state.loadingPlayers ? '<div class="empty">Spieler werden geladen …</div>' : players(own, state.selectedOwn, 'own')}${doublesSetupHtml()}<h3>Bekannte Gegner <span class="selection-count">${state.selectedOpp.length}/4</span></h3><p class="muted">Optional: bis zu vier Gegner, die sicher spielen.</p>${state.loadingPlayers ? '<div class="empty">Spieler werden geladen …</div>' : players(opp, state.selectedOpp, 'opp')}${opponentDirectionHtml()}<button class="primary setup-analyze" data-action="analyze" ${analyzeDisabled ? 'disabled' : ''}>${analyzeLabel}</button>`;
}

function opponentDirectionHtml() {
  if (state.selectedOpp.length !== 4) return '';
  return `<div class="direction-select" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px"><span class="direction-label" style="grid-column:1/-1">Gegnerische Aufstellungsrichtung</span><label class="option-check"><input type="checkbox" data-direction="letters" ${state.opponentDirection === 'letters' ? 'checked' : ''} ${state.analysisLoading ? 'disabled' : ''}><span>A–D (waagrecht)</span></label><label class="option-check"><input type="checkbox" data-direction="numbers" ${state.opponentDirection === 'numbers' ? 'checked' : ''} ${state.analysisLoading ? 'disabled' : ''}><span>1–4 (senkrecht)</span></label></div>`;
}

function venueSelect() {
  return `<label class="venue-label">Spielort<select data-field="ownIsHome" ${state.loadingPlayers || state.analysisLoading ? 'disabled' : ''}><option value="home" ${state.ownIsHome ? 'selected' : ''}>Heim</option><option value="away" ${!state.ownIsHome ? 'selected' : ''}>Gast</option></select></label>`;
}

function render() {
  const own = state.ownPlayers;
  const opp = state.opponentPlayers;
  const opponents = state.teams.filter((t) => t.id !== state.ownTeam);
  app.innerHTML = `<section class="grid two"><div class="card">${setupPanelHtml(own, opp, opponents)}</div><div class="card highlight"><div class="result-card-heading"><h2>2. Optimale Aufstellung</h2>${state.result?.recommendation ? `<button type="button" class="secondary edit-button" data-action="toggle-edit" ${state.analysisLoading ? 'disabled' : ''}>${state.editMode ? 'Neu berechnen' : 'Ändern'}</button>` : ''}</div>${resultHtml()}</div></section>`;
  scheduleDoublesSuggestion();
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
  const sorted = [...(list || [])].sort((a, b) => {
    const aRating = Number(a.rc_rating);
    const bRating = Number(b.rc_rating);
    const aHasRating = a.rc_rating != null && Number.isFinite(aRating);
    const bHasRating = b.rc_rating != null && Number.isFinite(bRating);
    if (aHasRating !== bHasRating) return aHasRating ? -1 : 1;
    if (aHasRating && aRating !== bRating) return bRating - aRating;
    return String(a.name || '').localeCompare(String(b.name || ''), 'de');
  });
  return `<div class="players">${sorted.map((p) => {
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
  const labels = state.opponentDirection === 'numbers' ? ['A', 'B', 'C', 'D'] : ['1', '2', '3', '4'];
  return (ids || []).map((id, i) => `<div class="optimal-player"><span>${labels[i] || i + 1}</span><strong>${escapeHtml(ownNameById(id) || backendNames?.[i] || `Spieler ${id}`)}</strong></div>`).join('');
}

function editableLineupHtml() {
  const ids = state.editOwnOrder;
  const labels = state.opponentDirection === 'numbers' ? ['A', 'B', 'C', 'D'] : ['1', '2', '3', '4'];
  return `<div class="optimal-players editable-lineup">${ids.map((id, i) => `<div class="optimal-player edit-player" draggable="${state.analysisLoading ? 'false' : 'true'}" data-edit-player="${escapeHtml(id)}" data-edit-index="${i}"><span>${labels[i]}</span><strong>${escapeHtml(ownNameById(id))}</strong></div>`).join('')}</div><div class="edit-hint muted">Spieler per Drag & Drop verschieben.</div>`;
}

function editableDoublesHtml() {
  const doubles = state.editDoubles;
  if (!doubles?.game5 || !doubles?.game10) return '';
  const row = (game, ids) => `<div class="optimal-player edit-double" draggable="${state.analysisLoading ? 'false' : 'true'}" data-edit-double="${game}"><span>${game}</span><strong>${ids.map((id) => escapeHtml(ownNameById(id))).join(' / ')}</strong></div>`;
  return `<div class="doubles-lineup editable-doubles">${row('5', doubles.game5)}${row('10', doubles.game10)}</div><div class="edit-hint muted">Doppel 5 und 10 können als Paar getauscht werden.</div>`;
}

function opponentLineup(pred, { includeDoubles = true } = {}) {
  const singles = (pred?.players || []).map((p, i) => {
    const id = typeof p === 'object' ? p.id : p;
    const name = typeof p === 'object' ? p.name : null;
    return `<div class="optimal-player"><span>${i + 1}</span><strong>${escapeHtml(opponentNameById(id) || name || `Spieler ${id}`)}</strong></div>`;
  }).join('');
  const doubles = includeDoubles ? doublesPlayerRows(pred?.doubles, opponentNameById) : '';
  return singles + doubles;
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
  if (summary.own_rc_sum != null && summary.opponent_top_lineup_rc_sum != null) {
    metaRows.push([
      'Spielstärke RC-Summen',
      `${Math.round(Number(summary.own_rc_sum))} gegen ${Math.round(Number(summary.opponent_top_lineup_rc_sum))}`,
    ]);
  }
  if (summary.expected_first_doubles_probability != null) {
    metaRows.push(['Doppel-Siegchancen', `Doppel 1: ${pct(summary.expected_first_doubles_probability)} · Doppel 2: ${pct(summary.expected_second_doubles_probability)}`]);
  }
  if (summary.top_lineup_margin_pp != null && summary.top_lineup_margin_pp >= 0.5) {
    metaRows.push(['Abstand zur 2. Aufstellung', `${summary.top_lineup_margin_pp.toFixed(1).replace('.', ',')} PP`]);
  }
  if (summary.h2h_pairs_with_data != null) {
    metaRows.push(['Direkte Duelle', `${summary.h2h_pairs_with_data} · Datenbasis ${summary.stats_window_years} Jahre`]);
  }
  const opponentBasis = state.selectedOpp.length === 4
    ? `${state.selectedOpp.length} feste Gegner`
    : `${summary.opponent_pool_size || 0} mögliche Gegner`;
  metaRows.push([
    'Berechnungsgrundlage',
    `${opponentBasis} · ${summary.scenario_variants || 0} historische Varianten · ${summary.orientation === 'opponent-A-D' ? 'Gegner A–D' : summary.orientation === 'opponent-1-4' ? 'Gegner 1–4' : (summary.orientation || '—')}`,
  ]);

  const playerCards = (summary.own_players || []).map((p) => {
    const expectedLabel = p.expected_singles_wins != null
      ? `${p.expected_singles_wins} erwartete Einzel`
      : '— erwartete Einzel';
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

function altLineupsHtml(recommendations, optimalProbability) {
  const items = (recommendations || []).slice(1, 6);
  if (!items.length) return '<p class="muted">Keine weiteren Aufstellungen.</p>';
  return `<ol class="prediction-list">${items.map((x) => { const loss = Math.max(0, (Number(optimalProbability) - Number(x.team_win_probability)) * 100); return `<li><div class="recommendation-rank">#${x.rank}</div><div class="recommendation-names">${ownLineup(x.own_player_ids, x.players)}</div><strong style="color:#b42318">-${loss.toFixed(1).replace('.', ',')} %</strong></li>`; }).join('')}</ol>`;
}

function opponentLineupsHtml(predictions, skipFirst = true) {
  const items = skipFirst ? (predictions || []).slice(1) : (predictions || []);
  if (!items.length) return '<p class="muted">Keine weiteren Gegner-Aufstellungen im Modell.</p>';
  return `<ol class="prediction-list opponent-lineup-list">${items.map((p, i) => `<li><div class="recommendation-rank">#${skipFirst ? i + 2 : i + 1}</div><div class="recommendation-names">${opponentLineup(p)}</div><strong>${pct(p.probability)}</strong></li>`).join('')}</ol>`;
}

function explanationHtml(explanation) {
  if (!explanation) return '';
  return collapsible('why', 'Warum diese Aufstellung?', explanationBody(explanation), 'Modellbegründung');
}

function resultHtml() {
  if (state.analysisLoading) return '<div class="analysis-loading" role="status" aria-live="polite"><div class="loading-title">Berechnung läuft …</div><div class="loading-track"><div class="loading-bar"></div></div><div class="loading-text">Die Analyse berücksichtigt Spielstärken, direkte Duelle und historische gegnerische Positionierungen.</div></div>';
  if (state.selectedOpp.length === 4 && !state.opponentDirection) return '<div class="empty">Phase 2: Die vier Gegner sind bekannt. Wähle noch, ob der Gegner A–D (waagrecht) oder 1–4 (senkrecht) spielt.</div>';
  if (!state.result) return '<div class="empty">Wähle vier eigene Spieler und starte die Berechnung.</div>';
  if (!state.result.recommendation) {
    const warning = (state.result.warnings || []).join(' | ');
    return `<div class="empty error-box"><strong>Berechnung nicht abgeschlossen.</strong><br>${escapeHtml(state.result.message || state.result.error || warning || 'Unbekannter Fehler')}</div>`;
  }
  const b = state.result.recommendation;
  const opp = state.result.most_likely_opponent;
  const summary = state.result.info_summary;
  const rcHint = summary?.own_rc_sum != null && summary?.opponent_top_lineup_rc_sum != null
    ? 'Spielstärkevergleich'
    : 'Modelldetails';

  const alternatives = (state.result.recommendations?.length || 1) > 1
    ? collapsible('altLineups', 'Alternative Aufstellungen', altLineupsHtml(state.result.recommendations, b.team_win_probability), `${Math.min(5, state.result.recommendations.length - 1)} weitere`)
    : '';
  const lineupHtml = state.editMode ? `${editableLineupHtml()}${editableDoublesHtml()}` : `<div class="optimal-players">${ownLineup(b.own_player_ids, b.players)}${ownDoublesLineupHtml()}</div>`;
  const comparison = state.editMode && state.result?.optimal_recommendation && state.optimalResult?.recommendation ? `<div class="edit-comparison">Optimal: ${pct(state.optimalResult.recommendation.team_win_probability)} · <span class="edit-deviation">Abweichung zu Optimal: ${((Number(b.team_win_probability) - Number(state.optimalResult.recommendation.team_win_probability)) * 100).toFixed(1).replace('.', ',')} %</span></div><button type="button" class="secondary reset-edit-button" data-action="reset-edit">Zurück zur optimalen Aufstellung</button>` : '';
  const resultClass = state.editMode ? 'fixed' : (state.result?.optimal_recommendation ? 'fixed' : '');
  return `<div class="optimal-result ${resultClass}"><div class="optimal-label">Empfohlene Eigene Aufstellung</div>${lineupHtml}${resultMetricsHtml(b)}<div class="probability-breakdown"><span>Sieg ${pct(b.team_win_probability)}</span><span>Unentschieden ${pct(b.team_draw_probability)}</span><span>Niederlage ${pct(b.team_loss_probability)}</span></div>${comparison}</div>${!state.editMode ? explanationHtml(state.result.explanation) : ''}${!state.editMode ? collapsible('moreInfo', 'Mehr Info', infoSummaryHtml(summary), rcHint) : ''}${opp ? `<div class="opponent-prediction"><div class="optimal-label">Wahrscheinlichste gegnerische Aufstellung</div><div class="muted small-text">${pct(opp.probability)} Wahrscheinlichkeit</div><div class="optimal-players">${opponentLineup(opp)}</div></div>` : ''}${alternatives}`;
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
      state.optimalResult = null;
      state.editMode = false;
      render();
      return;
    }
    if (field === 'opponentDirection') {
      state.opponentDirection = e.target.value;
      state.result = null;
      state.optimalResult = null;
      state.editMode = false;
      render();
      return;
    }
    state[field] = e.target.value;
    state.result = null;
    state.optimalResult = null;
    state.editMode = false;
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
      state.optimalResult = null;
      state.editMode = false;
      render();
    }
  }));
  app.querySelectorAll('input[type="checkbox"][data-direction]').forEach((el) => el.addEventListener('change', (e) => {
    if (e.currentTarget.checked) {
      state.opponentDirection = e.currentTarget.dataset.direction;
    } else if (state.opponentDirection === e.currentTarget.dataset.direction) {
      state.opponentDirection = '';
    }
    state.result = null;
    state.optimalResult = null;
    state.editMode = false;
    render();
  }));
  app.querySelectorAll('input[type="radio"][name="strongerDoublePair"]').forEach((el) => el.addEventListener('change', (e) => {
    state.strongerDoublePair = Number(e.target.value) === 2 ? 2 : 1;
    state.result = null;
    state.optimalResult = null;
    state.editMode = false;
    render();
  }));
  app.querySelectorAll('[data-double-toggle]').forEach((el) => el.addEventListener('click', (e) => {
    toggleDoublePair(e.currentTarget.dataset.doubleToggle);
  }));
  app.querySelector('[data-action="toggle-edit"]')?.addEventListener('click', () => {
    if (state.editMode) {
      runAnalysis();
      return;
    }
    const recommendation = state.result?.recommendation;
    if (!recommendation) return;
    state.optimalResult = state.optimalResult || state.result;
    state.editMode = true;
    state.editOwnOrder = [...recommendation.own_player_ids];
    const doubles = recommendation.doubles || state.result.doubles_advice;
    state.editDoubles = doubles?.game5?.players && doubles?.game10?.players
      ? { game5: doubles.game5.players.map((p) => String(p.id)), game10: doubles.game10.players.map((p) => String(p.id)) }
      : null;
    render();
  });
  app.querySelector('[data-action="reset-edit"]')?.addEventListener('click', () => {
    state.result = state.optimalResult;
    state.editMode = false;
    state.editOwnOrder = [];
    state.editDoubles = null;
    render();
  });
  app.querySelectorAll('[data-edit-player]').forEach((el) => {
    el.addEventListener('dragstart', (e) => {
      if (state.analysisLoading) return;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', el.dataset.editIndex);
    });
    el.addEventListener('dragover', (e) => e.preventDefault());
    el.addEventListener('drop', (e) => {
      e.preventDefault();
      const from = Number(e.dataTransfer.getData('text/plain'));
      const to = Number(el.dataset.editIndex);
      if (!Number.isInteger(from) || from === to) return;
      const next = [...state.editOwnOrder];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      state.editOwnOrder = next;
      render();
    });
  });
  app.querySelectorAll('[data-edit-double]').forEach((el) => {
    el.addEventListener('dragstart', (e) => {
      if (state.analysisLoading) return;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', el.dataset.editDouble);
    });
    el.addEventListener('dragover', (e) => e.preventDefault());
    el.addEventListener('drop', (e) => {
      e.preventDefault();
      const from = e.dataTransfer.getData('text/plain');
      const to = el.dataset.editDouble;
      if (!state.editDoubles || from === to) return;
      const next = { ...state.editDoubles, game5: [...state.editDoubles.game5], game10: [...state.editDoubles.game10] };
      [next.game5, next.game10] = [next.game10, next.game5];
      state.editDoubles = next;
      render();
    });
  });
  app.querySelectorAll('[data-player]').forEach((el) => el.addEventListener('click', async (e) => {
    const group = e.currentTarget.dataset.group;
    const key = group === 'own' ? 'selectedOwn' : 'selectedOpp';
    const id = e.currentTarget.dataset.player;
    state[key] = state[key].includes(id) ? state[key].filter((x) => x !== id) : state[key].length < 4 ? [...state[key], id] : state[key];
    state.result = null;
    if (group === 'opp' && state.selectedOpp.length !== 4) state.opponentDirection = '';
    if (group === 'own') {
      if (state.selectedOwn.length === 4) {
        // Load the historical pair suggestion in the background. It must
        // never block the analysis UI when the database is cold.
        scheduleDoublesSuggestion();
      } else {
        state.doublePair1Ids = [];
        state.doublesSuggestion = null;
      }
    }
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
    const direction = state.opponentDirection ? '&opponent_on_letters=' + (state.opponentDirection === 'letters' ? 'true' : 'false') : '';
    const spieltyp = state.useSpieltyp ? '&use_spieltyp=true' : '';
    ensureDoublePairs();
    const p2 = state.selectedOwn.filter((id) => !state.doublePair1Ids.includes(String(id)));
    let pairs;
    let strongerPair;
    let fixedDoublesOn = '';
    let fixedOrder = '';
    if (state.editMode && state.editDoubles) {
      pairs = `&own_double_pairs=${encodeURIComponent(`${state.editDoubles.game5.join(',')};${state.editDoubles.game10.join(',')}`)}`;
      strongerPair = '&stronger_double_pair=1';
      fixedDoublesOn = '&fixed_doubles_on=5';
      fixedOrder = `&fixed_own_order=${encodeURIComponent(state.editOwnOrder.join(','))}`;
    } else {
      pairs = `&own_double_pairs=${encodeURIComponent(`${state.doublePair1Ids.join(',')};${p2.join(',')}`)}`;
      strongerPair = `&stronger_double_pair=${state.strongerDoublePair}`;
    }
    const ownTeam = `&own_team=${encodeURIComponent(state.ownTeam)}`;
    const nextResult = await api(`/api/analysis?own_player_ids=${encodeURIComponent(state.selectedOwn.join(','))}&opponent_team=${encodeURIComponent(state.opponentTeam)}${home}${known}${direction}${spieltyp}${pairs}${strongerPair}${ownTeam}${fixedOrder}${fixedDoublesOn}`);
    if (state.editMode) {
      state.result = nextResult;
      state.optimalResult = state.optimalResult || nextResult;
    } else {
      state.result = nextResult;
      state.optimalResult = nextResult;
    }
  } catch (e) {
    state.result = { recommendations: [], message: e.message };
  } finally {
    state.analysisLoading = false;
    syncHeader();
    render();
  }
}

init();
