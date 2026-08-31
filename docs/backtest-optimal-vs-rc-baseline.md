# Backtest: Optimale Aufstellung vs. triviale RC-Stärke-Aufstellung

Stand: August 2026 · Saison **2025/2026** · reproduzierbar über die Skripte in `scripts/`.

## Kurzfassung

**Die optimale Aufstellung des Produktionsmodells ist nachweislich besser als die triviale RC-Stärke-Aufstellung** (stärkster Spieler auf A/1, schwächster auf D/4):

- In **94,3 %** der Fälle liefert die optimale Aufstellung eine **höhere modellierte Mannschafts-Siegchance** als die RC-Baseline (+**1,97 PP** im Mittel, Median +1,32 PP).
- Die optimale Aufstellung ist in **keinem** der 3936 Fälle schlechter als die RC-Baseline (0 % „optimal niedriger“).
- Gegenüber reiner RC-Spielstärke in den **Einzelduellen** (ohne Trend, H2H, Heim/Gast) erhöht das Vollmodell die Siegchance bei **gleicher Aufstellung** um durchschnittlich **+5,2 PP**.

Damit sind zwei getrennte Fragen beantwortet:

1. **Aufstellungssuche:** Lohnt sich die Optimierung über 24 Permutationen gegenüber der Stärke-Aufstellung? → **Ja, klar messbar.**
2. **Spielstärke-Modell:** Lohnt sich RC + Zusatzsignale gegenüber RC allein? → **Ja, besonders auf Mannschaftsebene.**

---

## Versuchsdesign

| Parameter | Wert |
|---|---|
| Saison | 2025/2026, alle Ligen |
| Perspektive | Heimmannschaft |
| Phase | **C** — bekannter Gegner-4er, fixe Reihenfolge und Richtung |
| Historie | 3 Jahre, leakage-safe (`ref_end = Spieltag − 1`) |
| Fälle | **3936** Heim-Spiele mit 4:4 Aufstellung und Team-Ergebnis |
| RC-Baseline | `_trivial_strength_own_order`: stärkster RC auf A, schwächster auf D |

**Leakage:** Nur Daten, die am Vortag des Spiels verfügbar gewesen wären (RC-Snapshots, Form, H2H).

---

## Ergebnis 1 — Modellierte Siegchance: Optimal vs. RC-Baseline

**Frage:** Ist die Siegeswahrscheinlichkeit mit der **optimalen Aufstellung** höher als mit der **RC-Stärke-Aufstellung**?

Skript: `scripts/analyze_win_prob_vs_baseline.py`  
Rohdaten: `scripts/output/win_prob_vs_baseline_2526.json`

| Kennzahl | Optimal | RC-Stärke-Baseline | Δ |
|:---|:---:|:---:|:---:|
| Mittlere Siegchance | **45,11 %** | 43,14 % | **+1,97 PP** |
| Median Δ | — | — | **+1,32 PP** |
| Optimal > Baseline | **94,3 %** (3713/3936) | — | — |
| Gleich | 5,7 % | — | — |
| Optimal < Baseline | **0,0 %** | — | — |
| Spannweite Δ | 0 … **+21,7 PP** | — | — |

**Interpretation:** Die Aufstellungssuche des Modells verbessert die modellierte Siegchance fast immer und nie verschlechtert sie.

---

## Ergebnis 2 — Outcome-Treffer: Optimal vs. RC-Baseline

**Frage:** Trifft die Vorhersage (Sieg / 7:7 / Niederlage) mit optimaler Aufstellung öfter als mit RC-Baseline?

Skript: `scripts/backtest_team_result_2526.py`  
Rohdaten: `scripts/output/team_result_backtest_2526.json`

| Variante | Outcome-Treffer | Brier ↓ | Log-Loss ↓ |
|:---|:---:|:---:|:---:|
| **Vollmodell optimal** | **53,5 %** | 0,5782 | 1,0456 |
| Vollmodell RC-Baseline | 50,7 % | 0,5783 | 1,0496 |
| Δ optimal − Baseline | **+2,7 PP** | −0,0001 | −0,0040 |

Zum Vergleich **RC-only** (Einzel und Aufstellungssuche nur aus RC): optimal ≈ Baseline (~43 % Treffer, kein messbarer Vorteil). Der Mehrwert der Optimierung kommt aus dem **Vollmodell**, nicht aus RC allein.

---

## Ergebnis 3 — Spielstärke-Modell: Vollmodell vs. RC-only (Einzel)

**Frage:** Wie viel erhöht unsere Spielstärke (RC + Trend + Heim/Gast + H2H) die Siegchance gegenüber **nur RC** — bei **derselben optimalen Aufstellung**?

Skript: `scripts/analyze_full_vs_rc_singles_strength.py`  
Rohdaten: `scripts/output/full_vs_rc_singles_strength_2526.json`

| Ebene | Vollmodell-Einzel | RC-only Einzel | Mehrwert |
|:---|:---:|:---:|:---:|
| **Mannschafts-Siegchance** | **45,11 %** | 39,90 % | **+5,21 PP** (Median +4,36 PP) |
| Mittl. Einzel-Spielgewinn | **51,99 %** | 50,05 % | **+1,94 PP** |

- Vollmodell höher in **71,8 %** der Spiele (Mannschaftsebene)
- Doppel unverändert Produktionsmodell; nur Einzel-Wahrscheinlichkeiten variiert

**Interpretation:** Trend, direkte Duelle und Heim/Gast liefern zusätzlich zur RC-Stärke einen großen Anteil der modellierten Siegchance. Das erklärt, warum reine RC-Aufstellungssuche (Ergebnis 2, RC-only-Zweig) kaum über die Stärke-Baseline hinauskommt.

---

## Reproduktion

```powershell
cd TT-Aufstellung
$env:PYTHONPATH = 'backend'

# Gesamt-Backtest (Outcome + Brier + Log-Loss)
.\.venv\Scripts\python.exe scripts\backtest_team_result_2526.py

# Siegchance optimal vs. RC-Baseline
.\.venv\Scripts\python.exe scripts\analyze_win_prob_vs_baseline.py

# Vollmodell-Spielstärke vs. RC-only (Einzel), gleiche Aufstellung
.\.venv\Scripts\python.exe scripts\analyze_full_vs_rc_singles_strength.py
```

Voraussetzung: lokale PostgreSQL mit XTTV-Import (Saison 2025/2026).

---

## Grenzen

- Nur **Heim**-Perspektive; Gast-Seite nicht backgetestet.
- Phase C setzt **bekannten Gegner** voraus (kein Gegner-Lineup-Raten).
- Doppel-Paare nicht separat optimiert (Default-Logik).
- Kein direkter Vergleich zur **tatsächlich gespielten** Aufstellung in XTTV.
- Metriken beziehen sich auf **modellierte** Wahrscheinlichkeiten, nicht auf garantierte Real-Ergebnisse.

Trotz dieser Grenzen zeigen die drei unabhängigen Auswertungen konsistent: **Optimale Aufstellung schlägt triviale RC-Stärke-Aufstellung** — sowohl in der Siegchance pro Spiel als auch in der Outcome-Trefferquote.
