"""Generate spieltyp-eingabe.html for a XTTV league Einzelrangliste."""
from __future__ import annotations

import html
from pathlib import Path

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_league_einzel import BASE, fetch_html, parse_players

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "spieltyp-eingabe.html"

TYPE_OPTIONS = [
    ("offensive", "O", "Offensiv"),
    ("pips", "N", "Noppen"),
    ("defensive", "D", "Defensiv"),
    ("", "-", "Kein Eintrag"),
]


def row_html(player: dict) -> str:
    radios = ""
    for value, short, label in TYPE_OPTIONS:
        checked = " checked" if not value else ""
        radios += (
            f'<label class="type-opt"><input type="radio" name="p{player["pass_id"]}" '
            f'value="{html.escape(value)}" data-short="{html.escape(short)}"{checked}>'
            f'<span title="{html.escape(label)}">{html.escape(short)}</span></label>'
        )
    return (
        f"<tr data-pass=\"{html.escape(player['pass_id'])}\">"
        f"<td>{player['rank']}</td>"
        f"<td>{html.escape(player['name'])}</td>"
        f"<td><code>{html.escape(player['pass_id'])}</code></td>"
        f"<td>{html.escape(player['team_code'])}</td>"
        f"<td class=\"types\">{radios}</td>"
        "</tr>"
    )


def build_html(players: list[dict], lid: int) -> str:
    rows = "\n".join(row_html(p) for p in players)
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spieltyp-Eingabe Liga {lid}</title>
  <style>
    :root {{ font-family: system-ui, sans-serif; color: #1a1a1a; }}
    body {{ margin: 1rem 1.5rem 2rem; max-width: 1100px; }}
    h1 {{ font-size: 1.35rem; }}
    .muted {{ color: #666; font-size: 0.92rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.35rem 0.5rem; vertical-align: middle; }}
    th {{ background: #f4f4f4; text-align: left; }}
    .types {{ white-space: nowrap; }}
    .type-opt {{ margin-right: 0.35rem; cursor: pointer; }}
    .type-opt span {{ display: inline-block; min-width: 1.2rem; text-align: center; padding: 0.1rem 0.25rem; border: 1px solid #ccc; border-radius: 4px; }}
    .type-opt input {{ margin: 0; }}
    .type-opt input:checked + span {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
    textarea {{ width: 100%; min-height: 8rem; font-family: ui-monospace, monospace; font-size: 0.85rem; }}
    .actions {{ margin: 1rem 0; display: flex; gap: 0.75rem; flex-wrap: wrap; }}
    button {{ padding: 0.5rem 0.9rem; cursor: pointer; border: 1px solid #ccc; border-radius: 6px; background: #fff; }}
    button.primary {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
    #status {{ margin-top: 0.5rem; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>Spieltyp pro Spieler (Testliga lid={lid})</h1>
  <p class="muted">Markiere pro Spieler: <strong>O</strong> Offensiv, <strong>N</strong> Noppen, <strong>D</strong> Defensiv, <strong>−</strong> kein Eintrag.
  Danach „Text kopieren“ und in Cursor einfügen — oder „In DB speichern“.</p>
  <div class="actions">
    <button type="button" class="primary" id="copyBtn">Text für Cursor kopieren</button>
    <button type="button" id="saveBtn">In DB speichern</button>
  </div>
  <textarea id="export" readonly placeholder="Hier erscheint der Export …"></textarea>
  <p id="status" class="muted"></p>
  <table>
    <thead>
      <tr><th>Rang</th><th>Name</th><th>Pass</th><th>Team</th><th>Spieltyp</th></tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  <script>
    const TYPE_MAP = {{ offensive: 'O', pips: 'N', defensive: 'D', '': '-' }};
    function buildLines() {{
      const lines = [];
      document.querySelectorAll('tbody tr').forEach((row) => {{
        const passId = row.dataset.pass;
        const checked = row.querySelector('input[type=radio]:checked');
        const value = checked ? checked.value : '';
        const short = checked ? checked.dataset.short : '-';
        if (value) lines.push(`${{passId}} ${{short}}`);
      }});
      return lines.join('\\n');
    }}
    function updateExport() {{
      document.getElementById('export').value = buildLines();
    }}
    document.querySelectorAll('input[type=radio]').forEach((el) => el.addEventListener('change', updateExport));
    document.getElementById('copyBtn').addEventListener('click', async () => {{
      updateExport();
      const text = document.getElementById('export').value;
      try {{ await navigator.clipboard.writeText(text); }}
      catch {{ document.getElementById('export').select(); }}
      document.getElementById('status').textContent = 'Kopiert — füge den Text in Cursor ein.';
    }});
    document.getElementById('saveBtn').addEventListener('click', async () => {{
      updateExport();
      const text = document.getElementById('export').value;
      const status = document.getElementById('status');
      status.textContent = 'Speichere …';
      try {{
        const res = await fetch('/api/spieltyp/bulk', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ text }}),
        }});
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'Speichern fehlgeschlagen');
        status.textContent = `Gespeichert: ${{data.applied}} gesetzt, ${{data.cleared}} geleert, ${{data.missing_pass_ids?.length || 0}} nicht in DB.`;
      }} catch (err) {{
        status.textContent = err.message;
      }}
    }});
    updateExport();
  </script>
</body>
</html>
"""


def main() -> None:
    lid = 8277
    players = parse_players(fetch_html(BASE))
    OUTPUT.write_text(build_html(players, lid), encoding="utf-8")
    print(f"Wrote {len(players)} players to {OUTPUT}")


if __name__ == "__main__":
    main()
