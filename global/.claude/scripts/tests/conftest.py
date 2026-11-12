import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AFA_ENGINES_AVAILABLE", "*")  # tests assume every engine is installed; the engine-missing tests clear this
import closed_room_entry  # noqa: E402  ticket 81: the run happens inside closed_room.sh, where the real home cannot be reached
closed_room_entry.enter()
import state_guard  # noqa: E402,F401  refuses any write to the live ~/.claude/state for the whole run
