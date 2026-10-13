from app.rc_import import _RATING_RE

print("pattern:", _RATING_RE.pattern)
s = "1393 \u00b1 45.2"
print("match:", _RATING_RE.match(s).groups())
