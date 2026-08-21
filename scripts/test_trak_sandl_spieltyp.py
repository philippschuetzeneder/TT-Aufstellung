from app.analysis_service import analyze_lineup

own = ["21773", "24890", "23782", "24889"]
opp_known = ["70433", "70417", "22938", "23882"]
opp_team = "Sandl 1"

for home in (True, False):
    print("===", "Heim" if home else "Gast", "===")
    for use in (False, True):
        r = analyze_lineup(
            own,
            opp_team,
            actual_opponent_ids=opp_known,
            own_is_home=home,
            use_spieltyp=use,
        )
        rec = r["recommendation"]
        p = rec["team_win_probability"] * 100
        label = "an" if use else "aus"
        print(f"  spieltyp {label:3}  Sieg {p:5.2f}%  Aufstellung {rec['own_player_ids']}")
    print()

r0 = analyze_lineup(own, opp_team, actual_opponent_ids=opp_known, own_is_home=True, use_spieltyp=False)
r1 = analyze_lineup(own, opp_team, actual_opponent_ids=opp_known, own_is_home=True, use_spieltyp=True)
p0 = r0["recommendation"]["team_win_probability"]
p1 = r1["recommendation"]["team_win_probability"]
print("Delta Sieg Heim:", round((p1 - p0) * 100, 2), "pp")
print("Anzeige 1 Dezimal:", f"{p0*100:.1f}% vs {p1*100:.1f}%")
