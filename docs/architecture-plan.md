# Architektur- und Entwicklungsplan: TT-Aufstellungsoptimierung

Stand: 2026-08-12

## 1. Zielbild und Scope dieses Dokuments

Dieses Dokument ist bewusst keine vollständige Implementierung. Es fasst die Anforderungen zusammen, dokumentiert erste Erkenntnisse zur öffentlich sichtbaren XTTV-Struktur und legt eine umsetzbare Gesamtarchitektur mit Datenmodell, Import-, Statistik-, API- und Frontend-Plan fest.

Das Produktziel ist eine mobile-first Web-Anwendung, in der ein Mannschaftsführer im Normalfall nur die gegnerische Mannschaft und vier eigene Spieler auswählt. Das Backend prognostiziert gegnerische Spieler-/Aufstellungswahrscheinlichkeiten, simuliert alle 24 eigenen Aufstellungen und empfiehlt die Aufstellung mit maximaler Mannschaftssieg-Wahrscheinlichkeit.

## 2. Verifizierte XTTV-Erkenntnisse

Die folgenden Punkte wurden anhand öffentlich auffindbarer Seiten geprüft:

- `xttv.at` beschreibt den XTTV-Ergebnisdienst als zentrale Quelle für ÖTTV-/Landesverbands-Meisterschaften inklusive Online-Ergebniseingabe, Spielberichten, Tabellen, Ranglisten, Spieler- und Vereinsinformationen.
- Direktlinks zu Landesverbänden laufen unter `oettv.xttv.at/ed/` und verwenden Parameter wie `oid` für Organisationen und `lid` für Ligen.
- Suchindex-Snippets zeigen Ergebnisdienst-URLs wie `index.php?oid=188`, `index.php?lid=7850` und Listen-/Spielansichten mit Parametern wie `do=spiele`, `uegID`, `highVid`, `seite`, `order` und `lang`.
- Aktuelle direkte Abrufe über das verfügbare Web-Tool lieferten für mehrere alte/indizierte XTTV-URLs eine Umleitung auf eine `404-file-not-found`-Seite auf `oettv-test.xttv.at`. Das ist ein zentrales Scraping-Risiko und muss vor einer Importer-Implementierung nochmals mit realem Browser/Netzwerkzugang, Session-/Cookie-Verhalten und aktuellen Einstiegspunkten überprüft werden.
- Öffentliche RC-/Ranglisteninformationen sind mindestens über Verbandsseiten verfügbar. Beispiel: die NÖTTV-Rangliste enthält Rang, Name, Punkte mit Unsicherheit, Verein und Datum `zuletzt gespielt`.
- Die Google-Play-Beschreibung der offiziellen XTTV-Mobile-App bestätigt, dass XTTV Mobile Zugriff auf Daten von Ligen und Vereinen bietet, die im XTTV-Ergebnisdienst verwaltet werden. Eine App kann auf nicht-dokumentierte Endpunkte hindeuten; deren Nutzung muss rechtlich/technisch geprüft werden.

Konsequenz: Der Importer darf nicht auf Vermutungen über eine stabile HTML-Struktur bauen. Phase 3 beginnt daher mit einem XTTV-Struktur-Spike, der aktuelle Seiten, Parameter, Cookies, Sprache, Tabellen, Links und mögliche JSON-/XHR-Endpunkte aufzeichnet, bevor Parser produktiv gebaut werden. Die detaillierten Ergebnisse dieses Struktur-Spikes sind in [`docs/xttv-data-structure.md`](xttv-data-structure.md) dokumentiert.

## 3. Gesamtarchitektur

Empfohlener Stack:

