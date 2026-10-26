import time
from app import analysis_service as m

def wrap(name):
    orig = getattr(m, name)
    def inner(*args, **kwargs):
        t = time.monotonic()
        try:
            return orig(*args, **kwargs)
        finally:
            print(name, round(time.monotonic() - t, 3), flush=True)
    setattr(m, name, inner)

for name in (
    "_load_analysis_data",
    "_evaluate_lineups",
    "_explain_recommendation",
    "_compute_lineup_configuration_spread_pp",
    "_build_info_summary",
    "_evaluate_one_own_order",
):
    wrap(name)

t = time.monotonic()
result = m.analyze_lineup(
    ["21772", "21773", "22183", "23754"],
    "Luftenberg 1",
    None,
    own_is_home=True,
    own_team="Tragwein/Kamig 2",
)
print("total", round(time.monotonic() - t, 3), "elapsed_field", result.get("analysis_elapsed_seconds"))
print("warnings", result.get("warnings"))
print("scenarios_source", result.get("scenario_source") or result.get("source"))
