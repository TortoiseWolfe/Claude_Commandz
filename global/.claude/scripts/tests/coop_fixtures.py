"""Synthetic people for the co-op scrubber tests. None of these is a real person, address or number."""
import json
import os
import sys

ZELDA = "Zelda Quillfeather"
BARNABY = "Barnaby Thistlewood"
ZELDA_EMAIL = "zelda.q@example.invalid"
BARNABY_EMAIL = "barnaby@example.invalid"
ZELDA_PHONE = "(423) 555-0142"
BARNABY_PHONE = "+1 423 555 0177"
ROSTER = {"salt": "testsalt", "people": [
    {"names": [ZELDA], "emails": [ZELDA_EMAIL], "phones": [ZELDA_PHONE]},
    {"names": [BARNABY], "emails": [BARNABY_EMAIL], "phones": [BARNABY_PHONE]},
]}

# A stand-in for the Presidio venv: reports every capitalised word pair that starts with "Wendell" as a PERSON, and also
# "Standard Nextcloud", "Zephyr Lantern" and "Standard Lantern", the kind of product phrase the real one sometimes reads as a person (issue 239).
FAKE_PRESIDIO = '''#!/usr/bin/env python3
import json, re, sys
texts = json.load(sys.stdin)["texts"]
out = [[[m.start(), m.end(), "PERSON"] for m in re.finditer(r"Wendell [A-Z][a-z]+|Standard Nextcloud|Zephyr Lantern|Standard Lantern", t)] for t in texts]
json.dump({"spans": out}, sys.stdout)
'''


def write_fake_presidio(folder):
    """Returns (python, worker): the current Python and a worker script standing in for coop_scrubber_presidio.py."""
    worker = os.path.join(folder, "worker.py")
    with open(worker, "w") as fh:
        fh.write(FAKE_PRESIDIO)
    return sys.executable, worker


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh)
