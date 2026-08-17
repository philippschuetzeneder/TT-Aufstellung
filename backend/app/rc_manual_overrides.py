"""Permanent manual XTTV -> RatingsCentral identity overrides.

These are explicit identity corrections for genuine duplicate-name cases.
They take precedence over name/recency matching.
"""

# Key: XTTV external/pass ID. Value: RatingsCentral PlayerID.
RC_PLAYER_OVERRIDES: dict[str, int] = {
    # Two different real players share the exact same XTTV/RC name.
    "21417": 53717,
    "23995": 65528,
}