- Backend/API: Python 3.12, FastAPI, Pydantic, SQLAlchemy 2.x, Alembic.
- Datenbank: PostgreSQL; lokal über Docker Compose.
- Jobs: separater Python-Worker/CLI für Imports und spätere Modellläufe; Scheduling zuerst per GitHub Actions oder Render Cron Job, später Cloud Scheduler möglich.
- Statistik/ML: Python-Module im Backend-Monorepo; zunächst deterministische Baselines, später kalibrierte/hierarchische Modelle.
- Frontend: Next.js/React mit TypeScript, mobile-first CSS, API nur gegen eigenes Backend.
- Deployment: Docker-basierte Services `api`, `worker`, `frontend`, `postgres` für Entwicklung; Render-kompatible Container für Staging.

Komponenten:

1. XTTV Data Collector: lädt ausschließlich im geplanten Importprozess öffentliche XTTV-/Verbandsseiten.
2. Data Validation / ETL: normalisiert Daten, prüft Konsistenz und schreibt idempotent in PostgreSQL.
3. Database: versioniertes relationales Schema mit XTTV-IDs und Import-Metadaten.
4. RC Data: separater Import/Snapshot, da RC zeitabhängig und mit Unsicherheit genutzt wird.
5. Matchup Model: liefert `P(A schlägt B | Datenstand t)`.
6. Opponent Availability Model: liefert Wahrscheinlichkeiten gemeinsamer Viererkombinationen.
7. Opponent Lineup Prediction Model: liefert konsistente Verteilungen über gültige Gegner-Permutationen.
8. Own Lineup Optimization: bewertet alle 24 eigenen Permutationen gegen gewichtete Gegner-Szenarien.
9. Explanation Engine: generiert Gründe ausschließlich aus Modellbeiträgen und Simulationsdaten.
10. Backend API: Validierung, Szenarioverwaltung, Optimierung, Erklärungen, Import-/Health-Status.
11. Frontend: schneller mobiler Flow, keine XTTV-Kommunikation.
12. Scheduled Import: Montag 08:00, nachvollziehbare Logs und Retry-fähige Fehler.

## 4. Relationales Datenbankschema

Kernprinzipien:

- Jede externe Entität bekommt `xttv_*_id` bzw. `source_key` und eindeutige Constraints.
- Importe sind idempotent via Upserts und Rohdaten-Snapshots.
- Historische Vorhersagen nutzen `as_of`-Zeitpunkte; keine Future Leakage.
- RC-Ratings werden als Zeitreihe gespeichert, nicht als Feld am Spieler.

Vorgeschlagene Tabellen:

### Stammdaten

- `associations`: Verband/Landesverband, `xttv_oid`, Name, Kürzel.
- `clubs`: Verein, `xttv_club_id`, Verband, Name, Kürzel.
- `players`: Spieler, `xttv_player_id`, Name, Geburtsjahr optional, Geschlecht optional, Nationalität optional.
- `player_club_memberships`: zeitliche Vereins-/Mannschaftszuordnung.
- `seasons`: Saison, z. B. `2025/26`, Start/Ende.
- `leagues`: Liga/Gruppe, `xttv_lid`, Verband, Saison, Name, Spielsystem, RC-Grenze optional.
- `teams`: Mannschaft, Verein, Liga, Saison, Anzeigename, XTTV-ID/Key.

### Spiel- und Ergebnisdaten

- `team_matches`: Mannschaftsspiel, `xttv_match_id`, Liga, Runde, Datum, Heimteam, Auswärtsteam, Status, Ergebnis, Spielort.
- `lineups`: Teamaufstellung pro Mannschaftsspiel und Team, `home_away`, Quelle, bestätigt-Flag.
- `lineup_players`: Position 1-4, Spieler, Ersatz-/Kommentar-Felder.
- `singles_matches`: Einzel mit Match-Reihenfolge, Heimspieler, Auswärtsspieler, Positionen, Satz-/Punktergebnis, Sieger.
- `doubles_matches`: Doppel mit Spielerpaaren, Satz-/Punktergebnis, Sieger.
- `sets`: optionale Normalisierung einzelner Satzpunkte für Einzel/Doppel, falls XTTV Punktverhältnisse liefert.

### Ratings, Features und Modelle

