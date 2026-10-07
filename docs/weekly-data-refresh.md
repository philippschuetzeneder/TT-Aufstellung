# Wöchentliches Daten-Update (Prod)

Stand: August 2026

## Zeitplan

| | |
|---|---|
| **Rhythmus** | Montag, **04:00** (`Europe/Vienna`) |
| **Erster Lauf** | **5. Oktober 2026** (Montag) |
| **Sommerpause** | Vor dem 5.10.2026 passiert auf Prod **nichts** |

Der 5.10.2026 ist bewusst gewählt: Saisonwechsel im Import (`2026/2027` statt `2023/2024` in der 3-Saison-Fenster-Logik) und erster wöchentlicher Lauf fallen zusammen.

## Ablauf

1. Cron auf dem VPS startet `scripts/weekly-data-refresh-prod.sh`
2. Skript prüft Sommerpause (`DATA_REFRESH_NOT_BEFORE`, Standard `2026-10-05`)
3. Im App-Container: `run_data_refresh()` — inkrementeller XTTV-MEID-Scan, RC-Nachzug für Spieler aus neuen Berichten (nur **neue** RC-Snapshot-Zeilen wenn Historie schon da; voller Import nur ohne Snapshots), Analysis-Cache
4. Bei neuen Daten: `docker compose -f docker-compose.prod.yml restart app`
5. **Eine Report-E-Mail** an `p.schuetzeneder@gmail.com` (Status grün/rot, alle Kennzahlen + JSON-Anhang im Body)

Lokal (Windows): weiterhin `scripts/weekly-data-refresh.ps1` per Task Scheduler; ohne Sommerpause, sofern `DATA_REFRESH_NOT_BEFORE` nicht gesetzt ist.

## Prod-Installation

Auf dem VPS (`/opt/tt-aufstellung`), nach Deploy:

```bash
sudo bash scripts/install-weekly-refresh-cron.sh
```

Manueller Test (Dry-Run der Sommerpause — sollte sofort exit 0 mit „skipped“):

```bash
DATA_REFRESH_NOT_BEFORE=2026-10-05 bash scripts/weekly-data-refresh-prod.sh
```

Nach dem 5.10.2026 (oder mit überschriebenem Datum zum Testen):

```bash
DATA_REFRESH_NOT_BEFORE=2026-01-01 bash scripts/weekly-data-refresh-prod.sh
```

## Prod-Umgebungsvariablen (`.env`)

```env
# Blockiert auch manuellen Refresh per API bis einschließlich 4.10.2026
DATA_REFRESH_NOT_BEFORE=2026-10-05

# Empfänger (dein privates Postfach)
REFRESH_REPORT_EMAIL_TO=p.schuetzeneder@gmail.com

# Absender: eigene Adresse vom VPS (Postfix), nicht Gmail
SMTP_HOST=172.19.0.1
SMTP_PORT=25
SMTP_USE_TLS=0
SMTP_USE_AUTH=0
SMTP_FROM=tt-aufstellung@tt-aufstellung.at
```

Postfix-Einrichtung auf dem VPS (einmalig):

```bash
sudo bash deploy/postfix/setup-postfix.sh
docker compose -f docker-compose.prod.yml up -d --build
```

Damit Gmail die Mails annimmt, in **Cloudflare** einen SPF-Eintrag setzen:

```
Typ: TXT   Name: @
Wert:  v=spf1 ip4:159.195.240.88 -all
```

SMTP-Test:

```bash
bash scripts/test-refresh-report-email.sh
```

Die E-Mail enthält:

- Status **OK** (grün) oder **FEHLER** (rot)
- XTTV: importiert / geprüft / Fehler / MEID-Bereich / importierte MEIDs
- RC: neu gemappt / RC-Historie importiert / Fehler
- Analysis-Cache, App-Neustart, Laufzeit
- vollständiges JSON am Ende

Ohne diese Variable ist der Refresh in der App **nicht** pausiert (nur das Shell-Skript hat weiterhin den Shell-Default `2026-10-05`).

## XTTV Saison 2026/2027 (sjid=26)

Quelle: [OÖTTV Ergebnisdienst sjid=26](https://oettv.xttv.at/ed/index.php?oid=191&sjid=26)

| Parameter | Wert | Bedeutung |
|---|---|---|
| `oid=191` | OÖTTV | Oberösterreichischer TT-Verband |
| `sjid=26` | 2026/2027 | Neues Spieljahr (sjid=25 → 2025/2026) |
| `lid=8794…` | neue Liga-IDs | Saison 26/27 hat **neue** `lid`-Werte (z. B. `8794` OÖ-Liga, `8815` RK Linz) |

**Spielplan „Alle Spiele“** (pro Liga):

```
/ed/index.php?oid=191&sjid=26&do=spiele&showMenu=Y&lid=<LID>&zeit=all&limit=500
```

`zeit=all` entspricht dem UI-Filter „Alle Spiele“ (im HTML als `<input type="hidden" name="zeit" value="all">`).

Stand August 2026: **54 Ligen** sichtbar, Spielpläne teils schon online, aber noch **keine `meid`-Links** in den Terminlisten — Saisonstart steht bevor. Der produktive Import nutzt weiterhin den **inkrementellen MEID-Vorwärts-Scan** (`xttv_db_import.import_new_reports`), nicht den Liga-Spielplan-Crawl.

Saison-Filter im Import (`get_target_seasons()`):

- bis 4.10.2026: `2025/2026`, `2024/2025`, `2023/2024`
- ab 5.10.2026: `2026/2027`, `2025/2026`, `2024/2025`

Probe-Skript: `scripts/probe_xttv_sjid26.py`
