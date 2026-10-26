"""demo_video.py: the spec parser, the audio cache, the docker command, the caption files, the ffmpeg arguments and the checks on the result.

Nothing here starts Docker or ffmpeg: every external call goes through an injected runner that records its arguments.
"""
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo_video as dv  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

TARGET = {"text": "Hello", "exact": True}


def good_spec(**over):
    spec = {
        "name": "demo1",
        "baseUrl": "http://localhost:3000",
        "steps": [
            {"say": "This is the home page.", "actions": [{"do": "highlight", "target": TARGET, "ms": 1000}]},
            {"say": "Now the second screen.   It has  extra spaces.", "caption": "Second screen", "actions": []},
        ],
    }
    spec.update(over)
    return spec


def with_action(action, **over):
    return good_spec(steps=[{"say": "A line.", "actions": [action]}], **over)


class FakeRunner:
    """Records every call; `reply` makes the result of a call (a CompletedProcess-like object)."""

    def __init__(self, reply=None, on_call=None):
        self.calls = []
        self.reply = reply or (lambda cmd, kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
        self.on_call = on_call

    def __call__(self, cmd, timeout, **kw):
        self.calls.append((list(cmd), timeout, kw))
        if self.on_call:
            self.on_call(cmd, kw)
        return self.reply(cmd, kw)


class ValidateSpec(unittest.TestCase):
    def errors(self, spec):
        return dv.validate_spec(spec)

    def has(self, spec, text):
        errs = self.errors(spec)
        self.assertTrue(any(text in e for e in errs), "%r not in %r" % (text, errs))

    def test_good_spec_has_no_errors(self):
        self.assertEqual(self.errors(good_spec()), [])

    def test_not_an_object(self):
        self.assertEqual(dv.validate_spec([]), ["the spec must be a JSON object"])

    def test_name_rules(self):
        for bad in ("", "has space", "../x", "-lead", None, 5):
            self.has(good_spec(name=bad), "name must be")
        self.assertEqual(self.errors(good_spec(name="719")), [])

    def test_base_url(self):
        for bad in ("localhost:3000", "ftp://x", "http://x/path", None):
            self.has(good_spec(baseUrl=bad), "baseUrl")
        self.assertEqual(self.errors(good_spec(baseUrl="https://example.com/")), [])

    def test_viewport_gap_voice_title(self):
        self.has(good_spec(viewport={"width": 100, "height": 800}), "viewport")
        self.has(good_spec(viewport={"width": 1280}), "viewport")
        self.has(good_spec(gap=-1), "gap")
        self.has(good_spec(gap=True), "gap")
        self.has(good_spec(voice="lessac"), "voice")
        self.has(good_spec(title=5), "title")
        self.assertEqual(self.errors(good_spec(voice="en_GB-alan-low", gap=0, title="t")), [])

    def test_runner_block(self):
        self.has(good_spec(runner=[]), "runner must be an object")
        self.has(good_spec(runner={"bogus": 1}), "unknown key")
        self.has(good_spec(runner={"user": "bob"}), "host or root")
        self.has(good_spec(runner={"mounts": {}}), "must be a list")
        self.has(good_spec(runner={"mounts": [{"host": "x", "container": "rel"}]}), "absolute container path")
        self.has(good_spec(runner={"env": {"A": 1}}), "runner.env")
        self.has(good_spec(runner={"nodePath": "x"}), "nodePath")
        self.has(good_spec(runner={"timeoutSec": 5}), "timeoutSec")
        self.has(good_spec(runner={"network": "bad net"}), "runner.network")
        ok = {"network": "container:app-1", "user": "root", "mounts": [{"host": "~/x", "container": "/x"}], "env": {"A": "b"},
              "nodePath": ["/x/node_modules"], "timeoutSec": 300, "image": "img", "workdir": "/x", "heavy": False}
        self.assertEqual(self.errors(good_spec(runner=ok)), [])
        for net in ("host", "bridge", "container:name", "my-net"):
            self.assertEqual(self.errors(good_spec(runner={"network": net})), [])

    def test_hook_modules(self):
        self.has(good_spec(setup={}), "needs a module path")
        self.has(good_spec(setup={"module": "/abs/h.cjs"}), "inside the spec's folder")
        self.has(good_spec(setup={"module": "../h.cjs"}), "inside the spec's folder")
        self.has(good_spec(setup={"module": "h.cjs", "export": "not a name"}), "export")
        self.has(good_spec(setup={"module": "a.cjs"}, cleanup={"module": "b.cjs"}), "same file")
        self.assertEqual(self.errors(good_spec(setup={"module": "h.cjs"}, cleanup={"module": "h.cjs"})), [])

    def test_start_block(self):
        self.has(good_spec(start=[]), "start may hold")
        self.has(good_spec(start={"other": 1}), "start may hold")
        self.has(good_spec(start={"path": "x"}), "start.path")
        self.has(good_spec(start={"settleMs": -1}), "settleMs")
        self.has(good_spec(start={"waitFor": {}}), "start.waitFor")
        self.assertEqual(self.errors(good_spec(start={"path": "/a", "waitFor": TARGET, "settleMs": 0})), [])

    def test_steps(self):
        self.has(good_spec(steps=[]), "at least one step")
        self.has(good_spec(steps="x"), "at least one step")
        self.has(good_spec(steps=["x"]), "must be an object")
        self.has(good_spec(steps=[{"say": ""}]), "say")
        self.has(good_spec(steps=[{"say": "x" * 601}]), "limit 600")
        self.has(good_spec(steps=[{"say": "a", "caption": ""}]), "caption")
        self.has(good_spec(steps=[{"say": "a", "extra": 1}]), "unknown key")
        self.has(good_spec(steps=[{"say": "a", "actions": "x"}]), "actions must be a list")
        self.has(good_spec(steps=[{"say": "Narration: AI voice."}]), "label is added for you")

    def test_action_shape(self):
        self.has(with_action("x"), "must be an object")
        self.has(with_action({}), "exactly one of do, check")
        self.has(with_action({"do": "goto", "check": "url"}), "exactly one of do, check")
        self.has(with_action({"do": "dance"}), "unknown do")
        self.has(with_action({"check": "feel"}), "unknown check")
        self.has(with_action({"do": "click"}), "needs 'target'")

    def test_targets(self):
        for bad, text in (("x", "must be an object"), ({}, "exactly one of"), ({"css": "a", "text": "b"}, "exactly one of"),
                          ({"css": ""}, "non-empty"), ({"css": "a", "zzz": 1}, "unknown target key"),
                          ({"text": 5}, "string or {regex"), ({"text": {"regex": "("}}, "string or {regex"),
                          ({"text": {"regex": "a", "flags": "x"}}, "string or {regex"), ({"css": "a", "name": "n"}, "name only goes with role"),
                          ({"role": "button", "name": 5}, "name must be"), ({"role": 5}, "role must be"),
                          ({"css": "a", "exact": "yes"}, "exact must be"), ({"css": "a", "nth": -1}, "nth"),
                          ({"css": "a", "nth": True}, "nth"), ({"css": "a", "within": ""}, "within")):
            self.has(with_action({"do": "click", "target": bad}), text)
        for ok in ({"css": "#a"}, {"text": {"regex": "a+", "flags": "i"}}, {"role": "button", "name": "Go", "exact": True, "nth": 2},
                   {"label": "Email"}, {"placeholder": "x"}, {"testId": "t", "within": "#box"}):
            self.assertEqual(self.errors(with_action({"do": "click", "target": ok})), [], ok)

    def test_do_actions(self):
        self.has(with_action({"do": "goto", "path": "nope"}), "goto path")
        self.has(with_action({"do": "scroll", "to": "middle"}), "scroll to")
        self.has(with_action({"do": "scroll_to", "target": TARGET, "block": "x"}), "block must be")
        self.has(with_action({"do": "wait", "ms": -5}), "numbers from 0")
        self.has(with_action({"do": "highlight", "target": TARGET, "ms": "x"}), "numbers from 0")
        self.has(with_action({"do": "fill", "target": TARGET, "value": 5}), "fill value")
        self.has(with_action({"do": "press", "key": ""}), "press key")
        self.has(with_action({"do": "wait_for", "target": TARGET, "state": "gone"}), "state must be")
        self.has(with_action({"do": "hook", "name": "x"}), "needs setup.module")
        self.has(with_action({"do": "hook", "name": "1x"}, setup={"module": "h.cjs"}), "function name")
        for ok in ({"do": "goto", "path": "/x"}, {"do": "goto", "path": "https://x.test/"}, {"do": "scroll", "to": "top"},
                   {"do": "scroll_to", "target": TARGET}, {"do": "wait", "ms": 10}, {"do": "press", "key": "Enter"},
                   {"do": "fill", "target": TARGET, "value": "v"}, {"do": "wait_for", "target": TARGET, "state": "hidden"}):
            self.assertEqual(self.errors(with_action(ok)), [], ok)
        self.assertEqual(self.errors(with_action({"do": "hook", "name": "prep"}, setup={"module": "h.cjs"})), [])

    def test_checks(self):
        self.has(with_action({"check": "count", "target": TARGET}), "equals or atLeast")
        self.has(with_action({"check": "order", "within": "", "texts": ["a", "b"]}), "order within")
        self.has(with_action({"check": "order", "within": "#a", "texts": ["a"]}), "2 or more")
        self.has(with_action({"check": "text", "target": TARGET, "contains": ""}), "contains")
        self.has(with_action({"check": "url", "matches": "("}), "regular expression")
        self.has(with_action({"check": "visible"}), "needs 'target'")
        for ok in ({"check": "visible", "target": TARGET}, {"check": "absent", "target": TARGET},
                   {"check": "count", "target": TARGET, "equals": 0}, {"check": "count", "target": TARGET, "atLeast": 2},
                   {"check": "order", "within": "#a", "texts": ["a", "b"]}, {"check": "text", "target": TARGET, "contains": "x"},
                   {"check": "url", "matches": "/quests$"}):
            self.assertEqual(self.errors(with_action(ok)), [], ok)


class LoadAndNormalize(unittest.TestCase):
    def write(self, spec, name="s.json"):
        d = Path(fresh_dir(self))
        p = d / name
        p.write_text(spec if isinstance(spec, str) else json.dumps(spec))
        return p

    def test_load_good(self):
        self.assertEqual(dv.load_spec(self.write(good_spec()))["name"], "demo1")

    def test_load_unreadable_and_bad_json(self):
        with self.assertRaises(dv.SpecError) as c:
            dv.load_spec(Path(fresh_dir(self)) / "missing.json")
        self.assertIn("cannot read", c.exception.errors[0])
        with self.assertRaises(dv.SpecError):
            dv.load_spec(self.write("{not json"))

    def test_load_invalid_lists_every_error(self):
        with self.assertRaises(dv.SpecError) as c:
            dv.load_spec(self.write({"name": "", "baseUrl": "x", "steps": []}))
        self.assertGreaterEqual(len(c.exception.errors), 3)

    def test_label_step_is_first_and_text_is_tidied(self):
        n = dv.normalize(good_spec(), Path("/s"))
        self.assertEqual(n["steps"][0], {"say": dv.LABEL, "caption": dv.LABEL, "actions": []})
        self.assertEqual(n["steps"][2]["say"], "Now the second screen. It has extra spaces.")
        self.assertEqual(n["steps"][2]["caption"], "Second screen")
        self.assertEqual(n["steps"][1]["caption"], "This is the home page.")
        self.assertEqual(len(n["steps"]), 3)

    def test_defaults_and_hook_paths(self):
        n = dv.normalize(good_spec(baseUrl="http://x:1/", setup={"module": "sub/h.cjs"}, cleanup={"module": "sub/h.cjs", "export": "bye"}), Path("/s"))
        self.assertEqual(n["baseUrl"], "http://x:1")
        self.assertEqual((n["gap"], n["voice"], n["viewport"]), (0.7, "en_US-lessac-medium", {"width": 1280, "height": 800}))
        self.assertEqual(n["setup"], {"module": "/spec/sub/h.cjs", "export": "setup"})
        self.assertEqual(n["cleanup"], {"module": "/spec/sub/h.cjs", "export": "bye"})

    def test_the_spec_is_not_changed(self):
        spec = good_spec()
        before = json.dumps(spec)
        dv.normalize(spec, Path("/s"))
        self.assertEqual(json.dumps(spec), before)


class Audio(unittest.TestCase):
    def setUp(self):
        self.out = Path(fresh_dir(self))
        self.norm = dv.normalize(good_spec(), Path("/s"))

    def runner(self):
        def on_call(cmd, kw):
            wav = [a for a in cmd if a.startswith("/out/")][0].split("/")[-1]
            (self.out / wav).write_bytes(b"RIFF")
        return FakeRunner(on_call=on_call)

    def test_one_piper_call_per_line_with_limits(self):
        r = self.runner()
        lines, made = dv.make_audio(self.norm, self.out, r, probe=lambda p: 1.5)
        self.assertEqual((len(lines), made, len(r.calls)), (3, 3, 3))
        cmd, timeout, kw = r.calls[0]
        self.assertEqual(cmd[:3], ["timeout", "120", "docker"])
        self.assertIn("--cpus", cmd)
        self.assertIn("--memory", cmd)
        self.assertEqual(kw["input"], dv.LABEL.encode())
        self.assertIn("/voices/en_US-lessac-medium.onnx", cmd)
        self.assertGreater(timeout, 120)
        meta = json.loads((self.out / "demo1-audio.json").read_text())
        self.assertEqual([l["wav"] for l in meta["lines"]], ["demo1-0.wav", "demo1-1.wav", "demo1-2.wav"])
        self.assertEqual(meta["lines"][0]["dur"], 1.5)

    def test_unchanged_lines_keep_their_wav(self):
        dv.make_audio(self.norm, self.out, self.runner(), probe=lambda p: 1.0)
        r = self.runner()
        lines, made = dv.make_audio(self.norm, self.out, r, probe=lambda p: 1.0)
        self.assertEqual((made, len(r.calls)), (0, 0))
        self.assertEqual(len(lines), 3)

    def test_only_the_changed_line_is_voiced_again(self):
        dv.make_audio(self.norm, self.out, self.runner(), probe=lambda p: 1.0)
        changed = dv.normalize(good_spec(steps=[good_spec()["steps"][0], {"say": "A different second line."}]), Path("/s"))
        r = self.runner()
        _, made = dv.make_audio(changed, self.out, r, probe=lambda p: 1.0)
        self.assertEqual(made, 1)
        self.assertEqual(r.calls[0][2]["input"], b"A different second line.")

    def test_a_new_voice_voices_everything_and_force_does_too(self):
        dv.make_audio(self.norm, self.out, self.runner(), probe=lambda p: 1.0)
        other = dv.normalize(good_spec(voice="en_US-amy-medium"), Path("/s"))
        self.assertEqual(dv.make_audio(other, self.out, self.runner(), probe=lambda p: 1.0)[1], 3)
        self.assertEqual(dv.make_audio(other, self.out, self.runner(), force=True, probe=lambda p: 1.0)[1], 3)

    def test_a_missing_wav_or_broken_metadata_is_made_again(self):
        dv.make_audio(self.norm, self.out, self.runner(), probe=lambda p: 1.0)
        (self.out / "demo1-1.wav").unlink()
        self.assertEqual(dv.make_audio(self.norm, self.out, self.runner(), probe=lambda p: 1.0)[1], 1)
        (self.out / "demo1-audio.json").write_text("{broken")
        self.assertEqual(dv.make_audio(self.norm, self.out, self.runner(), probe=lambda p: 1.0)[1], 3)

    def test_line_hash_depends_on_voice_and_text(self):
        self.assertNotEqual(dv.line_hash("a", "v1"), dv.line_hash("a", "v2"))
        self.assertNotEqual(dv.line_hash("a", "v1"), dv.line_hash("b", "v1"))
        self.assertEqual(dv.line_hash("a", "v1"), dv.line_hash("a", "v1"))

    def test_probe_duration_reads_ffprobe(self):
        r = FakeRunner(reply=lambda c, k: SimpleNamespace(stdout="3.25\n"))
        self.assertEqual(dv.probe_duration("x.wav", r), 3.25)
        self.assertEqual(r.calls[0][0][0], "ffprobe")


class DockerRecordCommand(unittest.TestCase):
    def cmd(self, runner=None, **kw):
        raw = good_spec(runner=runner or {})
        norm = dv.normalize(raw, Path("/s"))
        return dv.docker_record_cmd(raw, norm, "/o", "/s", 1000, 1001, **kw)

    def pairs(self, cmd, flag):
        return [cmd[i + 1] for i, a in enumerate(cmd) if a == flag]

    def test_default_runs_as_the_host_user_with_limits(self):
        c = self.cmd()
        self.assertEqual(c[:3], ["docker", "run", "--rm"])
        self.assertEqual(self.pairs(c, "--user"), ["1000:1001"])
        self.assertIn("HOME=/tmp", self.pairs(c, "-e"))
        self.assertIn("--cpus", c)
        self.assertIn("--memory", c)
        self.assertEqual(c[-3:], ["node", "/dv/demo_video_record.cjs", "/out/demo1.run.json"])
        self.assertEqual(c[-4], dv.PLAYWRIGHT_IMAGE)

    def test_standard_mounts(self):
        v = self.pairs(self.cmd(), "-v")
        self.assertIn("/o:/out", v)
        self.assertIn("/s:/spec:ro", v)
        self.assertIn("%s:/dv:ro" % dv.HERE, v)
        self.assertIn("%s:/dvmods:ro" % dv.CACHE, v)

    def test_root_runner_chowns_the_outputs_back(self):
        c = self.cmd({"user": "root"})
        self.assertNotIn("--user", c)
        self.assertIn("DV_CHOWN=1000:1001", self.pairs(c, "-e"))

    def test_network_mounts_env_node_path_workdir_image(self):
        c = self.cmd({"network": "container:app-1", "mounts": [{"host": "/h/app", "container": "/app", "ro": False}, {"host": "/h/v", "container": "/v"}],
                      "env": {"B": "2", "A": "1"}, "nodePath": ["/app/node_modules"], "workdir": "/app", "image": "my/img:1"})
        self.assertEqual(self.pairs(c, "--network"), ["container:app-1"])
        v = self.pairs(c, "-v")
        self.assertIn("/h/app:/app", v)
        self.assertIn("/h/v:/v:ro", v)
        e = self.pairs(c, "-e")
        self.assertLess(e.index("A=1"), e.index("B=2"))
        self.assertIn("NODE_PATH=/dvmods/node_modules:/app/node_modules", e)
        self.assertEqual(self.pairs(c, "-w"), ["/app"])
        self.assertEqual(c[-4], "my/img:1")

    def test_heavy_goes_through_docker_heavy_without_its_own_limits(self):
        c = self.cmd({"timeoutSec": 400}, heavy=True, docker_heavy="/x/docker-heavy.sh")
        self.assertEqual(c[:4], ["/x/docker-heavy.sh", "--timeout", "400", "--"])
        self.assertEqual(c[4:7], ["docker", "run", "--rm"])
        self.assertNotIn("--cpus", c)
        self.assertEqual(self.cmd({"heavy": True}, docker_heavy="/x/h.sh")[0], "/x/h.sh")


class Captions(unittest.TestCase):
    TL = {"steps": [{"start": 0, "dur": 1.8}, {"start": 2.5, "dur": 3.65}], "total": 8}

    def test_times(self):
        self.assertEqual(dv.ass_time(0), "0:00:00.00")
        self.assertEqual(dv.ass_time(3725.5), "1:02:05.50")
        self.assertEqual(dv.srt_time(0), "00:00:00,000")
        self.assertEqual(dv.srt_time(3725.5), "01:02:05,500")
        self.assertEqual(dv.srt_time(59.9996), "00:01:00,000")

    def test_windows_hold_each_caption_half_a_second_past_its_voice(self):
        self.assertEqual(dv.caption_windows(self.TL), [(0, 2.3), (2.5, 2.5 + 3.65 + 0.5)])

    def test_ass_has_the_label_text_in_order_and_escapes(self):
        ass = dv.build_ass(self.TL, [dv.LABEL, "two {x}\\y\nz"], (1280, 800))
        self.assertIn("PlayResX: 1280", ass)
        self.assertIn("Style: Cap,DejaVu Sans", ass)
        d = [l for l in ass.splitlines() if l.startswith("Dialogue")]
        self.assertEqual(len(d), 2)
        self.assertTrue(d[0].endswith(",,Narration: AI voice."))
        self.assertTrue(d[0].startswith("Dialogue: 0,0:00:00.00,0:00:02.30,Cap"))
        self.assertTrue(d[1].endswith("two (x)/y\\Nz"))

    def test_srt(self):
        srt = dv.build_srt(self.TL, ["one", "two"])
        self.assertEqual(srt, "1\n00:00:00,000 --> 00:00:02,300\none\n\n2\n00:00:02,500 --> 00:00:06,650\ntwo\n\n")


class Sync(unittest.TestCase):
    def frames(self, colours):
        return b"".join(bytes(c) for c in colours)

    def test_markers_found_in_seconds(self):
        grey, red, blue = (90, 90, 90), (255, 0, 0), (0, 0, 255)
        raw = self.frames([grey] * 3 + [red] * 2 + [grey] * 5 + [blue])
        self.assertEqual(dv.find_markers(raw, fps=25), (3 / 25, 10 / 25))

    def test_blue_before_red_does_not_count(self):
        raw = self.frames([(0, 0, 255), (255, 0, 0), (0, 0, 255)])
        self.assertEqual(dv.find_markers(raw, fps=1), (1.0, 2.0))

    def test_missing_markers_raise(self):
        with self.assertRaisesRegex(RuntimeError, "red"):
            dv.find_markers(self.frames([(1, 2, 3)] * 4))
        with self.assertRaisesRegex(RuntimeError, "blue"):
            dv.find_markers(self.frames([(255, 0, 0), (1, 2, 3)]))

    def test_sync_numbers_scale_the_webm_clock(self):
        tl = {"mRed": 0.0, "mBlue": 20.0, "demoOffset": 5.0, "total": 10.0}
        off, tot, b = dv.sync_numbers(1.0, 41.0, tl)
        self.assertEqual((off, tot, b), (11.0, 20.0, 2.0))
        off, tot, b = dv.sync_numbers(0.0, 20.0, tl)
        self.assertEqual((off, tot, b), (5.0, 10.0, 1.0))

    def test_mix_filter_delays_each_line_to_its_step(self):
        tl = {"steps": [{"start": 0}, {"start": 2.5}]}
        f = dv.mix_filter(tl)
        self.assertIn("[1:a]adelay=0:all=1[a0]", f)
        self.assertIn("[2:a]adelay=2500:all=1[a1]", f)
        self.assertTrue(f.endswith("[a0][a1]amix=inputs=2:normalize=0:dropout_transition=0[aout]"))

    def test_contact_times_are_the_middle_of_each_step(self):
        self.assertEqual(dv.contact_times({"steps": [{"start": 1, "dur": 2}, {"start": 4, "dur": 4}]}), [2.0, 6.0])


class Assemble(unittest.TestCase):
    def setUp(self):
        self.out = Path(fresh_dir(self))
        self.norm = dv.normalize(good_spec(), Path("/s"))
        self.lines = [{"wav": "demo1-%d.wav" % i, "dur": 1.0} for i in range(3)]
        self.tl = {"mRed": 0.0, "mBlue": 20.0, "demoOffset": 5.0, "total": 10.0,
                   "steps": [{"start": 0, "dur": 1.0}, {"start": 1.7, "dur": 1.0}, {"start": 3.4, "dur": 1.0}]}

    def test_cuts_mix_and_contact_sheet(self):
        r = FakeRunner()
        info = dv.assemble(self.norm, self.lines, self.tl, self.out, r, markers=(1.0, 41.0))
        self.assertEqual(info["sync"], {"trim": 11.0, "len": 20.0, "speed": 2.0})
        self.assertEqual(info["contactSheet"], "demo1-contact-sheet.png")
        self.assertIn("Narration: AI voice.", (self.out / "demo1.srt").read_text())
        self.assertTrue((self.out / "demo1.ass").exists())
        cap, mix = r.calls[0][0], r.calls[1][0]
        self.assertEqual(cap[cap.index("-ss") + 1], "11.000")
        self.assertEqual(cap[cap.index("-t") + 1], "20.000")
        self.assertIn("setpts=(PTS-STARTPTS)/2.000000,subtitles=demo1.ass,fps=25", cap)
        self.assertIn("-an", cap)
        self.assertEqual(cap[-1], "demo1-captions-only.mp4")
        self.assertEqual(r.calls[0][2]["cwd"], self.out)
        self.assertEqual(mix[-1], "demo1-demo.mp4")
        self.assertEqual([a for a in mix if a.endswith(".wav")], ["demo1-0.wav", "demo1-1.wav", "demo1-2.wav"])
        self.assertIn("[aout]", mix)
        self.assertEqual(mix[mix.index("-c:v") + 1], "copy")
        for cmd, timeout, kw in r.calls:
            self.assertTrue(timeout > 0)

    def test_markers_come_from_the_recording_when_not_given(self):
        raw = bytes((255, 0, 0)) + bytes((0, 0, 255))
        r = FakeRunner(reply=lambda c, k: SimpleNamespace(stdout=raw) if c[0] == "ffmpeg" and "rawvideo" in c else SimpleNamespace(stdout=b""))
        info = dv.assemble(self.norm, self.lines, self.tl, self.out, r)
        self.assertEqual(r.calls[0][0][r.calls[0][0].index("-f") + 1], "rawvideo")
        self.assertAlmostEqual(info["sync"]["speed"], (1 / 25) / 20)

    def test_contact_sheet_columns_follow_the_step_count(self):
        r = FakeRunner()
        dv.contact_sheet("demo1", self.tl, self.out, r)
        tile = r.calls[-1][0][r.calls[-1][0].index("-vf") + 1]
        self.assertTrue(tile.startswith("tile=2x2"))
        many = {"steps": [{"start": i, "dur": 1} for i in range(7)]}
        r2 = FakeRunner()
        dv.contact_sheet("demo1", many, self.out, r2)
        self.assertEqual(len(r2.calls), 8)
        self.assertTrue(r2.calls[-1][0][r2.calls[-1][0].index("-vf") + 1].startswith("tile=3x3"))


class Verify(unittest.TestCase):
    def setUp(self):
        self.out = Path(fresh_dir(self))
        self.norm = dv.normalize(good_spec(), Path("/s"))

    def tl(self, **over):
        tl = {"total": 10.0, "steps": [{"overran": 0}, {"overran": 0}], "checks": [{"step": 1, "ok": True, "msg": "m"}]}
        tl.update(over)
        return tl

    def verify(self, tl, durs=(10.0, 10.0)):
        d = iter(durs)
        return dv.verify(self.norm, tl, self.out, probe=lambda p: next(d))

    def test_clean_run_has_no_warnings(self):
        r = self.verify(self.tl())
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["narrated"], r["captionsOnly"], r["stalledSec"], r["netOfStalls"]), (10.0, 10.0, 0, 10.0))

    def test_length_mismatch_warnings(self):
        w = self.verify(self.tl(), durs=(12.5, 10.0))["warnings"]
        self.assertTrue(any("recorded timeline" in x for x in w))
        self.assertTrue(any("differ by more than" in x for x in w))

    def test_overrun_and_failed_check(self):
        w = self.verify(self.tl(steps=[{"overran": 0}, {"overran": 3.2}], checks=[{"step": 2, "ok": False, "msg": "bad one"}]))["warnings"]
        self.assertTrue(any("step 1" in x and "3.2" in x for x in w))
        self.assertTrue(any("check failed: step 2: bad one" in x for x in w))

    def test_container_stalls_are_reported_and_netted_out(self):
        r = self.verify(self.tl(stalls=[{"at": 1, "sec": 2.5}, {"at": 5, "sec": 2.5}], total=10.0), durs=(10.0, 10.0))
        self.assertEqual((r["stalledSec"], r["netOfStalls"]), (5.0, 5.0))
        self.assertTrue(any("froze 2 time(s) for 5.0 s" in x for x in r["warnings"]))

    def test_a_small_stall_is_not_a_warning(self):
        r = self.verify(self.tl(stalls=[{"at": 1, "sec": 0.9}]))
        self.assertEqual(r["warnings"], [])

    def test_first_caption_must_be_the_label(self):
        self.norm["steps"][0]["caption"] = "something else"
        self.assertTrue(any("first caption" in x for x in self.verify(self.tl())["warnings"]))


