#!/usr/bin/env python3
"""A stand-in for hub/scripts/mail_index.py in the muse_poll_record.sh tests (MUSE_MAIL_INDEX_PY points at it).
It records every call to the file named by FAKE_MAIL_INDEX_LOG, one line per call, and exits with FAKE_MAIL_INDEX_RC
(default 0). With FAKE_MAIL_INDEX_RC_FIRST set, the first `handled` call exits with that code and later ones with the
default, which is how the "no row yet, update, try again" path is tested. The real command is tested in the hub repo
(hub/scripts/test_mail_index.py); no mail, address or index is touched here."""

import os
import sys

log = os.environ.get("FAKE_MAIL_INDEX_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(" ".join(sys.argv[1:]) + "\n")
first = os.environ.get("FAKE_MAIL_INDEX_RC_FIRST")
if first and sys.argv[1:2] == ["handled"] and log:
    with open(log, encoding="utf-8") as f:
        calls = [ln for ln in f if ln.startswith("handled")]
    if len(calls) == 1:
        sys.exit(int(first))
sys.exit(int(os.environ.get("FAKE_MAIL_INDEX_RC", "0")))
