"""Issue 203: a ticket written with a heredoc and then checked with a script was refused as a search of the working folder. Fake folders only.
The prose of the ticket holds the word `find`. Because the same command wrote a file and ran a program outside the data tools, the wall gave
up reading the commands and took the wide reading, which counted that word as the find program and walked the starting folder (which holds a listed
folder). An interpreter that runs its own script file, or only prints its version, no longer causes the give-up. Every way the written file can
itself be run still takes the wide reading and is refused (the controls below)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_no_ai_wall as base  # noqa: E402

w = base.w

PROSE = "The check will find every step that has no link. Please find a way to search open and closed items."


class HeredocFalseAlarm(base.Fixture):
    """Same setup as the issue 130 tests: the session starts in self.tmp, a folder above the listed folders."""
    run_bash, allowed, refused = base.Issue130Tests.run_bash, base.Issue130Tests.allowed, base.Issue130Tests.refused

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))
        os.makedirs(w.wall_dir(), exist_ok=True)

    def ticket(self, tail):
        return "cat > t2-issue.md <<'EOF'\n%s\nEOF\n%s" % (PROSE, tail)

    # ---- the false alarms: allowed ----

    def test_a_ticket_then_a_python_script_of_its_own_is_allowed(self):
        self.allowed(self.ticket("python3 ~/.claude/scripts/check.py --strict t2-issue.md"), self.tmp)
        self.allowed(self.ticket("python3 check.py t2-issue.md"), self.tmp)
        self.allowed(self.ticket("python3 -I /opt/tools/check.py --strict --recipient team t2-issue.md"), self.tmp)
        self.allowed(self.ticket("node tools/check.js t2-issue.md"), self.tmp)

    def test_a_ticket_then_a_version_check_is_allowed(self):
        self.allowed(self.ticket("python3 --version"), self.tmp)
        self.allowed(self.ticket("python3 -V"), self.tmp)

    def test_a_ticket_written_with_tee_then_a_script_of_its_own_is_allowed(self):
        self.allowed("tee t2-issue.md <<'EOF' >/dev/null\n%s\nEOF\npython3 check.py t2-issue.md" % PROSE, self.tmp)

    # ---- the real refusals: the written file can itself be run ----

    def test_running_the_written_file_still_takes_the_wide_reading(self):
        for tail in ("bash t2-issue.md", "sh t2-issue.md", "python3 t2-issue.md", "source t2-issue.md", ". t2-issue.md", "./t2-issue.md",
                     "python3 ./t2-issue.md", "python3 -I t2-issue.md", "python3 /tmp/x/t2-issue.md"):
            self.refused(self.ticket(tail), self.tmp)

    def test_an_inline_program_after_a_written_file_still_takes_the_wide_reading(self):
        for tail in ("python3 -c 'import sys'", "node -e 1", "python3 -m http.server", "perl -e 1", "python3 -W ignore check.py"):
            self.refused(self.ticket(tail), self.tmp)

    def test_a_script_file_named_like_the_written_file_still_takes_the_wide_reading(self):
        # the text writes run.py with a redirect and then runs it: the file may hold anything, the ticket's prose is read as its commands
        self.refused("cat > run.py <<'EOF'\n%s\nEOF\npython3 run.py" % PROSE, self.tmp)
        self.refused("tee run.py <<'EOF' >/dev/null\n%s\nEOF\npython3 run.py" % PROSE, self.tmp)
        # a copy that is then run: no prose to misread, but the text is still not read with certainty
        self.assertIsNone(w._searches("cp tool.py run.py\npython3 run.py", self.tmp))

    def test_an_unknown_program_after_a_written_file_still_takes_the_wide_reading(self):
        for tail in ("make", "mytool t2-issue.md", "python3", "python3 -"):
            self.refused(self.ticket(tail), self.tmp)

    def test_a_real_search_in_the_same_text_is_still_refused(self):
        self.refused(self.ticket("python3 check.py t2-issue.md; find . -name x"), self.tmp)
        self.refused(self.ticket("python3 check.py t2-issue.md; grep -r secret %s" % self.p("wall/clientA")), self.tmp)
        self.refused(self.ticket("python3 check.py %s/docs/secret.txt" % self.p("wall/clientA")), self.tmp)


if __name__ == "__main__":
    unittest.main()
