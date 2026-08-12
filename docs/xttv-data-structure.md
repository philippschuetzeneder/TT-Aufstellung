# XTTV-Datenstruktur-Spike für den wöchentlichen Import

Stand: 2026-08-12

## Ziel und Abgrenzung

Dieses Dokument klärt vor einer `xttv_probe`-Implementierung, welche Aussagen zur XTTV-Datenstruktur bereits belastbar sind und welche Aussagen in der aktuellen Codex-Cloud wegen `403 Forbidden`/Proxy-Fehlern noch nicht direkt verifiziert werden konnten. Es wurde bewusst kein vollständiger Scraper, keine Datenbankmigration und keine ML-Komponente implementiert.

Das langfristige Ziel bleibt ein automatischer wöchentlicher Import, z. B. montags um 08:00 Uhr. Die produktive Web-App und ihre normalen API-Requests dürfen danach ausschließlich die eigene Datenbank verwenden und XTTV niemals live abfragen.

## 1. Erkenntniskategorien

### 1.1 Sicher bekannt aus öffentlich sichtbarer XTTV-Struktur und Dokumentation

Diese Punkte sind nicht von der Codex-Cloud-Erreichbarkeit abhängig:

- XTTV betreibt einen öffentlichen Ergebnisdienst für österreichische Tischtennis-Meisterschaften mit Ergebnisdienst-, Tabellen-, Ranglisten-, Vereins-, Spieler- und Spielberichtsfunktionen.
- Der öffentliche Ergebnisdienst verwendet die Pfade `https://oettv.xttv.at/ed/` bzw. `https://oettv.xttv.at/ed/index.php`.
- Öffentliche XTTV-/ÖTTV-URLs verwenden identifizierbare Query-Parameter:
  - `oid` für Organisation/Verband.
  - `lid` für Liga/Spielklasse.
  - `sjid` für Spieljahr/Saison in öffentlichen Ranglistenabfragen.
  - `vid` für Verein in öffentlichen Ranglistenabfragen.
  - `lang` für Sprache.
- Öffentlich dokumentierte Ergebnisdienst-Funktionen umfassen Spielklassen-/Ligaauswahl, Tabellen, Ranglisten, Spiele/Termine, Detailergebnisse/Spielberichte und Spielersuche/Spielereinsätze.
- Spielberichte enthalten fachlich die Daten, die für das Projekt wesentlich sind: Mannschaften, Aufstellungen, Einzel, Doppel und Ergebnisse. Bei zumindest einem dokumentierten Spielsystem werden vier Einzelpositionen je Mannschaft und Doppel abgebildet.
- Öffentliche Vereinslisten sind über `https://oettv.xttv.at/public/ausgabe_vereine.php?oid=<OID>` erreichbar und benötigen `oid` als Verbandsparameter.
- Öffentliche RC-Ranglisten werden über `https://oettv.xttv.at/public/ranglistenabfrage.php` bereitgestellt und enthalten strukturierte Ratingdaten wie Rang, Punkte, Standardabweichung/Unsicherheit, Name, RC-ID, Verein/Nationalität und zuletzt gespielt.
- Passnummern bzw. RC-IDs sind in XTTV-/ÖTTV-Kontexten als stabile Spielerkennungen sichtbar und müssen im späteren Import als primäre externe Spieler-IDs bevorzugt werden, statt Spieler nur über Namen zu matchen.

### 1.2 Tatsächlich in dieser Codex-Cloud getestet

Diese Punkte wurden in der aktuellen Umgebung konkret ausgeführt:

- Lokale direkte HTTP(S)-Abrufe mit Python `urllib` auf XTTV-/ÖTTV-Seiten wurden getestet.
- Getestete URL-Klassen waren u. a.:
  - `https://www.xttv.at/`
  - `http://www.xttv.at/`
  - `https://oettv.xttv.at/ed/index.php?...`
  - `http://oettv.xttv.at/ed/index.php`
  - `https://oettv.xttv.at/public/ausgabe_vereine.php?oid=...`
  - `https://oettv.xttv.at/public/ranglistenabfrage.php?...`
