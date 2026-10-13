#!/usr/bin/env bash
# Read-only: Trak vs Allerheiligen lineup flatness (run on VPS).
set -eu
cd /opt/tt-aufstellung
COMPOSE=(docker compose -f docker-compose.prod.yml)

run_psql() {
  "${COMPOSE[@]}" exec -T postgres psql -U tt -d tt_aufstellung -v ON_ERROR_STOP=1 "$@"
}

echo "=== API smoke (origin) ==="
curl -sS -m 5 -o /dev/null -w "home: %{http_code} %{time_total}s\n" https://tt-aufstellung.at/ || true
curl -sS -m 15 -o /dev/null -w "teams_api: %{http_code} %{time_total}s\n" \
  "https://tt-aufstellung.at/api/teams/players?team=Tragwein%2FKamig%202" || true

echo "=== Player IDs (xttv_players) ==="
run_psql -c "
SET statement_timeout = '30s';
SELECT external_player_id, name FROM xttv_players
WHERE name ILIKE '%Friedinger%Niklas%'
   OR name ILIKE '%Hahn%Julian%'
   OR name ILIKE '%Meisinger%Hannes%'
   OR name ILIKE '%Schmalzer%Martin%'
   OR name ILIKE '%Allerheiligen%' 
ORDER BY name LIMIT 30;
"

echo "=== Allerheiligen 1 roster sample ==="
run_psql -c "
SET statement_timeout = '60s';
SELECT DISTINCT mp.external_player_id::text AS pid, max(mp.name) AS name
FROM match_players mp
JOIN xttv_matches m ON m.id = mp.match_id
WHERE m.home_team = 'Allerheiligen 1' OR m.away_team = 'Allerheiligen 1'
GROUP BY mp.external_player_id
ORDER BY name LIMIT 40;
"

"${COMPOSE[@]}" exec -T app python <<'PY'
import json
import math
import app.analysis_service as mod
from app.analysis_service import (
    SCENARIO_SHARPENING_ALPHA,
    _adaptive_strength_weight,
    _lineup_cohesion,
    _load_analysis_data,
    _load_latest_rc_map,
    _load_player_profiles,
    _reference_date,
    _resolve_rc_rating,
    _sharpen_scenarios,
    analyze_lineup,
)
from app.db import SessionLocal
from itertools import permutations

# Trak 2 vs Allerheiligen — resolve by known Pass-Nr / names from DB
OWN_TEAM = "Tragwein/Kamig 2"
OPP_TEAM = "Allerheiligen 1"

def pick_ids():
    from sqlalchemy import text
    with SessionLocal() as db:
        def by_name(pattern):
            return db.execute(
                text(
                    "SELECT external_player_id FROM xttv_players WHERE name ILIKE :p LIMIT 1"
                ),
                {"p": pattern},
            ).scalar()
        own = [
            str(by_name("%Friedinger%Niklas%") or "23784"),
            str(by_name("%Hahn%Julian%") or by_name("%Julian%") or ""),
            str(by_name("%Meisinger%Hannes%") or by_name("%Hannes%") or ""),
            str(by_name("%Schmalzer%Martin%") or by_name("%Martin%Schmalzer%") or "22183"),
        ]
        opp_names = ["%Franz%", "%Martin%", "%Tobias%", "%Thomas%"]
        opp = []
        for p in opp_names:
            row = db.execute(
                text(
                    """
                    SELECT DISTINCT mp.external_player_id::text
                    FROM match_players mp
                    JOIN xttv_matches m ON m.id = mp.match_id
                    WHERE (m.home_team = :t OR m.away_team = :t)
                      AND mp.name ILIKE :p
                    LIMIT 1
                    """
                ),
                {"t": OPP_TEAM, "p": p},
            ).scalar()
            opp.append(str(row) if row else "")
    return [x for x in own if x], [x for x in opp if x]

own, opp = pick_ids()
print("=== Resolved IDs ===")
print("own", own)
print("opp", opp)
if len(own) != 4 or len(opp) != 4:
    print("WARN: need 4+4; analysis may be partial")

ref = _reference_date(SessionLocal())
with SessionLocal() as db:
    sw, exact, trio = _adaptive_strength_weight(db, opp or own, ref)
    ex_n, ex_m, tr_n, tr_m = _lineup_cohesion(db, opp or own, ref)
    names, profiles, _m = _load_player_profiles(db, list(set(own + opp)), ref)
    _, _, _, scenarios, source, _, _, _ = _load_analysis_data(own, OPP_TEAM, opp if len(opp)==4 else None)