class DoctorAndOpen(unittest.TestCase):
    def runner(self, missing=()):
        def reply(cmd, kw):
            hit = any(m in " ".join(cmd) for m in missing)
            return SimpleNamespace(returncode=1 if hit else 0, stdout="subtitles" if "-filters" in cmd else "", stderr="")
        return FakeRunner(reply=reply)

    def setUp(self):
        self.cache = Path(fresh_dir(self))
        self.old = dv.CACHE
        dv.CACHE = self.cache
        self.addCleanup(setattr, dv, "CACHE", self.old)

    def test_all_present(self):
        (self.cache / "node_modules" / "playwright").mkdir(parents=True)
        lines, ok = dv.doctor(runner=self.runner())
        self.assertTrue(ok, lines)
        self.assertFalse(any("MISSING" in l for l in lines if "DejaVu" not in l and "powershell" not in l))

    def test_missing_pieces_are_named_and_nothing_is_installed_without_the_flag(self):
        r = self.runner(missing=("image inspect",))
        lines, ok = dv.doctor(runner=r)
        self.assertFalse(ok)
        self.assertTrue(any("Piper image" in l and "MISSING" in l for l in lines))
        self.assertTrue(any("run: demo_video.py doctor --install" in l for l in lines))
        self.assertFalse(any(c[0][1] in ("build", "pull") for c in r.calls if c[0][0] == "timeout"))

    def test_install_builds_pulls_and_caches_playwright_with_timeouts(self):
        r = self.runner(missing=("image inspect",))

        def installing(cmd, timeout, **kw):
            if "npm" in cmd:
                (self.cache / "node_modules" / "playwright").mkdir(parents=True)
            return r(cmd, timeout, **kw)
        lines, _ = dv.doctor(install=True, runner=installing)
        joined = [" ".join(c[0]) for c in r.calls]
        self.assertTrue(any(j.startswith("timeout 1200 docker build -t %s" % dv.PIPER_IMAGE) for j in joined), joined)
        self.assertTrue(any(j.startswith("timeout 1800 docker pull %s" % dv.PLAYWRIGHT_IMAGE) for j in joined))
        self.assertTrue(any("npm install" in j and "playwright@%s" % dv.PLAYWRIGHT_VERSION in j and j.startswith("timeout 600") for j in joined))
        self.assertTrue(any("installed playwright" in l for l in lines))

    def test_no_docker_skips_the_image_checks(self):
        lines, ok = dv.doctor(runner=self.runner(missing=("docker info",)))
        self.assertFalse(ok)
        self.assertFalse(any("Piper image" in l for l in lines))

    def test_open_uses_powershell_with_a_windows_path(self):
        c = dv.open_cmd("/x/out/a b.mp4", wslpath=r"C:\x\a b'c.mp4", which=lambda n: "/usr/bin/powershell.exe")
        self.assertEqual(c[:3], ["powershell.exe", "-NoProfile", "-Command"])
        self.assertEqual(c[3], r"Start-Process -FilePath 'C:\x\a b''c.mp4'")

    def test_open_falls_back_to_xdg_open(self):
        c = dv.open_cmd("/x/a.mp4", which=lambda n: None)
        self.assertEqual(c, ["xdg-open", str(Path("/x/a.mp4").resolve())])