- Diese direkten Abrufe scheiterten in der Codex-Cloud mit `HTTP Error 403: Forbidden` oder `Tunnel connection failed: 403 Forbidden`.
- Das verfügbare Web-Open-Tool zeigte für mehrere `oettv.xttv.at/ed/...`-URLs aktuell Redirects auf `https://oettv-test.xttv.at/error/404-file-not-found.html`.
- Websuche/Suchindex-Snippets lieferten dennoch Hinweise auf bestehende XTTV-Parameter und sichtbare Felder. Snippets ersetzen aber keinen vollständigen Abruf eines aktuellen Spielberichts.

Wichtig: Diese Ergebnisse beweisen nur, dass die aktuelle Codex-Cloud XTTV nicht zuverlässig direkt abrufen kann. Sie beweisen nicht, dass XTTV allgemein nicht öffentlich erreichbar wäre.

### 1.3 Wegen Cloud-403 noch nicht verifiziert

Folgende Punkte sind fachlich plausibel bzw. aus öffentlicher Struktur ableitbar, müssen aber mit echten HTML-/CSV-/XHR-Antworten in einer zugriffsberechtigten Umgebung geprüft werden:

- Ob eine aktuelle Liga-URL `ed/index.php?lid=<LIGA_ID>` ohne zusätzliche Session/Cookies die komplette Liga erkennt und alle Teams/Tabellen/Spielpläne im HTML enthält.
- Welche konkrete `do=...`-Ansicht für alle Spiele einer Liga erforderlich ist und ob Filter wie `minRound`, `maxRound`, `minDg`, `maxDg` nötig sind.
- Ob abgeschlossene Spiele direkt als eigene Spielbericht-URLs verlinkt sind oder ob Detailbereiche per JavaScript eingeblendet bzw. per XHR nachgeladen werden.
- Ob die eindeutige Spiel-ID `meid` in Links, Formularfeldern oder XHR-Parametern sichtbar ist und stabil für idempotente Imports genutzt werden kann.
- Ob Spielberichte Passnummern/RC-IDs direkt bei den Spielern enthalten oder ob diese über Spielerlinks/Ranglisten separat gematcht werden müssen.
- Ob Satzpunkte vollständig öffentlich sichtbar sind oder nur Satzsummen/Spielausgänge.
- Ob Doppelbesetzungen in allen relevanten Ligen einheitlich strukturiert und öffentlich sichtbar sind.
- Ob `ranglistenabfrage.php` einen CSV-Export über Parameter wie `saveCSV` oder `CSV_Output` ohne Login anbietet oder ob nur HTML-Parsing möglich ist.

## 2. Benötigte Seiten/Endpunkte und konkrete Probe-Parameter

Der spätere Probe soll keine Datenbank schreiben und keine Produktlogik enthalten. Er soll ausschließlich Rohdokumente und einen strukturierten Befundbericht erzeugen.

### 2.1 Ergebnisdienst-Einstieg / Ligaansicht

- URL-Muster: `https://oettv.xttv.at/ed/index.php?oid=<OID>&lang=de`
- URL-Muster für konkrete Liga: `https://oettv.xttv.at/ed/index.php?lid=<LID>&lang=de`
- Bekannte/zu prüfende Parameter:
  - `oid`: Verband/Organisation.
  - `lid`: Liga/Spielklasse.
  - `lang=de`: stabile deutschsprachige Labels für spätere Parser-Fixtures.
  - optional `sjid`: falls Saison nicht implizit über Liga-ID festgelegt wird.
- Probe-Aufgaben:
  - HTTP-Status, finale URL, Redirect-Kette und Content-Type protokollieren.
  - HTML speichern.
  - Prüfen, ob Liga-Name, Saison/Spieljahr, Teams, Tabelle und Navigationslinks sichtbar sind.
  - Alle Links/Formulare mit Parametern `lid`, `oid`, `do`, `meid`, `vid`, `spid`, `runde`, `dg`, `sjid` extrahieren.

### 2.2 Spiele einer Liga