rc = _load_latest_rc_map(db, own + opp)
for label, ids in (("heim", own), ("gast", opp)):
    vals = [_resolve_rc_rating(i, rc, ids) for i in ids]
    vals = [v for v in vals if v is not None]
    spread = max(vals) - min(vals) if len(vals) >= 2 else None
    print(f"RC spread {label}: {spread}")
    for pid in ids:
        print(f"  {names.get(pid,pid):30} RC={profiles.get(pid,{}).get('rc_rating')}")

probs = [p for p, _ in scenarios] if scenarios else []
ent = -sum(p * math.log(p) for p in probs if p > 0) if probs else 0
print(f"source={source} scenarios={len(scenarios)} top_scenario_pct={max(probs)*100 if probs else 0:.2f} entropy={ent:.3f}")
print(f"cohesion exact4={exact} trio={trio} strength_weight={sw:.2f}")

def margins(result, k=5):
    recs = result.get("recommendations") or []
    if not recs:
        return {}
    top = recs[0]["team_win_probability"]
    return {
        "top1_pct": round(top * 100, 3),
        "m12_pp": round((top - recs[1]["team_win_probability"]) * 100, 4) if len(recs) > 1 else 0,
        "loss6_pp": round((top - recs[min(5, len(recs)-1)]["team_win_probability"]) * 100, 4),
        "spread_pp": (result.get("recommendation") or {}).get("lineup_spread_pp"),
        "adv_strength_pp": (result.get("recommendation") or {}).get("advantage_vs_strength_lineup_pp"),
        "top_players": (result.get("recommendation") or {}).get("players"),
    }

if len(own) == 4 and len(opp) == 4:
    base = analyze_lineup(
        own, OPP_TEAM, actual_opponent_ids=opp,
        own_is_home=True, own_team=OWN_TEAM, opponent_on_letters=True,
    )
    print("=== Baseline ===", json.dumps(margins(base), ensure_ascii=False))
    recs = base.get("recommendations") or []
    for r in recs[1:6]:
        print(f"  rank{r['rank']} loss_pp={r.get('loss_pp_vs_optimal')} {r.get('players')}")

    def run_cf(alpha=None, scenarios_override=None):
        saved = mod.SCENARIO_SHARPENING_ALPHA
        if alpha is not None:
            mod.SCENARIO_SHARPENING_ALPHA = alpha
        mod.clear_analysis_runtime_caches()
        try:
            if scenarios_override is not None:
                from app.analysis_service import _evaluate_lineups, _merge_orientations, _load_doubles_stats
                names2, profiles2, matchups2, _, _, ref2, _, _ = _load_analysis_data(own, OPP_TEAM, opp)
                sc = _sharpen_scenarios(scenarios_override)
                with SessionLocal() as db:
                    ds = _load_doubles_stats(db, own, set(opp), OWN_TEAM, OPP_TEAM, ref2)
                import time
                t0 = time.monotonic()
                h, _ = _evaluate_lineups(own, sc, profiles2, matchups2, names2, True, t0, doubles_stats=ds, own_on_letters=False)
                a, _ = _evaluate_lineups(own, sc, profiles2, matchups2, names2, False, t0, doubles_stats=ds, own_on_letters=False)
                ev = _merge_orientations(h, a)
                ev.sort(key=lambda x: (-x["team_win_probability"], x["own_player_ids"]))
                top = ev[0]["team_win_probability"]
                return {
                    "m12_pp": round((top - ev[1]["team_win_probability"]) * 100, 4),
                    "loss6_pp": round((top - ev[min(5, len(ev)-1)]["team_win_probability"]) * 100, 4),
                }
            r = analyze_lineup(
                own, OPP_TEAM, actual_opponent_ids=opp,
                own_is_home=True, own_team=OWN_TEAM, opponent_on_letters=True,
            )
            return margins(r)
        finally:
            mod.SCENARIO_SHARPENING_ALPHA = saved
            mod.clear_analysis_runtime_caches()

    uniform = [(1/24.0, o) for o in permutations(opp)]
    top1 = [max(scenarios, key=lambda x: x[0])] if scenarios else uniform[:1]
    print("=== Counterfactuals (what would steepen?) ===")
    for label, kw in [
        ("alpha=1.0", {"alpha": 1.0}),
        ("alpha=5.0", {"alpha": 5.0}),
        ("uniform opp scenarios", {"scenarios_override": uniform}),
        ("only top opp scenario", {"scenarios_override": top1}),
    ]:
        print(label, run_cf(**kw))
PY
