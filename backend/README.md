# Backend / XTTV Import

## 1. PostgreSQL starten

From the repository root:

```bash
docker compose up -d postgres
```

## 2. Python dependencies installieren

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
```

On Linux/macOS use `source .venv/bin/activate`.

## 3. Einen einzelnen XTTV-Spielbericht importieren

For the first real-world test we deliberately import only one match:

```bash
PYTHONPATH=backend python -m app 437757
```

Windows PowerShell:

```powershell
$env:PYTHONPATH="backend"
python -m app 437757
```

The importer currently does three things:

1. downloads `https://oettv.xttv.at/ed/index.php?meid=437757`;
2. stores the complete source document unchanged in `raw_source_documents`;
3. stores a first-pass structured representation in `xttv_matches`, `match_players` and `match_games`.

The structured parser is intentionally conservative at this stage. We need one real response from the user's environment to verify the exact XTTV HTML markup before locking down selectors for teams, positions, singles, doubles and set scores.

## Spielsystem / Ergebnisregeln

Für das unterstützte 4-Spieler-Spielsystem gelten diese Regeln verbindlich:

- Es gibt immer **genau 2 Doppel**.
- Es gibt **mindestens 8 Einzel**.
- Das Mannschaftsspiel endet, sobald ein Team **8 Siege** erreicht.
- Bei einem Gleichstand 7:7 werden alle 12 Einzel gespielt.
- Die gültigen Endstände und die daraus resultierende Einzelanzahl sind:
  - `10:0` → 8 Einzel + 2 Doppel
  - `9:1` → 8 Einzel + 2 Doppel
  - `8:2` → 8 Einzel + 2 Doppel
  - `8:3` → 9 Einzel + 2 Doppel
  - `8:4` → 10 Einzel + 2 Doppel
  - `8:5` → 11 Einzel + 2 Doppel
  - `8:6` → 12 Einzel + 2 Doppel
  - `7:7` → 12 Einzel + 2 Doppel

Ergebnisse wie `10:1`, `10:2`, `9:2`, `8:0`, `8:1` oder `8:7` sind für dieses Spielsystem ungültig. Der XTTV-Parser validiert diese Regeln beim Import.

## Database connection

Default local connection:

```text
postgresql+psycopg://tt:tt_dev@localhost:5432/tt_aufstellung
```

Set `DATABASE_URL` to override it.
