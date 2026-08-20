# TT-Aufstellung

Mobile-first MVP zur Optimierung einer Tischtennis-Mannschaftsaufstellung.

## Lokale Entwicklung (Cursor)

Vollständige Anleitung: [`docs/local-development.md`](docs/local-development.md)

```powershell
.\scripts\setup-local.ps1
.\scripts\clone-render-db.ps1   # optional: Render-Daten read-only klonen
.\scripts\start-dev.ps1
```

GUI: http://localhost:10000/

## Demo-Start (nur Frontend, ohne Backend-Daten)

```bash
npm install
npm run dev
```

## Prüfen

```bash
npm test
npm run build
```

Die aktuelle Anwendung läuft bewusst mit Demo-/Seed-Daten. Der XTTV-Import ist sauber als spätere Datenquelle vorgesehen und wird nicht live aus der UI aufgerufen. Architektur- und Importnotizen liegen in [`docs/architecture-plan.md`](docs/architecture-plan.md) und [`docs/xttv-data-structure.md`](docs/xttv-data-structure.md).
