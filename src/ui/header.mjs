import { adminRequestHeaders, applyAdminTokenFromUrl, bindAdminTokenInput, readAdminToken, storeAdminToken } from './admin-auth.mjs';

/** Set true to show Admin-Token field and Daten-Refresh in the header again. */
export const SHOW_HEADER_ADMIN_UI = false;

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

function leagueLabel(leagueItem) {
  if (!leagueItem) return 'Liga wählen';
  return `${leagueItem.name}${leagueItem.season ? ` ${leagueItem.season}` : ''} (${leagueItem.match_count})`;
}

function normalizeLeagueSearch(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/ä/g, 'a')
    .replace(/ö/g, 'o')
    .replace(/ü/g, 'u')
    .replace(/ß/g, 'ss')
    .trim();
}

function leagueSearchText(leagueItem) {
  return normalizeLeagueSearch(`${leagueItem.id} ${leagueItem.name} ${leagueItem.season || ''} ${leagueItem.match_count || ''}`);
}

function filterLeagueModal(query) {
  const list = document.querySelector('#header-league-modal-list');
  if (!list) return;
  const normalized = normalizeLeagueSearch(query);
  const tokens = normalized.split(/\s+/).filter(Boolean);
  let visible = 0;
  list.querySelectorAll('.header-league-option').forEach((button) => {
    const haystack = button.dataset.searchText || '';
    const match = !tokens.length || tokens.every((token) => haystack.includes(token));
    button.hidden = !match;
    if (match) visible += 1;
  });
  const empty = list.querySelector('.header-league-empty');
  if (empty) empty.hidden = visible > 0;
}

function closeLeagueModal() {
  const modal = document.querySelector('#header-league-modal');
  if (!modal) return;
  modal.hidden = true;
  document.body.classList.remove('league-modal-open');
  const search = document.querySelector('#header-league-search');
  if (search) search.value = '';
  filterLeagueModal('');
}

function openLeagueModal() {
  const modal = document.querySelector('#header-league-modal');
  if (!modal) return;
  modal.hidden = false;
  document.body.classList.add('league-modal-open');
  const search = document.querySelector('#header-league-search');
  if (search) {
    search.value = '';
    filterLeagueModal('');
    window.setTimeout(() => search.focus(), 0);
  }
  const active = modal.querySelector('.header-league-option.is-active:not([hidden])');
  if (active) active.scrollIntoView({ block: 'nearest' });
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
  const refreshEnabled = SHOW_HEADER_ADMIN_UI && showDataRefresh;
  const refreshBtn = refreshEnabled
    ? `<button type="button" class="header-btn header-btn-warn header-btn-refresh" id="header-data-refresh" ${disabled || dataRefreshRunning ? 'disabled' : ''} title="Daten Refresh">${dataRefreshRunning ? 'Refresh …' : 'Refresh'}</button>`
    : '';
  if (refreshHost) {
    refreshHost.innerHTML = refreshBtn;
  }
  if (!host) return;
  const leagueList = leagues || [];
  const current = leagueList.find((l) => l.id === league);
  const options = leagueList.map(
    (l) => `<option value="${escapeHtml(l.id)}" ${league === l.id ? 'selected' : ''}>${escapeHtml(leagueLabel(l))}</option>`,
  ).join('');
  const modalOptions = leagueList.map(
    (l) => `<button type="button" class="header-league-option${league === l.id ? ' is-active' : ''}" data-league-id="${escapeHtml(l.id)}" data-search-text="${escapeHtml(leagueSearchText(l))}" ${disabled || dataRefreshRunning ? 'disabled' : ''}><span class="header-league-option-name">${escapeHtml(l.name)}${l.season ? ` <span class="header-league-option-season">${escapeHtml(l.season)}</span>` : ''}</span><span class="header-league-option-meta">${l.match_count} Spiele</span></button>`,
  ).join('');
  const controlsNav = navHost ? '' : navLink;
  const controlsRefresh = refreshHost ? '' : refreshBtn;
  const adminTokenHtml = SHOW_HEADER_ADMIN_UI && adminRequired
    ? `<label class="header-admin-token"><span class="header-admin-token-label">Admin-Token</span><input type="password" id="header-admin-token" class="header-admin-token-input" placeholder="für Daten-Refresh" value="${escapeHtml(readAdminToken())}" ${disabled || dataRefreshRunning ? 'disabled' : ''} autocomplete="off"></label>`
    : '';
  host.innerHTML = `
    ${controlsRefresh}
    ${adminTokenHtml}
    <div class="header-league">
      <span class="header-league-label">Liga-Auswahl</span>
      <button type="button" class="header-league-trigger" id="header-league-trigger" ${disabled || dataRefreshRunning ? 'disabled' : ''} aria-haspopup="dialog" aria-controls="header-league-modal">
        <span class="header-league-trigger-text">${escapeHtml(leagueLabel(current))}</span>
        <span class="header-league-trigger-chevron" aria-hidden="true"></span>
      </button>
      <select id="header-league" class="header-league-select header-league-select-native" ${disabled || dataRefreshRunning ? 'disabled' : ''} aria-hidden="true" tabindex="-1">${options}</select>
      <div id="header-league-modal" class="header-league-modal" hidden>
        <button type="button" class="header-league-modal-backdrop" id="header-league-modal-backdrop" aria-label="Schließen"></button>
        <div class="header-league-modal-panel" role="dialog" aria-modal="true" aria-labelledby="header-league-modal-title">
          <div class="header-league-modal-head">
            <h2 id="header-league-modal-title">Liga wählen</h2>
            <input type="search" id="header-league-search" class="header-league-search" placeholder="Liga suchen …" autocomplete="off" enterkeyhint="search">
            <button type="button" class="header-league-modal-close" id="header-league-modal-close" aria-label="Schließen">×</button>
          </div>
          <div class="header-league-modal-list" id="header-league-modal-list">${modalOptions}<p class="header-league-empty" hidden>Keine Liga gefunden.</p></div>
        </div>
      </div>
    </div>
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
  const trigger = document.querySelector('#header-league-trigger');
  const modal = document.querySelector('#header-league-modal');
  const backdrop = document.querySelector('#header-league-modal-backdrop');
  const closeBtn = document.querySelector('#header-league-modal-close');
  const searchInput = document.querySelector('#header-league-search');
  if (!select || typeof onChange !== 'function') return;

  select.addEventListener('change', () => onChange(select.value));

  if (searchInput) {
    searchInput.addEventListener('input', () => filterLeagueModal(searchInput.value));
    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeLeagueModal();
      }
    });
  }

  if (trigger) {
    trigger.addEventListener('click', () => {
      if (trigger.disabled) return;
      openLeagueModal();
    });
  }

  modal?.querySelectorAll('.header-league-option').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.disabled) return;
      const leagueId = button.dataset.leagueId;
      if (!leagueId || leagueId === select.value) {
        closeLeagueModal();
        return;
      }
      select.value = leagueId;
      closeLeagueModal();
      onChange(leagueId);
    });
  });

  backdrop?.addEventListener('click', closeLeagueModal);
  closeBtn?.addEventListener('click', closeLeagueModal);
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
      <div class="refresh-report-top">
        <button type="button" class="refresh-back-btn" id="refresh-report-back">← Zurück zur Hauptseite</button>
      </div>
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
