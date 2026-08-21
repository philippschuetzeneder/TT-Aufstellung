from app.spieltyp_service import normalize_spieltyp, parse_bulk_lines


def test_normalize_aliases():
    assert normalize_spieltyp("O") == "offensive"
    assert normalize_spieltyp("Noppenspieler") == "pips"
    assert normalize_spieltyp("defensiv") == "defensive"
    assert normalize_spieltyp("-") is None


def test_parse_bulk_lines():
    rows = parse_bulk_lines("21773 O\n24890 N\n23782 defensive\n")
    assert rows[0]["pass_id"] == "21773"
    assert rows[0]["spieltyp"] == "offensive"
    assert rows[1]["spieltyp"] == "pips"
    assert rows[2]["spieltyp"] == "defensive"


def test_style_component_bounds():
    from app.analysis_service import _empty_profile, _style_component, SPIELTYP_MAX_COMPONENT

    own = _empty_profile()
    own["wins"] = 20
    own["games"] = 40
    own["style_matchups"] = {"offensive": (8, 10)}
    opp = _empty_profile()
    opp["spieltyp"] = "offensive"
    component = _style_component(own, opp)
    assert -SPIELTYP_MAX_COMPONENT <= component <= SPIELTYP_MAX_COMPONENT
