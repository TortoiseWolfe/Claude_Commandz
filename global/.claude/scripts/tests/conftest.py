import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_guard  # noqa: E402,F401  refuses any write to the live ~/.claude/state for the whole run
