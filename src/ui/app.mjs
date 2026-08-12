import { DemoDataProvider } from '../data/DemoDataProvider.mjs';
import { buildOpponentCombinationPredictions, buildPositionVariants } from '../engine/predictionEngine.mjs';

const provider = new DemoDataProvider();
const state = { leagues: [], teams: [], players: [], leagueId: '', ownTeamId: '', opponentTeamId: '', ownPlayerIds: [], actualOpponentIds: [], predictions: [], loading: false };
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
  app.innerHTML = `<section class="grid two"><div class="card"><h2>1. Match Setup</h2>${select('Liga', 'leagueId', state.leagues)}${select('Eigene Mannschaft', 'ownTeamId', state.teams)}${select('Gegner', 'opponentTeamId', state.teams.filter(t => t.id !== state.ownTeamId))}<h3>Eigene 4 Spieler</h3>${playersHtml(ownPlayers, state.ownPlayerIds, 'own')}<button class="primary" data-action="analyze" ${state.ownPlayerIds.length !== 4 || state.loading ? 'disabled' : ''}>${state.loading ? 'Berechne …' : 'Analyse starten'}</button></div><div class="card highlight"><h2>2. Gegner-Prognose</h2>${predictionHtml()}</div></section><section class="grid two"><div class="card"><h2>3. Tatsächliche Gegner stehen fest</h2><p class="muted">Wähle die vier tatsächlich angetretenen Gegner. Die gegnerische Positionierung bleibt geheim.</p>${playersHtml(opponentPlayers, state.actualOpponentIds, 'opp')}${actual.length === 4 ? `<div class="success">Aufstellung erkannt: ${actual.map(p => p.name).join(', ')}</div>` : `<div class="empty">${actual.length}/4 Gegner ausgewählt.</div>`}</div><div class="card"><h2>4. Analyse View</h2>${analysisHtml(actual, variants)}</div></section>`;
  bind();
}
function bind() {
  app.querySelectorAll('select').forEach(el => el.addEventListener('change', e => { state[e.target.dataset.field] = e.target.value; if (e.target.dataset.field === 'ownTeamId') state.ownPlayerIds = team(state.ownTeamId).players.slice(0,4); if (e.target.dataset.field === 'opponentTeamId') { state.actualOpponentIds = []; state.predictions = []; } render(); }));
  app.querySelectorAll('[data-player]').forEach(el => el.addEventListener('click', e => togglePlayer(e.currentTarget.dataset.player, e.currentTarget.dataset.group)));
  app.querySelector('[data-action="analyze"]')?.addEventListener('click', async () => { state.loading = true; render(); await new Promise(r => setTimeout(r, 350)); state.predictions = buildOpponentCombinationPredictions(team(state.opponentTeamId).players.map(player), await provider.getHistoricalCombinations(state.opponentTeamId)); state.loading = false; render(); });
}
function togglePlayer(id, group) { const key = group === 'own' ? 'ownPlayerIds' : 'actualOpponentIds'; state[key] = state[key].includes(id) ? state[key].filter(x => x !== id) : state[key].length < 4 ? [...state[key], id] : state[key]; render(); }
function predictionHtml() { if (state.loading) return '<div class="empty">Berechnung läuft …</div>'; if (!state.predictions.length) return '<div class="empty">Starte die Analyse, um gegnerische Vierer-Kombinationen zu berechnen.</div>'; return `<p class="muted">Positionsgeheimnis aktiv: keine gegnerischen Positionen vor Matchbeginn.</p><ol class="prediction-list">${state.predictions.slice(0,5).map((p,i)=>`<li><span>${i+1}. ${p.playerIds.map(id=>player(id).name).join(', ')}</span><strong>${pct(p.probability)}</strong><small>Verfügbarkeit ${Math.round(p.factors.availability*100)} % · RC-Faktor ${Math.round(p.factors.rcStrength*100)} % · Historie ${Math.round(p.factors.historicalUsage*100)} %</small></li>`).join('')}</ol>`; }
function analysisHtml(actual, variants) { if (actual.length !== 4) return '<div class="empty">Nach Auswahl der vier Gegner erscheinen mögliche Positionsvarianten und Stärkedaten.</div>'; return `<div class="strength-grid">${actual.map(p=>`<div class="player-stat"><strong>${p.name}</strong><span>RC ${p.rcRating}</span><small>Erwartete Stärke ${Math.round(p.rcRating/18)}</small></div>`).join('')}</div><h3>Mögliche gegnerische Aufstellungen</h3><ol class="prediction-list">${variants.map(v=>`<li><span>${v.playerIds.map((id,i)=>`${i+1}. ${player(id).name}`).join(' · ')}</span><strong>${pct(v.probability)}</strong></li>`).join('')}</ol>`; }
function playersHtml(players, selected, group) { return `<div class="players">${players.map(p=>`<button class="player ${selected.includes(p.id)?'selected':''}" data-player="${p.id}" data-group="${group}" ${!p.active && !selected.includes(p.id) ? 'disabled' : ''}><span>${p.name}</span><small>RC ${p.rcRating}${p.active ? '' : ' · nicht verfügbar'}</small></button>`).join('')}</div>`; }
function select(label, field, items) { return `<label>${label}<select data-field="${field}">${items.map(i=>`<option value="${i.id}" ${state[field]===i.id?'selected':''}>${i.name}</option>`).join('')}</select></label>`; }
function team(id) { return state.teams.find(t => t.id === id); }
function player(id) { return state.players.find(p => p.id === id); }
function pct(value) { return `${(value * 100).toFixed(1).replace('.', ',')} %`; }
