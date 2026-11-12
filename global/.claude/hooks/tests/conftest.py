import importlib.util
import os

# Ticket 81: the hook tests run inside closed_room.sh too. The helper lives beside the scripts' tests; load it by path.
_entry = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "tests", "closed_room_entry.py")
_spec = importlib.util.spec_from_file_location("closed_room_entry", _entry)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_mod.enter()