- URL-Muster: `https://oettv.xttv.at/ed/index.php?lid=<LID>&do=spiele&lang=de`
- Zusätzlich zu testen, falls auf der Seite gefunden:
  - `minRound=<N>` und `maxRound=<N>` für Rundenfilter.
  - `minDg=<N>` und `maxDg=<N>` für Durchgänge.
  - `highVid=<VID>` für Team-/Vereinsfilter.
  - `seite=<N>` für Paginierung.
  - `order=<FIELD>` für Sortierung.
- Probe-Aufgaben:
  - Prüfen, ob die Liga als vollständige Einheit erkennbar ist.
  - Alle Mannschaftsspiele extrahierbar machen: Runde, Durchgang, Datum/Uhrzeit, Heimteam, Auswärtsteam, Status, Ergebnis.
  - Abgeschlossene Spiele erkennen.
  - Für abgeschlossene Spiele Detail-Links, JavaScript-Hooks oder XHR-Kandidaten sammeln.
  - Prüfen, ob `meid` pro Spiel sichtbar ist.

### 2.3 Spielbericht / Matchdetails

- URL-Muster noch nicht sicher bestätigt; Kandidaten müssen aus der Spieleansicht extrahiert werden.
- Erwartete Parameter-Kandidaten:
  - `meid=<MATCH_EVENT_ID>` als wichtigste zu suchende Spiel-ID.
  - `lid=<LID>` als Kontext.
  - ggf. `do=...` für Detail-/Spielberichtansicht.
- Probe-Aufgaben:
  - Für mindestens ein abgeschlossenes Spiel den Roh-Spielbericht speichern.
  - `meid` aus Link, Formular, DOM, Script oder XHR-URL erkennen und protokollieren.
  - Prüfen, ob vier Spieler je Mannschaft sichtbar sind.
  - Prüfen, ob Positionsangaben je Mannschaft sichtbar sind: Position `1-4`, `A-D` oder anderes Schema.
  - Prüfen, ob Spieler-Passnummern/RC-IDs direkt sichtbar sind oder über Spielerlinks erreichbar sind.
  - Alle Einzel identifizieren: Reihenfolge/Spielnummer, Heimspieler, Auswärtsspieler, Ergebnis, Satzresultat, ggf. Punkte je Satz, Sieger, w.o.-Status.
  - Alle Doppel identifizieren: Spielerpaare, Ergebnis, Satzresultat, ggf. Punkte je Satz, Sieger, w.o.-Status.
  - Liga, Saison, Runde und Datum aus Bericht oder Liga-Kontext prüfen.

### 2.4 Spielerlinks / Spielerprofil / Spielereinsätze

- URL-Muster noch nicht sicher bestätigt; Kandidaten aus Liga-, Ranglisten- oder Spielberichtseiten extrahieren.
- Erwartete Parameter-Kandidaten:
  - `spid=<PLAYER_ID>` oder ähnliche Spieler-ID.
  - Passnummer/RC-ID als sichtbarer Text oder Query-Parameter.
- Probe-Aufgaben:
  - Prüfen, ob Spielerlinks aus Spielberichten stabile externe IDs enthalten.
  - Prüfen, ob Passnummer, RC-ID, Name und Verein eindeutig extrahierbar sind.
  - Prüfen, ob Spielereinsätze öffentlich abrufbar sind und ob sie für Spielerhistorie/Validierung genutzt werden können.

### 2.5 RC-Ranglistenabfrage

- URL-Muster: `https://oettv.xttv.at/public/ranglistenabfrage.php?oid=<OID>&sjid=<SJID>&vid=0&showList=1&SortOrder=Rating&OnlyShowPrimaryClub=Yes&lang=de`
- Zusätzlich zu testen:
  - `saveCSV=1`
  - `CSV_Output=1`
  - bestehende Formularfelder aus der initialen HTML-Seite.
- Probe-Aufgaben:
  - HTML- und, falls verfügbar, CSV-Antwort speichern.
  - Stichtag/Snapshot-Datum erkennen.
  - Pro Spieler extrahierbare Felder prüfen: Rang, Punkte, Standardabweichung, Nachname, Vorname, Passnummer/RC-ID, Verein, Nationalität, zuletzt gespielt.
  - Prüfen, ob Vereine/Spieler über IDs verlinkt sind.

### 2.6 Vereinsliste