- `rc_rating_snapshots`: Spieler, Quelle, Snapshot-Datum, Rating, Rating-Unsicherheit, Verein, zuletzt gespielt, Rohwert.
- `player_form_features`: materialisierte zeitpunktbezogene Features, reproduzierbar aus Matchdaten.
- `model_versions`: Modelltyp, Trainingszeitraum, Parameter, Metriken, Artefaktpfad.
- `prediction_runs`: API-/Backtest-Lauf mit `as_of`, Eingaben, Modellversionen und Ergebnis-JSON.
- `lineup_evaluations`: bewertete eigene Aufstellung mit Siegchance, erwarteten Punkten und Risiko-Kennzahlen.

### Import und Audit

- `import_runs`: Quelle, Start/Ende, Status, geladene/neue/aktualisierte Datensätze, Fehleranzahl, Commit/Version.
- `import_items`: einzelne URL/Entität, Status, Hash, HTTP-Status, Fehlermeldung, Retry-Zähler.
- `raw_source_documents`: URL, Inhalt-Hash, abgerufener Zeitpunkt, Content-Type, komprimierter Rohinhalt optional.

Wichtige Constraints:

- `UNIQUE(source, xttv_id)` für externe Entitäten.
- `UNIQUE(team_match_id, team_id)` für Lineups.
- `UNIQUE(lineup_id, position)` und `UNIQUE(lineup_id, player_id)`.
- `UNIQUE(source, snapshot_date, player_id)` für RC-Snapshots.
- `CHECK(position BETWEEN 1 AND 4)`.

## 5. XTTV-Importstrategie

Der Import läuft ausschließlich außerhalb normaler API-Requests.

Ablauf Montag 08:00:

1. `import_runs`-Eintrag öffnen.
2. Konfigurierte Testliga laden.
3. Liga-/Spielplanseite speichern und Linkstruktur extrahieren.
4. Für neue/geänderte Spielberichte Roh-HTML speichern.
5. Parser extrahieren Mannschaftsspiel, Mannschaften, Aufstellungen, Einzel, Doppel, Sätze und Status.
6. Validierung: genau vier Einzelspieler je Team, eindeutige Positionen, Ergebnis-Summen plausibel, Spielsystem erkannt.
7. Upsert in Normalformtabellen.
8. RC-Snapshot aus verifizierter Quelle importieren, falls verfügbar.
9. Import-Statistiken und Fehler persistieren.

Robustheitsregeln:

- Parser arbeiten adapterbasiert pro Seitentyp und enthalten Fixture-Tests mit real gespeicherten HTML-Beispielen.
- Jede URL bekommt Hash-Vergleich; unveränderte Dokumente müssen nicht neu geparst werden.
- Fehlgeschlagene Items werden isoliert markiert und blockieren nicht den gesamten Import.
- Rate Limiting, User-Agent, Backoff und Respekt vor `robots.txt`/Nutzungsbedingungen sind Pflicht.
- Falls XTTV dynamisch/XHR-basiert arbeitet, werden nur dokumentierte oder rechtlich unbedenklich nutzbare öffentliche Endpunkte verwendet.

## 6. ML-/Statistikarchitektur

### Matchup-Wahrscheinlichkeit

Startmodell:

- RC-only Baseline mit logistischer Elo-Formel: `P(A gewinnt) = 1 / (1 + 10^(-(RC_A - RC_B) / scale))`.
- `scale` wird aus historischen XTTV-Spielen validiert/kalibriert statt blind übernommen.

Erweiterungen ohne naives Doppelzählen:

- Regulierte logistische Regression oder Bayes-Modell mit RC-Differenz als starker Baseline-Kovariate.
- Zusatzfeatures modellieren nur Residualinformation: direkte Bilanz, Head-to-Head-Satz-/Punktdaten, Form, Heim/Auswärts, Position, Aktualität und Gegnerstärke.
- Kleine Stichproben werden über Shrinkage/hierarchische Priors Richtung RC-Baseline gezogen.
- Outputs werden kalibriert und mit Brier Score, Log Loss und Calibration Curves geprüft.

