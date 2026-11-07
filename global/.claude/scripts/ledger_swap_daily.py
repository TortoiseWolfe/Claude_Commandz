#!/usr/bin/env python3
"""The daily half of the two-machine ledger swap that a timer runs. No model, never sends mail.

  ledger_swap_daily.py export   stage this machine's ledger note as a Gmail draft to its own owner (ledger_mirror.py export)
  ledger_swap_daily.py import   read the other machine's newest ledger note and save it (ledger_mirror.py import)
  ledger_swap_daily.py run      export, then import; exit 1 if either failed (both are always tried)
  add --dry-run to any of them: print what would happen, stage and save nothing (the import is parsed and checked against a
  throw-away file, so a bad note shows up here too).

The note the other machine staged is found by its exact subject ("[PEER>ME] ledger YYYY-MM-DD") in the owner's own mailbox, in
any folder but Spam (a draft the notes poller already filed away still counts). Only a message whose From and To are exactly the
mailbox owner's address AND that carries the DRAFT or SENT label is read, because anyone can write a subject line and a From header
but only this account's own drafts and sent mail get those labels; ledger_mirror.py then checks every line of the body.
The export is one draft a day: a second run on the same day rewrites that day's draft in place, and a note the other machine
already read is simply replaced by a fresher one. Every run appends one line to ~/.claude/state/ledger-swap.log.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from email.utils import getaddresses, parseaddr
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_mirror(path):
    spec = importlib.util.spec_from_file_location("ledger_mirror_for_swap", path)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def sh(cmd, text_in=None, timeout=120):
    return subprocess.run(cmd, input=text_in, capture_output=True, text=True, timeout=timeout)


class Swap:
    def __init__(self, a):
        self.a = a
        self.mirror = str(a.mirror)
        self.gmail = str(a.gmail)
        m = load_mirror(self.mirror)
        self.me = m.own_machine()
        self.peer = a.peer or m.default_peer()
        self.log_path = Path(a.log)
        self.owner = None

    def log(self, what):
        line = "%s %s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M"), "DRY" if self.a.dry_run else "LIVE", what)
        print(line)
        if not self.a.dry_run:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def gm(self, *args):
        return sh([sys.executable, "-I", self.gmail, *args])

    def owner_address(self):
        if self.owner is None:
            r = self.gm("whoami")
            self.owner = r.stdout.strip().splitlines()[0].strip().lower() if r.returncode == 0 and r.stdout.strip() else ""
        return self.owner

    def export(self):
        r = sh([sys.executable, "-I", self.mirror, "export", "--peer", self.peer] + (["--all"] if self.a.all else []))
        if r.returncode != 0 or not r.stdout.startswith("Subject: "):
            self.log("export FAILED: ledger_mirror.py export gave exit %d" % r.returncode)
            return 1
        head, _, body = r.stdout.partition("\n\n")
        subject = head[len("Subject: "):].strip()
        lines = body.count("\n") - 2 if "END-LEDGER" in body else 0
        owner = self.owner_address()
        if not owner:
            self.log("export FAILED: could not read the mailbox owner (gmail_api.py whoami)")
            return 1
        found = self.gm("drafts-list", "--limit", "10", 'subject:"%s"' % subject)
        same = [ln.split("\t")[0] for ln in found.stdout.splitlines() if ln.count("\t") >= 3 and ln.split("\t")[3].strip() == subject]
        if self.a.dry_run:
            self.log("export would stage a draft to the owner, subject %r, %d ledger line(s), %s" % (
                subject, lines, "rewriting draft in place" if same else "as a new draft"))
            return 0
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "note.txt"
            f.write_text(body)
            cmd = ["draft-create", "--to", owner, "--subject", subject, "--text", str(f)]
            if same:
                cmd += ["--replace", same[0]]
            c = self.gm(*cmd)
        if c.returncode != 0:
            self.log("export FAILED: draft-create exit %d" % c.returncode)
            return 1
        self.log("export staged %r, %d ledger line(s), %s" % (subject, lines, "replaced today's draft" if same else "new draft"))
        return 0

    def newest_peer_note(self):
        """-> (message id, subject) of the newest note from the other machine in the owner's own mailbox, or None."""
        tag = "[%s>%s] ledger" % (self.peer, self.me)
        owner = self.owner_address()
        # in:anywhere because the notes poller files a read note away (Trash included); spam stays out
        r = self.gm("messages-list", "--limit", "10", 'subject:"%s" in:anywhere -in:spam' % tag)
        if r.returncode != 0 or not owner:
            return None
        for ln in r.stdout.splitlines():
            # messages-list prints: MSG id | sender address | From | Subject | Date | labels | threadId.
            # A header that carries " | " shifts the fields, so anything but exactly seven fields is refused.
            parts = [p.strip() for p in ln.split(" | ")]
            if len(parts) != 7 or not parts[0].startswith("MSG ") or not parts[3].startswith(tag):
                continue
            # Mail anyone can send carries INBOX; DRAFT and SENT are set only on messages this account wrote itself.
            if not {"DRAFT", "SENT"} & set(parts[5].split(",")):
                continue
            # exact addresses, never a substring: "owner@example.com.evil.net" and "evil+owner@..." must not pass
            if parts[1].lower() != owner or parseaddr(parts[2])[1].lower() != owner:
                continue
            # the list line carries no To, so read that header of this one message on its own
            h = self.gm("message-header", parts[0][4:], "To")
            if h.returncode != 0 or [a.lower() for _, a in getaddresses([h.stdout.strip()])] != [owner]:
                continue
            return parts[0][4:], parts[3]
        return None

    def import_(self):
        found = self.newest_peer_note()
        if not found:
            self.log("import: no note from %s found yet" % self.peer)
            return 0
        msg_id, subject = found
        t = self.gm("message-text", "--full", msg_id)
        if t.returncode != 0 or "LEDGER-V1" not in t.stdout:
            self.log("import FAILED: could not read the body of %r" % subject)
            return 1
        cmd = [sys.executable, "-I", self.mirror, "import", "-", "--peer", self.peer]
        with tempfile.TemporaryDirectory() as d:
            if self.a.dry_run:
                cmd += ["--peer-file", str(Path(d) / "dry-run.md")]
            r = sh(cmd, text_in=t.stdout)
        said = (r.stdout.strip() or r.stderr.strip()).splitlines()
        said = said[-1] if said else ""
        if r.returncode != 0:
            # an older-than-saved note is the normal case on a second run in a day; any other refusal is a real failure
            if "older" in said:
                self.log("import: %r is not newer than the one already saved" % subject)
                return 0
            self.log("import FAILED: %s" % said[:200])
            return 1
        self.log("import %s: %r: %s" % ("checked" if self.a.dry_run else "saved", subject, said[:160]))
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["export", "import", "run"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true", help="export: every fresh open item (passed to ledger_mirror.py export)")
    ap.add_argument("--peer")
    ap.add_argument("--mirror", default=str(HERE / "ledger_mirror.py"))
    ap.add_argument("--gmail", default=str(HERE / "gmail_api.py"))
    ap.add_argument("--log", default=str(Path(os.path.expanduser("~")) / ".claude/state/ledger-swap.log"))
    a = ap.parse_args(argv)
    s = Swap(a)
    rc = 0
    if a.cmd in ("export", "run"):
        rc |= s.export()
    if a.cmd in ("import", "run"):
        rc |= s.import_()
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