- URL-Muster: `https://oettv.xttv.at/public/ausgabe_vereine.php?oid=<OID>&lang=de`
- Probe-Aufgaben:
  - Vereinsname, Kürzel, Vereins-ID/Linkparameter, Region/Bezirk und Spielorte prüfen.
  - Datenschutzsensibles Material wie Kontaktpersonen, E-Mail, Telefon und Privatadressen im Probe-Bericht markieren, aber für MVP-Import nicht als Muss-Felder vorsehen.

## 3. Importer-Fähigkeiten: Statusmatrix

| Fähigkeit des späteren Importers | Sicher bekannt? | In Codex-Cloud getestet? | Wegen Cloud-403 offen? | Probe-Kriterium |
| --- | --- | --- | --- | --- |
| Komplette Liga erkennen | Teilweise: Ligaansichten über `lid` sind öffentlich strukturiert. | Nein, Direktabruf blockiert/redirected. | Vollständigkeit von Teams/Tabellen/Spielplan. | Liga-HTML enthält Liga-Name, Saison und alle Teams/Spiele oder Links dorthin. |
| Alle abgeschlossenen Spiele einer Liga erfassen | Teilweise: Spiele-Ansicht ist dokumentiert. | Nein. | Exakte Ansicht/Filter/Paginierung. | Spieleansicht liefert alle abgeschlossenen Spiele mit Ergebnis und Detailreferenz. |
| `meid` eines Spiels erkennen | Nein, nur als erwarteter XTTV-Parameter zu prüfen. | Nein. | Ob `meid` sichtbar/stabil ist. | Mindestens ein abgeschlossener Spiel-Link enthält `meid` oder eine äquivalente stabile Match-ID. |
| Vier Spieler je Mannschaft und Positionen extrahieren | Fachlich sicher in Spielberichten benötigt; konkrete HTML-Struktur offen. | Nein. | DOM-Struktur und Positionslabel. | Spielbericht enthält genau vier Spieler je Team mit eindeutiger Position. |
| Alle Einzel inklusive Ergebnis extrahieren | Fachlich sicher; konkrete HTML-Struktur offen. | Nein. | Satz-/Punktformat, w.o.-Darstellung. | Spielbericht listet alle Einzel mit Paarung, Ergebnis, Sieger und Satzdaten. |
| Doppel extrahieren | Fachlich sicher in betroffenen Spielsystemen; konkrete Struktur offen. | Nein. | Paarungsformat und Ligen ohne Doppel. | Spielbericht listet Doppel separat und erlaubt Paar-/Ergebnisextraktion. |
| Spieler anhand stabiler Passnummern identifizieren | Teilweise: RC-ID/Passnummern sind in Ranglisten sichtbar. | Nicht per Vollabruf. | Ob Spielberichte dieselben IDs enthalten. | Spieler-ID ist in Spielbericht, Spielerlink oder RC-Mapping eindeutig bestimmbar. |
| Liga, Saison, Runde und Datum speichern | Teilweise: Liga-/Spieleansichten enthalten diese Daten fachlich. | Nein. | Exakte Position im HTML/Detailbericht. | Diese Felder sind aus Liga- oder Spielberichtkontext eindeutig lesbar. |
| RC-Ratings separat importieren | Ja, öffentliche Ranglistenabfrage ist bekannt. | Nein, Direktabruf blockiert. | CSV-Verfügbarkeit und Feldnamen. | Ranglisten-HTML oder CSV enthält Rating, St.Abw., Stichtag und Spieler-ID. |

## 4. Robuster Entwicklungsplan trotz Cloud-403

### 4.1 Kein Fake-Scraper

Solange aus der Codex-Cloud keine echten XTTV-Antworten abrufbar sind, wird kein Scraper gegen erfundene HTML-Strukturen implementiert. Es werden keine synthetischen XTTV-Responses als Parserbasis verwendet. Parser entstehen erst aus gespeicherten echten Fixtures.

### 4.2 Probe vorbereiten, aber nicht als vollständigen Scraper bauen

Der nächste Code-Schritt soll nur ein isolierter `xttv_probe` sein. Dieser Probe wird so entworfen, dass er lokal oder in einer späteren Importumgebung mit XTTV-Zugriff ausgeführt werden kann.

