"""End-to-end smoke test of meeting_transcribe: a real two-track recording (espeak voices), the real container on the GPU.

Not collected by the default suite (the name has no test_ prefix). Run it with run_meeting_smoke.sh, which writes the stamp that
test_meeting_smoke_stamp.py checks. Every docker job here goes through docker-heavy.sh (one heavy job at a time, memory and CPU
caps, a timeout); run.sh does the same for its own docker calls through DOCKER_HEAVY.
Its transcript goes to a throwaway MEETINGS_ROOT under ~/.cache, never ~/meetings.
"""
import glob
import os
import shutil
import subprocess
import unittest
from tmpdirs import fresh_dir  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
RUN = os.path.join(SCRIPTS, "meeting_transcribe", "run.sh")
HEAVY = os.path.join(SCRIPTS, "docker-heavy.sh")
MODEL = os.environ.get("MEETING_SMOKE_MODEL", "small")


class Smoke(unittest.TestCase):
    def test_two_track_end_to_end(self):
        home = os.path.expanduser("~")
        work = fresh_dir(self, prefix="mt-smoke-", dir=os.path.join(home, ".cache"))
        root = fresh_dir(self, prefix="mt-smoke-out-", dir=os.path.join(home, ".cache"))
        env = dict(os.environ, MEETINGS_ROOT=root, DOCKER_HEAVY=HEAVY, DOCKER_HEAVY_ARGS="--timeout 900")
        try:
            script = (
                "set -e; cd /w; "
                "espeak-ng -s 140 -w a.wav 'Let us review the budget for next quarter.'; "
                "espeak-ng -s 140 -w b.wav 'I agree, we should hire two more engineers.'; "
                "ffmpeg -v error -y -i a.wav -i b.wav -filter_complex "
                "'[0:a]apad=pad_dur=12[x];[1:a]adelay=6000|6000[y]' "
                "-map '[x]' -map '[y]' -c:a aac -ar 16000 t.mkv"
            )
            subprocess.run([HEAVY, "--timeout", "900", "--", "docker", "run", "--rm", "-v", work + ":/w", "debian:bookworm-slim",
                            "bash", "-c", "apt-get update -qq >/dev/null && "
                            "apt-get install -y -qq espeak-ng ffmpeg >/dev/null && " + script],
                           check=True)
            subprocess.run([RUN, "--download-model", MODEL], check=True, env=env)
            r = subprocess.run([RUN, os.path.join(work, "t.mkv"), "--client", "test",
                                "--title", "smoke", "--speakers", "Jon,Other", "--model", MODEL],
                               capture_output=True, text=True, env=env)
            print(r.stdout, r.stderr)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = glob.glob(os.path.join(root, "test", "*-smoke"))
            self.assertEqual(len(out), 1, out)
            md = open(os.path.join(out[0], "transcript.md")).read()
            print(md)
            low = md.lower()
            self.assertLess(low.index("jon:"), low.index("other:"))
            self.assertIn("budget", low)
            self.assertIn("engineers", low)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
