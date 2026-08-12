import { DemoDataProvider } from '../data/DemoDataProvider.mjs';
import { buildOpponentCombinationPredictions, buildPositionVariants, buildOptimalOwnLineup } from '../engine/predictionEngine.mjs';

const provider = new DemoDataProvider();
const state = { leagues: [], teams: [], players: [], leagueId: '', ownTeamId: '', opponentTeamId: '', ownPlayerIds: [], actualOpponentIds: [], predictions: [], optimalLineup: null, loading: false };
const app = document.querySelector('#app');

init();
async function init() {
  [state.leagues, state.teams, state.players] = await Promise.all([provider.getLeagues(), provider.getTeams(), provider.getPlayers()]);
  state.leagueId = state.leagues[0].id;
  state.ownTeamId = state.teams[0].id;
  state.opponentTeamId = state.teams[1].id;
  state.ownPlayerIds = state.teams[0].players.slice(0, 4);
  render();
}
function render() {
  const ownTeam = team(state.ownTeamId), opponentTeam = team(state.opponentTeamId);
  const ownPlayers = ownTeam.players.map(player), opponentPlayers = opponentTeam.players.map(player);
  const actual = state.actualOpponentIds.map(player);
  const variants = actual.length === 4 ? buildPositionVariants(actual).slice(0, 6) : [];
  app.innerHTML = `<section class="grid two"><div class="card"><h2>1. Match Setup</h2>${select('Liga', 'leagueId', state.leagues)}${select('Eigene Mannschaft', 'ownTeamId', state.teams)}${select('Gegner', 'opponentTeamId', state.teams.filter(t => t.id !== state.ownTeamId))}<h3>Eigene 4 Spieler</h3>${playersHtml(ownPlayers, state.ownPlayerIds, 'own')}<button class="primary" data-action="analyze" ${state.ownPlayerIds.length !== 4 || state.loading ? 'disabled' : ''}>${state.loading ? 'Berechne …' : 'Optimale Aufstellung berechnen'}</button></div><div class="card highlight"><h2>2. Deine optimale Aufstellung</h2>${optimalHtml()}</div></section><section class="grid two"><div class="card"><h2>3. Tatsächliche Gegner stehen fest</h2><p class="muted">Wähle die vier tatsächlich angetretenen Gegner. Ihre Positionen bleiben geheim.</p>${playersHtml(opponentPlayers, state.actualOpponentIds, 'opp')}${actual.length === 4 ? `<div class="success">4 Gegner ausgewählt. Die optimale eigene Aufstellung wird neu bewertet.</div>` : `<div class="empty">${actual.length}/4 Gegner ausgewählt.</div>`}</div><div class="card"><h2>4. Gegneranalyse</h2>${analysisHtml(actual, variants)}</div></section>`;
  bind();
}
function bind() {
  app.querySelectorAll('select').forEach(el => el.addEventListener('change', e => { state[e.target.dataset.field] = e.target.value; if (e.target.dataset.field === 'ownTeamId') state.ownPlayerIds = team(state.ownTeamId).players.slice(0,4); if (e.target.dataset.field === 'opponentTeamId') { state.actualOpponentIds = []; state.predictions = []; state.optimalLineup = null; } render(); }));
  app.querySelectorAll('[data-player]').forEach(el => el.addEventListener('click', e => togglePlayer(e.currentTarget.dataset.player, e.currentTarget.dataset.group)));
  app.querySelector('[data-action="analyze"]')?.addEventListener('click', async () => { state.loading = true; render(); await new Promise(r => setTimeout(r, 250)); state.predictions = buildOpponentCombinationPredictions(team(state.opponentTeamId).players.map(player), await provider.getHistoricalCombinations(state.opponentTeamId)); state.optimalLineup = buildOptimalOwnLineup(state.ownPlayerIds.map(player), team(state.opponentTeamId).players.map(player), state.predictions); state.loading = false; render(); });
}
function togglePlayer(id, group) { const key = group === 'own' ? 'ownPlayerIds' : 'actualOpponentIds'; state[key] = state[key].includes(id) ? state[key].filter(x => x !== id) : state[key].length < 4 ? [...state[key], id] : state[key]; if (group === 'own') state.optimalLineup = null; render(); }
function optimalHtml() { if (state.loading) return '<div class="empty">Analysiere mögliche Gegner-Szenarien und berechne die beste eigene Aufstellung …</div>'; if (!state.optimalLineup) return '<div class="empty">Wähle deine vier Spieler und starte die Berechnung.</div>'; const best = state.optimalLineup.best; return `<p class="muted">Die gegnerischen Szenarien werden intern berücksichtigt, aber nicht angezeigt. Ziel ist die maximale erwartete Mannschafts-Siegwahrscheinlichkeit.</p><div class="optimal-result"><div class="optimal-label">Empfohlene Aufstellung</div><div class="optimal-players">${best.playerIds.map((id, i) => `<div class="optimal-player"><span>${i + 1}</span><strong>${player(id).name}</strong><small>RC ${player(id).rcRating}</small></div>`).join('')}</div><div class="win-probability">${pct(best.teamWinProbability)} <span>erwartete Mannschafts-Siegwahrscheinlichkeit</span></div></div>${state.optimalLineup.alternatives.length ? `<h3>Knapp dahinter</h3><ol class="prediction-list">${state.optimalLineup.alternatives.map(a => `<li><span>${a.playerIds.map(id => player(id).name).join(' · ')}</span><strong>${pct(a.teamWinProbability)}</strong></li>`).join('')}</ol>` : ''}`; }
function analysisHtml(actual, variants) { if (actual.length !== 4) return '<div class="empty">Nach Auswahl der vier Gegner erscheinen deren Stärkedaten. Die geheimen Positionen werden nicht angezeigt.</div>'; return `<div class="strength-grid">${actual.map(p => `<div class="player-stat"><strong>${p.name}</strong><span>RC ${p.rcRating}</span><small>Spielstärke</small></div>`).join('')}</div><p class="muted">Die möglichen Positionierungen werden intern für die Optimierung berücksichtigt, aber nicht als gegnerische Aufstellung offengelegt.</p>`; }
function playersHtml(players, selected, group) { return `<div class="players">${players.map(p => `<button class="player ${selected.includes(p.id) ? 'selected' : ''}" data-player="${p.id}" data-group="${group}" ${!p.active && !selected.includes(p.id) ? 'disabled' : ''}><span>${p.name}</span><small>RC ${p.rcRating}${p.active ? '' : ' · nicht verfügbar'}</small></button>`).join('')}</div>`; }
function select(label, field, items) { return `<label>${label}<select data-field="${field}">${items.map(i => `<option value="${i.id}" ${state[field] === i.id ? 'selected' : ''}>${i.name}</option>`).join('')}</select></label>`; }
function team(id) { return state.teams.find(t => t.id === id); }
function player(id) { return state.players.find(p => p.id === id); }
function pct(value) { return `${(value * 100).toFixed(1).replace('.', ',')} %`; }