Minimale Eingabekonfiguration:

```yaml
base_url: "https://oettv.xttv.at"
association_oid: "<OID>"
league_lid: "<LID>"
season_sjid: "<SJID>"
language: "de"
fixture_dir: "fixtures/xttv/<OID>/<LID>/<timestamp>"
rate_limit_seconds: 2
user_agent: "TT-Aufstellung research probe; contact=<PROJECT_CONTACT>"
```

Zu speichernde Probe-Artefakte:

- `request-log.jsonl`: URL, Methode, Zeitpunkt, Status, finale URL, Redirects, Content-Type, Content-Length, Hash, Fehler.
- `league.html`: Liga-/Tabellenansicht.
- `matches.html`: Spiele-/Terminansicht.
- `match-detail-<meid-or-index>.html`: mindestens ein abgeschlossener Spielbericht.
- `rating.html` und optional `rating.csv`: RC-Ranglistenantworten.
- `clubs.html`: Vereinsliste.
- `links.json`: extrahierte Links/Formulare/Scripts mit Query-Parametern.
- `field-availability.json`: Befund, welche Importfelder in welchem Dokument sichtbar sind.

Nicht-Aufgaben des Probe:

- Kein Datenbankimport.
- Keine vollständige HTML-Normalisierung.
- Keine ML-/Optimierungslogik.
- Kein Frontend.
- Kein Umgehen technischer Schutzmaßnahmen.

### 4.3 Fixture-first Parserentwicklung

Nach erfolgreichem lokalen Probe-Lauf werden echte Rohdokumente ins Review gegeben. Erst danach entstehen Parser mit Tests gegen diese Fixtures. Jeder Parser muss deklarieren:

- welche Seitentyp-Version er unterstützt,
- welche Felder Pflichtfelder sind,
- welche Felder optional sind,
- welche Validierungsregeln gelten,
- welche Fehler retry-fähig sind und welche manuelle Prüfung benötigen.

### 4.4 Wöchentlicher Import nach erfolgreichem Probe

Der spätere Importjob läuft montags um 08:00 Uhr und nutzt dieselben URL-Klassen wie der Probe:

1. Liga-/Spieleansicht laden.
2. Neue oder geänderte abgeschlossene Spiele über `meid` oder stabile Ersatz-ID erkennen.
3. Spielberichte laden und Rohdokumente speichern.
4. Daten validieren und idempotent upserten.
5. RC-Rangliste als separaten Snapshot importieren.
6. Import-Log mit Start/Ende, Quelle, Datensatzanzahlen, Hashes, Fehlern und Status speichern.
7. Bei XTTV-Ausfall letzte erfolgreiche Datenbankversion weiterverwenden; App bleibt funktionsfähig.

## 5. Entscheidungsempfehlung

**Empfehlung: B) XTTV ist aus Codex Cloud blockiert → Probe nur vorbereiten und lokal bzw. in einer späteren Importumgebung testen.**

Begründung:

- Die öffentlichen XTTV-Seiten sind grundsätzlich ohne Login erreichbar und enthalten fachlich die benötigten Spielberichte und Aufstellungen.
- Die aktuelle Codex-Cloud kann direkte HTTP(S)-Abrufe aber nicht zuverlässig durchführen (`403 Forbidden`/Proxy-Tunnel-Fehler und `oettv-test`-404-Redirects).
- Ohne echte HTML-/CSV-/XHR-Antworten wäre ein Scraper spekulativ und fragil.
- Der robuste Weg ist daher: `xttv_probe` vorbereiten, mit konkreten URL-Mustern und Artefakten spezifizieren, anschließend in einer Umgebung mit XTTV-Zugriff echte Fixtures erzeugen und erst danach Parser/Datenbankimport implementieren.

Option A ist derzeit nicht erfüllt, weil Codex-Cloud-Direktabrufe blockiert sind. Option C ist derzeit nicht nachgewiesen, weil keine bessere offizielle öffentliche API oder stabile maschinenlesbare XTTV-Datenquelle belegt wurde; die RC-Ranglistenabfrage kann später eine Teilquelle sein, ersetzt aber Spielberichte nicht.
