"""Import explicit MEID list (prod maintenance). Usage: python scripts/import_meid_list.py 462232 462233"""
import sys

from app.xttv_db_import import import_one


def main() -> None:
    meids = [int(a) for a in sys.argv[1:]]
    if not meids:
        print("usage: import_meid_list.py MEID ...", file=sys.stderr)
        sys.exit(1)
    for m in meids:
        try:
            r = import_one(m)
            print(m, "ok", r.get("home_team"), "vs", r.get("away_team"))
        except Exception as exc:
            print(m, "fail", exc)


if __name__ == "__main__":
    main()
