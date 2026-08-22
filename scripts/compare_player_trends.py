"""Trend and strength comparison for validation."""
from __future__ import annotations

from datetime import date

from app.analysis_service import (
    RC_BASELINE,
    RC_SCALE,
    RC_COMPONENT_WEIGHT,
    TREND_YEARS,
    _combined_strength,
    _cutoff,
    _empty_profile,
    _load_player_profiles,
)
from app.db import SessionLocal, create_all
from app.models import PlayerRatingSnapshot, XttvPlayer


def profile_for(external_id: str) -> dict:
    create_all()
    with SessionLocal() as session:
        player = session.query(XttvPlayer).filter_by(external_player_id=external_id).one()
        cutoff = _cutoff(date.today(), TREND_YEARS)
        snaps = (
            session.query(PlayerRatingSnapshot)
            .filter(
                PlayerRatingSnapshot.player_id == player.id,
                PlayerRatingSnapshot.source == "ratingscentral",
                PlayerRatingSnapshot.observed_at >= cutoff,
            )
            .order_by(PlayerRatingSnapshot.observed_at)
            .all()
        )
        latest = (
            session.query(PlayerRatingSnapshot)
            .filter_by(player_id=player.id, source="ratingscentral")
            .order_by(PlayerRatingSnapshot.observed_at.desc())
            .first()
        )
        _, profiles, _ = _load_player_profiles(session, [external_id], date.today())

    profile = profiles.get(external_id, _empty_profile())
    profile["rc_rating"] = float(latest.rc_rating) if latest else None
    strength = _combined_strength(profile)
    strength_no_trend = _combined_strength({**profile, "trend_component": 0.0})
    rc_component = (
        (profile["rc_rating"] - RC_BASELINE) / RC_SCALE * RC_COMPONENT_WEIGHT
        if profile["rc_rating"] else None
    )

    return {
        "name": player.name,
        "xttv": external_id,
        "rc": player.rc_player_id,
        "snap_count": len(snaps),
        "first": snaps[0] if snaps else None,
        "last": latest,
        "trend_momentum": profile.get("rc_trend", 0.0),
        "rc_rating": profile["rc_rating"],
        "rc_dev": latest.rc_deviation if latest else None,
        "strength": strength,
        "strength_without_trend": strength_no_trend,
        "trend_component": profile.get("trend_component", 0.0),
        "rc_component": rc_component,
    }


def main() -> None:
    players = {
        "21773": "Schützeneder Philipp",
        "24890": "Dreiling Tobias",
    }
    results = {ext: profile_for(ext) for ext in players}

    for ext, label in players.items():
        r = results[ext]
        print(f"=== {label} ===")
        print(f"XTTV {r['xttv']} | RC {r['rc']}")
        print(f"RC Rating: {r['rc_rating']} ± {r['rc_dev']}")
        print(f"Snapshots ({TREND_YEARS}-Jahres-Trend-Fenster): {r['snap_count']}")
        if r["first"]:
            print(f"Trend Start: {r['first'].observed_at.date()} → {r['first'].rc_rating}")
        if r["last"]:
            print(f"Trend Ende:  {r['last'].observed_at.date()} → {r['last'].rc_rating}")
        print(f"RC-Trend (gewichtete Momentum): {r['trend_momentum']:.1f}")
        print(f"RC-Komponente Stärke: {r['rc_component']:.4f}")
        print(f"Trend-Komponente Stärke: {r['trend_component']:.4f}")
        print(f"Kombinierte Stärke (mit Trend): {r['strength']:.4f}")
        print(f"Kombinierte Stärke (ohne Trend): {r['strength_without_trend']:.4f}")
        print(f"Trend-Einfluss auf Stärke: {r['strength'] - r['strength_without_trend']:.4f}")
        print()

    a, b = results["21773"], results["24890"]
    print("=== Vergleich Philipp vs Tobias ===")
    print(f"RC Rating: {a['rc_rating']} vs {b['rc_rating']} (Δ {a['rc_rating'] - b['rc_rating']:.1f})")
    print(f"Trend-Momentum: {a['trend_momentum']:.1f} vs {b['trend_momentum']:.1f}")
    print(f"Stärke: {a['strength']:.4f} vs {b['strength']:.4f} (Δ {a['strength'] - b['strength']:.4f})")


if __name__ == "__main__":
    main()
