import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.rc_import import import_rc_player

PASS_ID = "17885"
RC_ID = 52929

print(import_rc_player(RC_ID, xttv_external_player_id=PASS_ID, xttv_name="Meisinger Alexander"))
