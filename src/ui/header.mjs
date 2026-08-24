import { adminRequestHeaders, applyAdminTokenFromUrl, bindAdminTokenInput, readAdminToken, storeAdminToken } from './admin-auth.mjs';

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

export function renderHeaderLeague(leagues, league, {
  disabled = false,
  backHref = '/statistiken.html',
  backLabel = 'Statistiken',
  showDataRefresh = false,
  dataRefreshRunning = false,
  adminRequired = false,
} = {}) {
  const host = document.querySelector('#header-controls');
  const navHost = document.querySelector('#header-nav');
  const refreshHost = document.querySelector('#header-refresh');
  const navLink = backHref && backLabel
    ? `<a class="header-btn header-btn-compact" href="${escapeHtml(backHref)}">${escapeHtml(backLabel)}</a>`
    : '';
  if (navHost) {
    navHost.innerHTML = navLink;
  }
  const refreshBtn = showDataRefresh
    ? `<button type="button" class="header-btn header-btn-warn header-btn-refresh" id="header-data-refresh" ${disabled || dataRefreshRunning ? 'disabled' : ''} title="Daten Refresh">${dataRefreshRunning ? 'Refresh …' : 'Refresh'}</button>`
    : '';
  if (refreshHost) {
    refreshHost.innerHTML = refreshBtn;
  }
  if (!host) return;
  const options = (leagues || []).map(
    (l) => `<option value="${escapeHtml(l.id)}" ${league === l.id ? 'selected' : ''}>${escapeHtml(l.name)}${l.season ? ` ${l.season}` : ''} (${l.match_count})</option>`,
  ).join('');
  const controlsNav = navHost ? '' : navLink;
  const controlsRefresh = refreshHost ? '' : refreshBtn;
  const adminTokenHtml = adminRequired
    ? `<label class="header-admin-token"><span class="header-admin-token-label">Admin-Token</span><input type="password" id="header-admin-token" class="header-admin-token-input" placeholder="für Analyse & Refresh" value="${escapeHtml(readAdminToken())}" ${disabled || dataRefreshRunning ? 'disabled' : ''} autocomplete="off"></label>`
    : '';
  host.innerHTML = `
    ${controlsRefresh}
    ${adminTokenHtml}
    <label class="header-league">
      <span class="header-league-label">Liga</span>
      <select id="header-league" class="header-league-select" ${disabled || dataRefreshRunning ? 'disabled' : ''}>${options}</select>
    </label>
    ${controlsNav}
  `;
}

export function bindHeaderRefresh(onRefresh) {
  const button = document.querySelector('#header-data-refresh');
  if (!button || typeof onRefresh !== 'function') return;
  button.addEventListener('click', () => onRefresh());
}

export function bindHeaderAdminToken() {
  bindAdminTokenInput(document.querySelector('#header-admin-token'));
}

export async function fetchAdminRequired() {
  try {
    const response = await fetch('/api/auth/status');
    const data = await response.json();
    return Boolean(data?.admin_required);
  } catch {
    return false;
  }
}