### Gegnerische Viererkombination

- Nicht unabhängig über vier höchste Einzelwahrscheinlichkeiten auswählen.
- Kandidaten: alle historisch eingesetzten Spieler des Teams plus aktuelle Saison-Nennungen.
- Modelliert werden Wahrscheinlichkeiten über gültige Vierer-Sets, z. B. mit zeitgewichteten historischen Viererkombinationen, Team-/Spieler-Verfügbarkeitsfeatures und regularisierten Set-Scores.
- Ergebnis ist eine normalisierte Verteilung `P(Set von 4 Spielern | Team, as_of, Kontext)`.

### Gegnerische Positionen

- Wenn vier Gegner bekannt sind, wird eine Verteilung über alle 24 Permutationen berechnet.
- Features: historische Positionen, Set-spezifische Muster, Saisonaktualität, Heim/Auswärts, RC-Reihenfolge, Teamgewohnheiten.
- Die Verteilung ist automatisch konsistent, weil nur vollständige Permutationen normalisiert werden.

### Eigene Optimierung

- Für jede eigene Permutation werden alle relevanten Gegner-Szenarien gewichtet simuliert.
- Primäres Ziel: Mannschaftssieg-Wahrscheinlichkeit.
- Nebenmetriken: erwartete Einzelpunkte, Varianz/Quantile der Einzelpunkte, Remis-/Niederlagenwahrscheinlichkeit falls Spielsystem relevant, wichtigste Matchup-Beiträge.
- Erklärungstexte verweisen auf die größten positiven/negativen Beiträge aus der Simulation.

### Backtesting

- Jeder Trainings-/Vorhersagelauf bekommt `as_of`.
- Feature-Queries filtern strikt `match_date < as_of` bzw. `imported_at <= as_of`.
- Splits sind zeitlich, nicht zufällig.
- Modellvergleich gegen RC-only ist obligatorisch; komplexere Modelle werden nur übernommen, wenn sie out-of-time messbar besser und kalibriert sind.

## 7. API-Design

Vorgeschlagene Endpunkte:

- `GET /health`: Service-Status.
- `GET /metadata/import-status`: letzter erfolgreicher Import und Datenstand.
- `GET /teams?query=&season=`: Teamsuche.
- `GET /teams/{team_id}/players?season=`: verfügbare Spieler eines Teams.
- `POST /analysis/phase-a`: Input gegnerisches Team, eigene vier Spieler, optional Heim/Auswärts und Spieltermin; Output empfohlene Aufstellung, Alternativen, Gegner-Set-Verteilung und Erklärungen.
- `POST /analysis/phase-b`: Input gegnerisches Team, eigene vier Spieler, tatsächliche vier Gegner; Output neu berechnete Empfehlung mit gegnerischer Positionsverteilung.
- `GET /analysis/{run_id}`: gespeicherten Lauf abrufen.
- `POST /backtests`: administrativ, startet Backtest.
- `GET /backtests/{id}`: Metriken und Artefakte.

API-Regeln:

- Frontend sendet nur interne IDs, nie XTTV-URLs.
- Responses enthalten `model_version`, `data_as_of`, `assumptions` und Warnungen bei unvollständigen Daten.
- Validierung erzwingt genau vier verschiedene eigene Spieler und, in Phase B, genau vier verschiedene Gegner.

## 8. Mobile-first Frontend-Struktur

Seiten/Flows:

1. Start: Gegnerteam suchen/auswählen.
2. Eigene Spieler: vier Spieler per Suche/Recent/Favoriten auswählen.
3. Analyse: prominent empfohlene Aufstellung, Siegchance, erwartete Einzelpunkte.
4. Alternativen: Top-Aufstellungen als Karten.
5. Phase-B-Update: Button `Gegner bekannt`, vier Gegner auswählen, Analyse aktualisieren.
6. Warum: aufklappbare Erklärungen pro empfohlener Position und wichtigste Gegner-Szenarien.
7. Datenstand: letzter Import, Modellversion, Warnungen.

