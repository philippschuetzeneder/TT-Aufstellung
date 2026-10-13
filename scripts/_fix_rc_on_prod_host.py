import pathlib

p = pathlib.Path("/opt/tt-aufstellung/backend/app/rc_import.py")
t = p.read_text(encoding="utf-8", errors="replace")
t = t.replace("(?:??|", "(?:\\u00b1|")
t = t.replace('re.compile(r"??|', 're.compile(r"\\u00b1|')
t = t.replace('.replace("???", "-")', '.replace("\\u2212", "-").replace("\\u2013", "-")')
p.write_text(t, encoding="utf-8")
print("patched", p)
