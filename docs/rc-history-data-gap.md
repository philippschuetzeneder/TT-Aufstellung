# RC-Historie in der Datenbank (Stand Investigation)

## Symptom

Statistik/Profil zeigen keinen RC-Trend und „Keine ausreichenden RC-Snapshots“, obwohl Spieler gemappt sind und RC-Aktuellwerte sichtbar sind.

## Ursache (Daten, kein UI-Totalausfall)

Trend und RC-Kurve brauchen **mindestens 2** Einträge in `player_rating_snapshots` (Quelle `ratingscentral`).

Untersuchung (lokal, `scripts/investigate_rc_regression.py`):

- ~**3 551** gemappte Spieler mit **genau 1** Snapshot
- Deren `imported_at` liegt fast alle auf **2026-08-20** (ein Batch)
- Das passt zu **`sync_current_ratings_from_index`**: schreibt **einen** aktuellen RC-Wert pro Spieler aus dem RC-Index, **keine** Event-Historie von `PlayerHistory.php`
- Volle Historie entsteht durch **`import_rc_player`** (lädt `PlayerHistory.php`, viele Snapshots)

Spieler wie **Philipp Schützeneder** (Import **2026-08-17**, ~53 Snapshots) zeigen: Das System funktioniert, wenn die Historie importiert wurde.

## Was am 20.08. vermutlich passiert ist

Exaktes Skript-Log auf Prod/lokal nicht archiviert; technisch passt nur:

- Aufruf von **`GET /api/rc/sync-current-ratings`** (`sync_current_ratings_from_index`), **oder**
- ein Batch, der den Index in Snapshots überführt, **ohne** `import_history=true`

Relevant im Repo:

- `scripts/apply-rc-matches.ps1` — Standard **`ImportHistory = $false`** (nur Mapping, keine Historie)
- `scripts/run_rc_apply_and_report.py` — `apply_matches_all(..., import_history=False)`
- `data_refresh_service.run_data_refresh` — RC-Historie nur für Spieler aus **neuen** MEIDs, nicht für alle Gemappten

Nach **DB-Clone von Render** (z. B. `clone-render-db.ps1`) war die Historie oft schon lückenhaft; der 20.08.-Lauf hat vielen Spielern **nur den Index-Stand** gegeben, nicht die Events.

## Reparatur (lokal, vor Prod)

```powershell
.\scripts\load-env.ps1
$env:PYTHONPATH = "backend"
$env:RC_FETCH_TIMEOUT = "45"
$env:RC_BACKFILL_PAUSE = "3"
.\.venv\Scripts\python.exe .\scripts\backfill_all_rc_history.py
.\.venv\Scripts\python.exe .\scripts\audit_rc_snapshots.py --global
.\.venv\Scripts\python.exe .\scripts\check_player_rc.py "%Friedinger%Niklas%"
```

Phase 1 nutzt gecachtes HTML (`raw_source_documents`, `playerhistory:*`), Phase 2 holt fehlende Historien vom Netz (dauert Stunden, resumable via `data/rc_backfill_progress.json`).

**Prod:** Erst lokal verifizieren, dann denselben Backfill gegen Prod-DB oder vor Deploy auf frischer Kopie.
