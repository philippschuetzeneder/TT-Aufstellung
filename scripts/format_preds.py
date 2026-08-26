import json
import sys

d = json.load(open(sys.argv[1]))
for i, p in enumerate(d["opponent_predictions"], 1):
    names = [x["name"] for x in p["players"]]
    print(
        f"{i:2d}  {p['probability']*100:6.2f}  {p['model_probability']*100:6.2f}  "
        f"{names[0]} | {names[1]} | {names[2]} | {names[3]}"
    )