class RunAll(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        self.spec = self.d / "demo1.spec.json"
        self.say = []

    def write(self, spec):
        self.spec.write_text(json.dumps(spec))

    def test_stage_order_files_and_result(self):
        self.write(good_spec())
        out = self.d / "out"

        def on_call(cmd, kw):
            if cmd[0] == "timeout" and "piper" in cmd:
                wav = [a for a in cmd if a.startswith("/out/")][0].split("/")[-1]
                (out / wav).write_bytes(b"x")
            elif cmd[0] == "timeout" and "docker" in cmd and "node" in cmd:
                (out / "demo1.webm").write_bytes(b"x")
                (out / "demo1-timeline.json").write_text(json.dumps({
                    "mRed": 0, "mBlue": 20, "demoOffset": 5, "total": 10, "steps": [{"start": i * 2, "dur": 1.0} for i in range(3)],
                    "checks": [{"step": 1, "ok": True, "msg": "m"}]}))

        r = FakeRunner(on_call=on_call, reply=lambda c, k: SimpleNamespace(returncode=0, stdout=b"" if c[0] == "ffmpeg" else "1.0\n", stderr=""))
        orig = dv.verify
        dv.verify = lambda norm, tl, o: orig(norm, tl, o, probe=lambda p: 10.0)
        self.addCleanup(setattr, dv, "verify", orig)
        orig_markers = dv.find_markers
        dv.find_markers = lambda raw, fps=25: (0.0, 20.0)
        self.addCleanup(setattr, dv, "find_markers", orig_markers)
        res = dv.run_all(self.spec, out, say=self.say.append, runner=r)
        kinds = []
        for c, _, _ in r.calls:
            kinds.append("piper" if "piper" in c else "record" if "node" in c else c[0])
        self.assertEqual(kinds[:6], ["piper", "ffprobe"] * 3)
        self.assertEqual(kinds[6], "record")
        self.assertTrue(all(k in ("ffprobe", "ffmpeg") for k in kinds[7:]), kinds)
        self.assertTrue((out / "demo1-result.json").exists())
        self.assertEqual(res["warnings"], [])
        self.assertEqual(set(res["files"]), {"narrated", "captionsOnly", "srt", "contactSheet"})
        self.assertTrue(any("1/1 on-screen checks passed" in s for s in self.say))
        stale = json.loads((out / "demo1.run.json").read_text())
        self.assertEqual(stale["durations"], [1.0, 1.0, 1.0])
        self.assertEqual(stale["steps"][0]["caption"], dv.LABEL)

    def test_audio_stage_only_stops_before_recording(self):
        self.write(good_spec())
        out = self.d / "out"
        r = FakeRunner(on_call=lambda cmd, kw: "piper" in cmd and (out / [a for a in cmd if a.startswith("/out/")][0].split("/")[-1]).write_bytes(b"x"),
                       reply=lambda c, k: SimpleNamespace(returncode=0, stdout="2.0\n", stderr=""))
        self.assertEqual(dv.run_all(self.spec, out, stages=("audio",), say=self.say.append, runner=r), {"name": "demo1"})
        self.assertFalse(any("node" in c[0] for c in r.calls))

    def test_hook_module_must_exist_next_to_the_spec(self):
        self.write(good_spec(setup={"module": "hooks.cjs"}))
        with self.assertRaises(dv.SpecError) as c:
            dv.run_all(self.spec, self.d / "out", say=self.say.append, runner=FakeRunner())
        self.assertIn("hooks.cjs", c.exception.errors[0])

    def test_local_url_without_a_network_warns(self):
        self.write(good_spec())
        r = FakeRunner(reply=lambda c, k: SimpleNamespace(returncode=0, stdout="1.0\n", stderr=""),
                       on_call=lambda cmd, kw: "piper" in cmd and (self.d / "out" / [a for a in cmd if a.startswith("/out/")][0].split("/")[-1]).write_bytes(b"x"))
        dv.run_all(self.spec, self.d / "out", stages=("audio",), say=self.say.append, runner=r)
        self.assertTrue(any("runner.network" in s for s in self.say))


class Record(unittest.TestCase):
    def test_a_recorder_that_writes_no_webm_is_an_error(self):
        out = Path(fresh_dir(self))
        raw = good_spec()
        norm = dv.normalize(raw, Path("/s"))
        lines = [{"dur": 1.0}] * 3

        def on_call(cmd, kw):
            (out / "demo1-timeline.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "without a .webm"):
            dv.record(raw, norm, lines, out, "/s", runner=FakeRunner(on_call=on_call))

    def test_stale_outputs_are_removed_before_the_run(self):
        out = Path(fresh_dir(self))
        raw = good_spec()
        norm = dv.normalize(raw, Path("/s"))
        (out / "demo1.webm").write_bytes(b"old")
        (out / "demo1-timeline.json").write_text('{"old": 1}')
        seen = {}

        def on_call(cmd, kw):
            seen["webm"] = (out / "demo1.webm").exists()
            (out / "demo1.webm").write_bytes(b"new")
            (out / "demo1-timeline.json").write_text('{"new": 1}')
        tl = dv.record(raw, norm, [{"dur": 1.0}] * 3, out, "/s", runner=FakeRunner(on_call=on_call))
        self.assertEqual((seen["webm"], tl), (False, {"new": 1}))

    def test_the_container_call_has_a_timeout_wrapper(self):
        out = Path(fresh_dir(self))
        raw = good_spec(runner={"timeoutSec": 300})
        norm = dv.normalize(raw, Path("/s"))

        def on_call(cmd, kw):
            (out / "demo1.webm").write_bytes(b"x")
            (out / "demo1-timeline.json").write_text("{}")
        r = FakeRunner(on_call=on_call)
        dv.record(raw, norm, [{"dur": 1.0}] * 3, out, "/s", runner=r)
        cmd, timeout, _ = r.calls[0]
        self.assertEqual(cmd[:3], ["timeout", "300", "docker"])
        self.assertEqual(timeout, 330)


class Main(unittest.TestCase):
    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = dv.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def spec_file(self, spec):
        p = Path(fresh_dir(self)) / "s.json"
        p.write_text(json.dumps(spec))
        return str(p)

    def test_validate_ok_and_bad(self):
        code, out, _ = self.call("validate", self.spec_file(good_spec()))
        self.assertEqual(code, 0)
        self.assertIn("spec ok: demo1, 3 lines", out)
        code, _, err = self.call("validate", self.spec_file({"name": ""}))
        self.assertEqual(code, 2)
        self.assertIn("spec problems", err)

    def test_run_rejects_unknown_stages_before_doing_anything(self):
        code, _, err = self.call("run", self.spec_file(good_spec()), "--stages", "audio,fly")
        self.assertEqual(code, 2)
        self.assertIn("stages must be", err)

    def test_a_failed_external_command_is_reported_not_raised(self):
        orig = dv.run_all
        self.addCleanup(setattr, dv, "run_all", orig)

        def boom(*a, **k):
            raise subprocess.CalledProcessError(3, ["docker", "run", "x"], stderr=b"no such image")
        dv.run_all = boom
        code, _, err = self.call("run", self.spec_file(good_spec()))
        self.assertEqual(code, 1)
        self.assertIn("no such image", err)

    def test_a_timeout_is_reported(self):
        orig = dv.run_all
        self.addCleanup(setattr, dv, "run_all", orig)

        def slow(*a, **k):
            raise subprocess.TimeoutExpired(["ffmpeg"], 600)
        dv.run_all = slow
        code, _, err = self.call("run", self.spec_file(good_spec()))
        self.assertEqual(code, 1)
        self.assertIn("timed out after 600", err)

    def test_the_label_text_is_the_standard_one(self):
        self.assertEqual(dv.LABEL, "Narration: AI voice.")


if __name__ == "__main__":
    unittest.main()
