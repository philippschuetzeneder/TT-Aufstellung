"""Prod read-only: Trak 2 vs Allerheiligen lineup flatness + counterfactuals."""
from __future__ import annotations

import json
import math
import sys
from itertools import permutations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.analysis_service as mod
from app.analysis_service import (
    SCENARIO_SHARPENING_ALPHA,
    _adaptive_strength_weight,
    _evaluate_lineups,
    _lineup_cohesion,
    _load_analysis_data,
    _load_doubles_stats,
    _load_latest_rc_map,
    _load_player_profiles,
    _merge_orientations,
    _reference_date,
    _resolve_rc_rating,
    _sharpen_scenarios,
    analyze_lineup,
)
from app.db import SessionLocal
from sqlalchemy import text

OWN_TEAM = "Tragwein/Kamig 2"
OPP_TEAM = "Allerheiligen 1"


def resolve_ids():
    with SessionLocal() as db:
        def one_player(team: str, pattern: str) -> str | None:
            return db.execute(
                text(
                    """
                    SELECT mp.external_player_id::text
                    FROM match_players mp
                    JOIN xttv_matches m ON m.id = mp.match_id
                    WHERE (m.home_team = :team OR m.away_team = :team)
                      AND mp.name ILIKE :p
                    GROUP BY mp.external_player_id
                    ORDER BY max(m.match_date) DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"team": team, "p": pattern},
            ).scalar()

        def xp(pattern: str) -> str | None:
            return db.execute(
                text(
                    "SELECT external_player_id FROM xttv_players WHERE name ILIKE :p LIMIT 1"
                ),
                {"p": pattern},
            ).scalar()

        own = [
            one_player(OWN_TEAM, "%Friedinger%Niklas%") or xp("%Friedinger%Niklas%") or "23784",
            one_player(OWN_TEAM, "%Julian%") or xp("%Hahn%Julian%"),
            one_player(OWN_TEAM, "%Hannes%") or one_player(OWN_TEAM, "%Meisinger%"),
            one_player(OWN_TEAM, "%Schmalzer%Martin%") or one_player(OWN_TEAM, "%Martin%Schmalzer%") or "22183",
        ]
        opp = [
            one_player(OPP_TEAM, "%Franz%"),
            one_player(OPP_TEAM, "%Martin%"),
            one_player(OPP_TEAM, "%Tobias%"),
            one_player(OPP_TEAM, "%Thomas%"),
        ]
    own = [str(x) for x in own if x]
    opp = [str(x) for x in opp if x]
    return own, opp


def margins(result, k=5):
    recs = result.get("recommendations") or []
    if not recs:
        return {}
    top = recs[0]["team_win_probability"]
    return {
        "top1_pct": round(top * 100, 3),
        "m12_pp": round((top - recs[1]["team_win_probability"]) * 100, 4) if len(recs) > 1 else 0,
        "loss6_pp": round((top - recs[min(k, len(recs) - 1)]["team_win_probability"]) * 100, 4),
        "spread_pp": (result.get("recommendation") or {}).get("lineup_spread_pp"),
        "adv_strength_pp": (result.get("recommendation") or {}).get("advantage_vs_strength_lineup_pp"),
        "top": (result.get("recommendation") or {}).get("players"),
    }


def main() -> None:
    own, opp = resolve_ids()
    print("Resolved own:", own)
    print("Resolved opp:", opp)
    if len(own) != 4 or len(opp) != 4:
        print("ERROR: need 4+4 players")
        sys.exit(1)

    with SessionLocal() as db:
        ref = _reference_date(db)
        sw, exact, trio = _adaptive_strength_weight(db, opp, ref)
        _, ex_m, _, tr_m = _lineup_cohesion(db, opp, ref)
        names, profiles, _ = _load_player_profiles(db, own + opp, ref)
        _, _, _, scenarios, source, _, _, _ = _load_analysis_data(own, OPP_TEAM, opp)
        rc = _load_latest_rc_map(db, own + opp)

    for label, ids in (("heim", own), ("gast", opp)):
        vals = [_resolve_rc_rating(i, rc, ids) for i in ids]
        vals = [v for v in vals if v is not None]
        print(f"RC spread {label}:", round(max(vals) - min(vals), 1) if len(vals) >= 2 else None)
        for pid in ids:
            print(f"  {names.get(pid, pid):28} RC={profiles.get(pid, {}).get('rc_rating')}")

    probs = [p for p, _ in scenarios]
    ent = -sum(p * math.log(p) for p in probs if p > 0)
    print(f"source={source} n_scenarios={len(scenarios)} top_pct={max(probs)*100:.2f} entropy={ent:.3f}")
    print(f"cohesion exact={exact} mass={ex_m:.2f} strength_weight={sw:.2f} alpha={SCENARIO_SHARPENING_ALPHA}")

    base = analyze_lineup(
        own, OPP_TEAM, actual_opponent_ids=opp,
        own_is_home=True, own_team=OWN_TEAM, opponent_on_letters=True,
    )
    print("BASELINE", json.dumps(margins(base), ensure_ascii=False))
    for r in (base.get("recommendations") or [])[1:6]:
        print(f"  rank{r['rank']} loss_pp={r.get('loss_pp_vs_optimal')} {r.get('players')}")

    uniform = [(1 / 24.0, o) for o in permutations(opp)]
    top1 = [max(scenarios, key=lambda x: x[0])]

    def cf(label, *, alpha=None, sc_override=None):
        saved = mod.SCENARIO_SHARPENING_ALPHA
        if alpha is not None:
            mod.SCENARIO_SHARPENING_ALPHA = alpha
        mod.clear_analysis_runtime_caches()
        try:
            if sc_override is not None:
                import time
                names2, profiles2, matchups2, _, _, ref2, _, _ = _load_analysis_data(own, OPP_TEAM, opp)
                sc = _sharpen_scenarios(sc_override)
                with SessionLocal() as db:
                    ds = _load_doubles_stats(db, own, set(opp), OWN_TEAM, OPP_TEAM, ref2)
                t0 = time.monotonic()
                h, _ = _evaluate_lineups(own, sc, profiles2, matchups2, names2, True, t0, doubles_stats=ds, own_on_letters=False)
                a, _ = _evaluate_lineups(own, sc, profiles2, matchups2, names2, False, t0, doubles_stats=ds, own_on_letters=False)
                ev = _merge_orientations(h, a)
                ev.sort(key=lambda x: (-x["team_win_probability"], x["own_player_ids"]))
                top = ev[0]["team_win_probability"]
                out = {
                    "m12_pp": round((top - ev[1]["team_win_probability"]) * 100, 4),
                    "loss6_pp": round((top - ev[min(5, len(ev) - 1)]["team_win_probability"]) * 100, 4),
                }
            else:
                r = analyze_lineup(
                    own, OPP_TEAM, actual_opponent_ids=opp,
                    own_is_home=True, own_team=OWN_TEAM, opponent_on_letters=True,
                )
                out = margins(r)
            print(f"CF {label}:", json.dumps(out, ensure_ascii=False))
        finally:
            mod.SCENARIO_SHARPENING_ALPHA = saved
            mod.clear_analysis_runtime_caches()

    cf("alpha=1", alpha=1.0)
    cf("alpha=5", alpha=5.0)
    cf("uniform_opp", sc_override=uniform)
    cf("top_opp_only", sc_override=top1)


if __name__ == "__main__":
    main()