export async function fetchRefreshApi(restart = true) {
  const response = await fetch(`/api/data/refresh?restart=${restart ? '1' : '0'}`, {
    headers: adminRequestHeaders(),
  });
  const contentType = response.headers.get('content-type') || '';
  const body = await response.text();
  if (!contentType.includes('application/json')) {
    const isHtml = body.trimStart().startsWith('<!DOCTYPE') || body.trimStart().startsWith('<html');
    throw new Error(
      isHtml
        ? 'Server antwortet mit HTML statt JSON — Backend neu starten (.\\scripts\\start-dev.ps1), dann Seite neu laden.'
        : `Unerwartete Antwort (HTTP ${response.status}).`,
    );
  }
  let data;
  try {
    data = JSON.parse(body);
  } catch {
    throw new Error('Antwort ist kein gültiges JSON — Backend neu starten (.\\scripts\\start-dev.ps1).');
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Refresh fehlgeschlagen (HTTP ${response.status})`);
  }
  return data;
}

export function bindHeaderLeague(onChange) {
  const select = document.querySelector('#header-league');
  if (!select || typeof onChange !== 'function') return;
  select.addEventListener('change', () => onChange(select.value));
}

function refreshDetailRow(label, value) {
  return `<div class="refresh-detail"><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`;
}

function formatMeidList(meids) {
  if (!meids?.length) return '<span class="muted">—</span>';
  return escapeHtml(meids.join(', '));
}

export function renderRefreshReport(target, data, { onBack } = {}) {
  const summary = data.summary || {};
  const xttv = summary.xttv || {};
  const rc = summary.rc || {};
  const cache = summary.analysis_cache || {};
  const failed = data.ok === false;

  const xttvRows = [
    refreshDetailRow('Neue Berichte', escapeHtml(String(xttv.imported ?? 0))),
    refreshDetailRow('Geprüfte IDs', escapeHtml(String(xttv.checked ?? 0))),
  ];
  if (xttv.last_known_meid != null) {
    xttvRows.push(refreshDetailRow('Letzte bekannte MEID', escapeHtml(String(xttv.last_known_meid))));
  }
  if (xttv.max_imported_meid != null) {
    xttvRows.push(refreshDetailRow('Höchste importierte MEID', escapeHtml(String(xttv.max_imported_meid))));
  }
  if (xttv.scan_frontier_after != null) {
    xttvRows.push(refreshDetailRow('Scan-Grenze (gespeichert)', escapeHtml(String(xttv.scan_frontier_after))));
  }
  if (xttv.range) {
    xttvRows.push(refreshDetailRow('Scan-Bereich', escapeHtml(`${xttv.range.start}–${xttv.range.end}`)));
  }
  if (xttv.imported_meids?.length) {
    xttvRows.push(refreshDetailRow('Importierte MEIDs', formatMeidList(xttv.imported_meids)));
  }
  if (xttv.stopped_after_empty_streak) {
    xttvRows.push(refreshDetailRow('Abbruch', '25× hintereinander 404 oder „noch nicht vorhanden“'));
  }
  if (xttv.errors) {
    xttvRows.push(refreshDetailRow('Fehler', escapeHtml(String(xttv.errors))));
  }

  const rcHtml = rc.skipped
    ? `<p class="muted">${escapeHtml(rc.reason === 'no_new_reports' ? 'Übersprungen — keine neuen Spielberichte.' : 'Übersprungen.')}</p>`
    : `<dl class="refresh-dl">
        ${refreshDetailRow('Neu zugeordnet', escapeHtml(String(rc.newly_mapped ?? 0)))}
        ${refreshDetailRow('RC-Historie importiert', escapeHtml(String(rc.imported ?? 0)))}
        ${refreshDetailRow('Spieler mit RC', escapeHtml(String(rc.targets ?? 0)))}
        ${rc.errors ? refreshDetailRow('Fehler', escapeHtml(String(rc.errors))) : ''}
      </dl>`;

  const cacheHtml = cache.skipped
    ? '<p class="muted">Übersprungen — keine Datenänderung.</p>'
    : `<p>${cache.ok !== false ? 'Aktualisiert.' : 'Fehler beim Aktualisieren.'}</p>`;

  let restartNote = '';
  if (data.restart_scheduled) {
    restartNote = '<p class="refresh-note refresh-note-warn">Server-Neustart wurde geplant. Beim Zurückgehen wird die Hauptseite neu geladen.</p>';
  } else if (data.restart_note) {
    restartNote = `<p class="refresh-note muted">${escapeHtml(data.restart_note)}</p>`;
  }

  const badge = failed
    ? '<span class="refresh-badge refresh-badge-error">Fehler</span>'
    : `<span class="refresh-badge ${data.data_changed ? 'refresh-badge-ok' : 'refresh-badge-neutral'}">${data.data_changed ? 'Daten geändert' : 'Keine Änderungen'}</span>`;

  const lead = failed
    ? escapeHtml(data.error || 'Refresh fehlgeschlagen.')
    : escapeHtml(data.message || 'Refresh abgeschlossen.');

  target.innerHTML = `
    <section class="card refresh-report">
      <div class="refresh-report-head">
        <h2>${failed ? 'Daten-Refresh fehlgeschlagen' : 'Daten-Refresh abgeschlossen'}</h2>
        ${badge}
      </div>
      <p class="refresh-report-lead">${lead}</p>
      ${data.elapsed_seconds != null ? `<p class="muted refresh-report-meta">Dauer: ${escapeHtml(String(data.elapsed_seconds))} s</p>` : ''}
      <div class="refresh-report-grid">
        <div class="refresh-report-block">
          <h3>XTTV</h3>
          <dl class="refresh-dl">${xttvRows.join('')}</dl>
        </div>
        <div class="refresh-report-block">
          <h3>Ratings Central</h3>
          ${rcHtml}
        </div>
        <div class="refresh-report-block">
          <h3>Analyse-Cache</h3>
          ${cacheHtml}
        </div>
      </div>
      ${restartNote}
      <div class="refresh-report-actions">
        <button type="button" class="refresh-back-btn" id="refresh-report-back">← Zurück zur Hauptseite</button>
      </div>
    </section>
  `;

  const backBtn = target.querySelector('#refresh-report-back');
  if (!backBtn) return;
  backBtn.addEventListener('click', () => {
    if (typeof onBack === 'function') {
      onBack(data);
      return;
    }
    window.location.href = '/';
  });
}
