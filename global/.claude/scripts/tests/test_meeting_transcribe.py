"""Tests for meeting_transcribe. Unit tests need no GPU or Docker; the smoke
test (set MEETING_SMOKE=1) runs the real container end to end."""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MT = os.path.join(os.path.dirname(HERE), "meeting_transcribe")
RUN = os.path.join(MT, "run.sh")
spec = importlib.util.spec_from_file_location("transcribe", os.path.join(MT, "transcribe.py"))
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


def seg(s, e, who, text):
    return {"start": s, "end": e, "speaker": who, "text": text}


class PureLogic(unittest.TestCase):
    def test_hms(self):
        self.assertEqual(tr.fmt_hms(0), "00:00:00")
        self.assertEqual(tr.fmt_hms(3725.9), "01:02:05")

    def test_srt_time(self):
        self.assertEqual(tr.fmt_srt_time(3725.5), "01:02:05,500")
        self.assertEqual(tr.fmt_srt_time(0.0004), "00:00:00,000")

    def test_merge_orders_by_start(self):
        m = tr.merge_segments([seg(5, 6, "B", "x"), seg(1, 2, "A", "y")])
        self.assertEqual([g["speaker"] for g in m], ["A", "B"])

    def test_collapse(self):
        segs = [seg(0, 1, "A", "one"), seg(1, 2, "A", "two"),
                seg(3, 4, "B", "three"), seg(5, 6, "A", "four"), seg(7, 8, "A", " ")]
        t = tr.collapse_turns(segs)
        self.assertEqual([(x["speaker"], x["text"]) for x in t],
                         [("A", "one two"), ("B", "three"), ("A", "four")])
        self.assertEqual(t[0]["end"], 2)

    def test_srt_output(self):
        s = tr.to_srt([seg(0, 1.5, "A", "hi"), seg(2, 3, "B", "yo")])
        self.assertIn("1\n00:00:00,000 --> 00:00:01,500\nA: hi\n", s)
        self.assertIn("2\n00:00:02,000 --> 00:00:03,000\nB: yo", s)

    def test_markdown(self):
        md = tr.to_markdown([{"start": 65, "end": 70, "speaker": "Jon", "text": "hello"}],
                            "a b.mkv", 70, "small", "cuda", when="2026-01-02")
        for want in ("a b.mkv", "2026-01-02", "00:01:10", "small", "cuda",
                     "**[00:01:05] Jon:** hello"):
            self.assertIn(want, md)

    def test_speaker_names(self):
        self.assertEqual(tr.speaker_names("", 1), ["Speaker"])
        self.assertEqual(tr.speaker_names("Jon,Other", 2), ["Jon", "Other"])
        self.assertEqual(tr.speaker_names("Jon", 2), ["Jon", "Speaker 2"])

    def test_oom_detect(self):
        self.assertTrue(tr.is_oom(RuntimeError("CUDA failed with error out of memory")))
        self.assertFalse(tr.is_oom(RuntimeError("bad file")))

    def test_private_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f")
            tr.write_private(p, "x")
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)


class RunShRefusals(unittest.TestCase):
    def run_sh(self, root):
        env = dict(os.environ, MEETINGS_ROOT=root)
        return subprocess.run([RUN, "/nonexistent.mkv", "--client", "c", "--title", "t"],
                              env=env, capture_output=True, text=True)

    def test_refuses_git_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            r = self.run_sh(os.path.join(d, "meetings"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("git work tree", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "meetings", "c")))

    def test_refuses_repos(self):
        r = self.run_sh(os.path.expanduser("~/repos/zz-fake-meetings"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("~/repos", r.stderr)
        self.assertFalse(os.path.exists(os.path.expanduser("~/repos/zz-fake-meetings")))

    def test_plain_dir_passes_to_input_check(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_sh(os.path.join(d, "m"))
            self.assertIn("input not found", r.stderr)

    def test_download_needs_name(self):
        r = subprocess.run([RUN, "--download-model"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


@unittest.skipUnless(os.environ.get("MEETING_SMOKE") == "1", "set MEETING_SMOKE=1")
class Smoke(unittest.TestCase):
    def test_two_track_end_to_end(self):
        home = os.path.expanduser("~")
        work = tempfile.mkdtemp(prefix="mt-smoke-", dir=os.path.join(home, ".cache"))
        try:
            script = (
                "set -e; cd /w; "
                "espeak-ng -s 140 -w a.wav 'Let us review the budget for next quarter.'; "
                "espeak-ng -s 140 -w b.wav 'I agree, we should hire two more engineers.'; "
                "ffmpeg -v error -y -i a.wav -i b.wav -filter_complex "
                "'[0:a]apad=pad_dur=12[x];[1:a]adelay=6000|6000[y]' "
                "-map '[x]' -map '[y]' -c:a aac -ar 16000 t.mkv"
            )
            subprocess.run(["docker", "run", "--rm", "-v", work + ":/w", "debian:bookworm-slim",
                            "bash", "-c", "apt-get update -qq >/dev/null && "
                            "apt-get install -y -qq espeak-ng ffmpeg >/dev/null && " + script],
                           check=True)
            subprocess.run([RUN, "--download-model", os.environ.get("MEETING_SMOKE_MODEL", "small")], check=True)
            r = subprocess.run([RUN, os.path.join(work, "t.mkv"), "--client", "test",
                                "--title", "smoke", "--speakers", "Jon,Other", "--model", os.environ.get("MEETING_SMOKE_MODEL", "small")],
                               capture_output=True, text=True)
            print(r.stdout, r.stderr)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = subprocess.run("ls -d ~/meetings/test/*-smoke", shell=True,
                                 capture_output=True, text=True).stdout.strip()
            md = open(os.path.join(out, "transcript.md")).read()
            print(md)
            low = md.lower()
            self.assertLess(low.index("jon:"), low.index("other:"))
            self.assertIn("budget", low)
            self.assertIn("engineers", low)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(os.path.expanduser("~/meetings/test"), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
