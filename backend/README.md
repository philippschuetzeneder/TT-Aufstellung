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

## Database connection

Default local connection:

```text
postgresql+psycopg://tt:tt_dev@localhost:5432/tt_aufstellung
```

Set `DATABASE_URL` to override it.
