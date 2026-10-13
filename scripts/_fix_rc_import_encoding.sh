#!/usr/bin/env bash
set -eu
F=/app/backend/app/rc_import.py
python3 <<'PY'
from pathlib import Path
p = Path("/app/backend/app/rc_import.py")
text = p.read_text(encoding="utf-8", errors="replace")
text = text.replace('re.compile(r"??|', 're.compile(r"\\u00b1|')
text = text.replace('.replace("???", "-")', '.replace("\\u2212", "-").replace("\\u2013", "-")')
# normalize any remaining broken plus-minus in regex fragments
text = text.replace("(?:??|", "(?:\\u00b1|")
p.write_text(text, encoding="utf-8")
print("patched", p)
PY
python3 -c "from app.rc_import import parse_player_history; print('import ok')"