Mobile UX:

- Einspaltig, große Touch-Ziele, sticky Primäraktion.
- Keine Desktop-only Tabellen; Tabellen werden zu Karten/Accordions.
- Optimierungsergebnis muss ohne Scrollen zumindest Empfehlung, Siegchance und erwartete Punkte zeigen.

## 9. Offene technische Risiken

- XTTV-Struktur/URLs scheinen volatil; aktuell indizierte Links liefern teils 404/Redirects.
- Mögliche Session-, Cookie-, Sprach- oder JavaScript-Abhängigkeiten im Ergebnisdienst.
- Nutzungsbedingungen, robots.txt und Lastbegrenzung müssen vor produktivem Scraping geklärt werden.
- RC-Datenquelle ist noch nicht als stabile maschinenlesbare Schnittstelle verifiziert.
- Punktverhältnisse einzelner Sätze sind möglicherweise nicht durchgehend verfügbar.
- Spielsysteme können je Verband/Liga variieren; Optimierer darf Matchschema nicht hart codieren.
- Kleine Datenmengen pro Team/Spieler können komplexe Modelle instabil machen.
- Personenbezogene Daten erfordern DSGVO-konforme Datensparsamkeit, Zweckbindung und Lösch-/Korrekturprozesse.

## 10. Entwicklungsplan in kleinen Schritten

### Phase 1: Repository und Projektstruktur

- Dieses Dokument reviewen und freigeben.
- Monorepo-Struktur festlegen: `backend/`, `frontend/`, `docs/`, `infra/`.
- Docker-Compose-Entwicklungsumgebung planen.

### Phase 2: Datenmodell

- SQLAlchemy-Modelle und Alembic-Migrationen für Kernschema erstellen.
- Constraints und idempotente Upsert-Helfer testen.

### Phase 3: XTTV-Struktur-Spike und Testliga

- Aktuelle Einstiegspunkte im Browser prüfen.
- Eine konkrete Testliga auswählen.
- Rohseiten-Fetcher mit Rate Limit und Fixture-Speicherung bauen.
- Parser erst nach dokumentierter Seitenstruktur implementieren.

### Phase 4: Importvalidierung und Tests

- Fixture-Tests für Liga, Spielbericht, Aufstellung, Einzel, Doppel.
- Duplicate-Detection-Tests.
- Import-Run-/Import-Item-Logging testen.

### Phase 5: RC-Integration

- Stabile RC-Quelle verifizieren.
- Snapshot-Import und Spieler-Matching implementieren.

### Phase 6: RC-only Baseline

- Kalibrierte Gewinnwahrscheinlichkeit und Tests für Monotonie/Symmetrie.

### Phase 7: Historische Features

- Head-to-Head, Form, Position, Heim/Auswärts als zeitpunktbezogene Features.

### Phase 8: Gegner-Verfügbarkeitsmodell

- Vierer-Set-Verteilungen mit Zeitgewichtung und Shrinkage.

### Phase 9: Gegner-Aufstellungsmodell

- 24-Permutationen-Verteilung je Gegner-Vierergruppe.

### Phase 10: Eigene Optimierung

- Alle 24 eigenen Aufstellungen simulieren und ranken.

### Phase 11: Backtesting und Modellvergleich

- Zeitkorrekte Backtests, RC-only vs. komplexere Modelle, Calibration Reports.

### Phase 12: Backend API

- Phase-A/Phase-B-Endpunkte und gespeicherte Prediction Runs.

### Phase 13: Mobile-first Frontend

- Kernflow mit Team-/Spielersuche und Ergebnisdarstellung.

### Phase 14: Warum-Funktion

- Erklärungsgenerator aus Modell- und Simulationsbeiträgen.

### Phase 15: Deployment und Scheduler

- Render-kompatible Docker-Konfiguration.
- Montag-08:00-Import mit Audit-Log und Alerting.
