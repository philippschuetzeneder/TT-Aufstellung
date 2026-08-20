# Lokale Entwicklung (Cursor)

Diese Anleitung bringt Backend, PostgreSQL und die Web-GUI lokal zum Laufen — ohne die Render-Produktionsdatenbank zu verändern.

## Voraussetzungen

1. **Python 3.12** — `winget install Python.Python.3.12`
2. **Docker Desktop** — für lokales PostgreSQL (`docker compose`)
3. Optional: **Node.js** — nur für `npm test` / `npm run build`, nicht für den normalen App-Start

## Ersteinrichtung

```powershell
cd C:\Projects\tt-aufstellung\TT-Aufstellung
.\scripts\setup-local.ps1
```

Das Skript erstellt `.venv`, installiert Python-Dependencies und startet Postgres via Docker Compose.

## Render-Daten lokal klonen (read-only)

1. Render Dashboard â†’ PostgreSQL â†’ **External Database URL** kopieren
2. In `.env` eintragen (nicht committen):

```env
RENDER_DATABASE_URL=postgres://...
```

3. Klon ausfÃ¼hren:

```powershell
.\scripts\clone-render-db.ps1
```

`pg_dump` liest nur von Render. Schreiboperationen betreffen nur die lokale Docker-DB.

## App starten

```powershell
.\scripts\start-dev.ps1
```

GUI: [http://localhost:10000/](http://localhost:10000/)

Health-Checks:

- [http://localhost:10000/health](http://localhost:10000/health)
- [http://localhost:10000/api/db/health](http://localhost:10000/api/db/health)
- [http://localhost:10000/api/teams](http://localhost:10000/api/teams)

## Daten prÃ¼fen

```powershell
.\scripts\verify-local-db.ps1
```

Weitere Validierung Ã¼ber die API (Server muss laufen):

- `/api/db/validate` â€” XTTV-Import-Konsistenz
- `/api/analytics/validate` â€” Analytics-/Lineup-Konsistenz
- `/api/xttv/player-master-status` â€” Spielerstamm-Abdeckung

## Umgebungsvariablen

| Variable | Default | Zweck |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://tt:tt_dev@localhost:5432/tt_aufstellung` | Lokale DB |
| `PORT` | `10000` | Web-Server-Port |
| `RENDER_DATABASE_URL` | â€” | Nur fÃ¼r `clone-render-db.ps1` |

## Render vs. lokal

| | Render | Lokal |
|---|---|---|
| Start | `PYTHONPATH=backend python -m app.web` | `.\scripts\start-dev.ps1` |
| DB | Render PostgreSQL (`DATABASE_URL` im Dashboard) | Native Postgres oder Docker (`docker-compose.yml`) |
| Schema | `create_all()` beim Start | identisch |


## Native PostgreSQL (winget) statt Docker

Wenn PostgreSQL lokal installiert ist (`winget install PostgreSQL.PostgreSQL.17`), laeuft der Dienst typischerweise als `postgresql-x64-17`. Der Installer setzt ein **unbekanntes Superuser-Passwort** fuer `postgres` und `pg_hba.conf` erlaubt nur `scram-sha-256` fuer TCP (`127.0.0.1` / `::1`). Dann haengen `psql -U postgres` und `restore-local-db.ps1` an der Passwortabfrage.

**Option A (empfohlen fuer lokale Dev, nur localhost):** In `C:\Program Files\PostgreSQL\17\data\pg_hba.conf` die Zeilen fuer `127.0.0.1/32` und `::1/128` temporaer auf `trust` setzen, Konfiguration neu laden (`SELECT pg_reload_conf();` als Admin oder Dienst neu starten). Vorher Backup der Datei anlegen. **Wichtig:** Datei ohne UTF-8-BOM speichern (PowerShell `Set-Content -Encoding UTF8` fuegt BOM hinzu und PostgreSQL kann `pg_hba.conf` dann nicht laden).

**Option B:** Bekanntes Superuser-Passwort in `.env` setzen (nicht committen):

```env
POSTGRES_SUPERUSER_PASSWORD=...
```

**Dump wiederherstellen** (nach Auth-Fix):

```powershell
.\scripts\restore-local-db.ps1
.\scripts\verify-local-db.ps1
```

App-User: Rolle `tt`, Passwort `tt_dev`, Datenbank `tt_aufstellung`. In `.env` am besten `127.0.0.1` statt `localhost` (IPv6).

## Typische Probleme

**Docker nicht installiert** â€” Docker Desktop installieren oder PostgreSQL 16+ lokal einrichten mit DB `tt_aufstellung`, User `tt`, Passwort `tt_dev`.

**GUI zeigt â€žFehlerâ€œ / leere Teams** â€” Datenbank leer oder nicht erreichbar. `verify-local-db.ps1` ausfÃ¼hren oder Render-Daten klonen.

**Python nicht gefunden** â€” nach Installation Terminal neu Ã¶ffnen oder venv-Pfad nutzen: `.venv\Scripts\python.exe`.
