import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AFA_ENGINES_AVAILABLE", "*")  # tests assume every engine is installed; the engine-missing tests clear this
import state_guard  # noqa: E402,F401  refuses any write to the live ~/.claude/state for the whole run
