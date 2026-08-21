"""Permanent manual XTTV -> RatingsCentral identity overrides.

These are explicit identity corrections for genuine duplicate-name cases.
They take precedence over name/recency matching.
"""

# Key: XTTV external/pass ID. Value: RatingsCentral PlayerID.
RC_PLAYER_OVERRIDES: dict[str, int] = {
    # Two different real players share the exact same XTTV/RC name.
    "21417": 53717,
    "23995": 65528,
    # Fuzzy RC candidates confirmed manually (no exact-name match in RC index).
    "50178": 52297,   # Davidovic Goran
    "74070": 157295,  # Jozic Gabriela
    "59537": 54883,   # Jozic Mladen
    "74030": 98766,   # Panholzer Celine
    "60084": 68569,   # Pavlovic Andrea
    "26119": 159400,  # Pichler Alexander (RC profile, no XTTV RC-Graph)
}
