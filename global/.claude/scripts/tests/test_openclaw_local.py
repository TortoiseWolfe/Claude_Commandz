"""openclaw_local.py: the local model is the LAST fallback for both agents, the tools stay as they were, and
nothing it runs can create or write a session under agent main (Jonathan's one chat, "Main session")."""

import copy
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "openclaw_local.py"
spec = importlib.util.spec_from_file_location("openclaw_local", SCRIPT)
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

GEMINI = ["google/gemini-3.5-flash", "google/gemini-3.5-flash-lite", "google/gemini-3.6-flash"]


def base_config():
    chain = {"primary": GEMINI[0], "fallbacks": GEMINI[1:]}
    pol = {"allow": list(GEMINI)}
    return {
        "agents": {
            "defaults": {"model": copy.deepcopy(chain), "models": {m: {} for m in GEMINI}, "modelPolicy": copy.deepcopy(pol)},
            "entries": {
                "main": {"workspace": "/s/workspace"},
                "receptionist": {"model": copy.deepcopy(chain), "modelPolicy": copy.deepcopy(pol),
                                 "tools": {"allow": [], "profile": "minimal", "deny": ["gateway"]}},
            },
        },
        "models": {"providers": {}},
        "bindings": [{"type": "route", "agentId": "receptionist", "match": {"channel": "slack", "accountId": "*"}}],
    }


def merge(base, patch):
    """RFC 7396 merge patch, which is what `openclaw config patch` applies."""
    if not isinstance(patch, dict):
        return patch
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge(out.get(k), v)
    return out


class Rules(unittest.TestCase):
    """Rules 1 and 2: never main, never a session under main."""

    def test_agent_main_is_refused(self):
        for agent in ("main", "MAIN", " main "):
            with self.assertRaises(L.Refused):
                L.guard_target(agent)

    def test_no_agent_is_refused_because_the_cli_would_route_to_main(self):
        with self.assertRaises(L.Refused):
            L.guard_target(None)

    def test_session_keys_under_main_are_refused(self):
        for key in ("agent:main:main", "agent:main:explicit:hub-test", "agent:Main:dashboard:abc"):
            with self.assertRaises(L.Refused):
                L.guard_target("hubtest", key)

    def test_session_key_of_another_agent_is_refused(self):
        with self.assertRaises(L.Refused):
            L.guard_target("hubtest", "agent:receptionist:x")

    def test_probe_targets_the_test_agent_by_default(self):
        cmd = L.probe_command(L.TEST_AGENT, "qwen2.5:1.5b-instruct", "hi")
        self.assertEqual(cmd[cmd.index("--agent") + 1], "hubtest")
        self.assertNotIn("--session-key", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "ollama/qwen2.5:1.5b-instruct")

    def test_probe_uses_a_fresh_session_under_the_test_agent(self):
        seen = []
        orig_run, orig_cfg, orig_sync = L.run, L.config_path, L.sync_test_workspace
        L.run = lambda cmd, **k: seen.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        L.config_path, L.sync_test_workspace = lambda: "/s/openclaw.json", lambda d, r=False: d
        try:
            with redirect_stdout(io.StringIO()):
                L.main(["probe", "--model", "x", "--message", "hi"])
        finally:
            L.run, L.config_path, L.sync_test_workspace = orig_run, orig_cfg, orig_sync
        cmd = seen[0]
        key = cmd[cmd.index("--session-key") + 1]
        self.assertTrue(key.startswith("agent:hubtest:probe-"))
        self.assertEqual(cmd[cmd.index("--agent") + 1], "hubtest")

    def test_probe_main_never_reaches_the_cli(self):
        calls = []
        orig = L.run
        L.run = lambda *a, **k: calls.append(a) or None
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = L.main(["probe", "--agent", "main", "--model", "x", "--message", "hi"])
        finally:
            L.run = orig
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])
        self.assertIn("refused", buf.getvalue())

    def test_script_text_never_names_a_main_session_key(self):
        text = SCRIPT.read_text()
        # the only mentions of the main session are the guard's strings and its docs
        for line in text.splitlines():
            if "--session-key" in line or "session_key=" in line:
                self.assertNotIn("agent:main", line)


class Workspace(unittest.TestCase):
    def test_reminder_goes_only_into_the_test_agents_copy(self):
        import tempfile, os
        st = tempfile.mkdtemp()
        os.makedirs(os.path.join(st, "workspace"))
        main_agents = os.path.join(st, "workspace", "MEMORY.md")
        Path(main_agents).write_text("rules\n")
        dst = L.sync_test_workspace(st, reminder=True)
        self.assertEqual(Path(main_agents).read_text(), "rules\n")
        self.assertIn("FINAL REMINDER", Path(dst, "MEMORY.md").read_text())
        self.assertTrue(dst.endswith("workspace-hubtest"))


class Patch(unittest.TestCase):
    def patched(self, model="qwen2.5:1.5b-instruct"):
        cfg = base_config()
        return cfg, merge(cfg, L.build_patch(cfg, model, "/s"))

    def test_local_model_is_last_for_both_agents(self):
        _, new = self.patched()
        ref = "ollama/qwen2.5:1.5b-instruct"
        d = new["agents"]["defaults"]["model"]
        r = new["agents"]["entries"]["receptionist"]["model"]
        self.assertEqual([d["primary"]] + d["fallbacks"], GEMINI + [ref])
        self.assertEqual([r["primary"]] + r["fallbacks"], GEMINI + [ref])

    def test_apply_twice_changes_nothing(self):
        cfg, once = self.patched()
        twice = merge(once, L.build_patch(once, "qwen2.5:1.5b-instruct", "/s"))
        self.assertEqual(once, twice)

    def test_changing_the_model_replaces_the_tail_not_duplicates_it(self):
        _, once = self.patched("qwen2.5:1.5b-instruct")
        fb = merge(once, L.build_patch(once, "llama3.2:3b", "/s"))["agents"]["defaults"]["model"]["fallbacks"]
        self.assertEqual(fb[-1], "ollama/llama3.2:3b")
        self.assertEqual(fb.count("ollama/llama3.2:3b"), 1)

    def test_receptionist_tools_and_main_are_untouched(self):
        cfg, new = self.patched()
        self.assertEqual(new["agents"]["entries"]["receptionist"]["tools"], cfg["agents"]["entries"]["receptionist"]["tools"])
        self.assertEqual(new["agents"]["entries"]["main"], cfg["agents"]["entries"]["main"])
        self.assertEqual(new["bindings"], cfg["bindings"])

    def test_test_agent_has_no_tools_no_bindings_and_same_models(self):
        _, new = self.patched()
        t = new["agents"]["entries"]["hubtest"]
        self.assertEqual(t["tools"]["allow"], [])
        self.assertEqual(t["model"], new["agents"]["defaults"]["model"])
        self.assertEqual([b for b in new["bindings"] if b.get("agentId") == "hubtest"], [])
        self.assertEqual(t["workspace"], "/s/workspace-hubtest")

    def test_model_is_on_both_allowlists_and_provider_is_loopback_without_v1(self):
        _, new = self.patched()
        ref = "ollama/qwen2.5:1.5b-instruct"
        self.assertIn(ref, new["agents"]["defaults"]["modelPolicy"]["allow"])
        self.assertIn(ref, new["agents"]["entries"]["receptionist"]["modelPolicy"]["allow"])
        p = new["models"]["providers"]["ollama"]
        self.assertEqual(p["baseUrl"], "http://127.0.0.1:11434")
        self.assertEqual(p["api"], "ollama")
        self.assertEqual(list(new["models"]["providers"]), ["ollama", "google"])
        self.assertEqual(set(new["models"]["providers"]["google"]), {"models"})  # context caps only: no key, no URL
        self.assertEqual({m["contextTokens"] for m in new["models"]["providers"]["google"]["models"]}, {L.GEMINI_CAP})
        self.assertEqual(p["models"][0]["contextTokens"], 32768)

    def test_compaction_summarises_on_gemini_never_the_local_model(self):
        _, new = self.patched()
        c = new["agents"]["defaults"]["compaction"]
        self.assertTrue(c["model"].startswith("google/"))
        self.assertEqual(c["mode"], "safeguard")
        self.assertLess(L.GEMINI_CAP, L.CONTEXT_WINDOW)  # a compacted chat must fit the local window

    def test_no_secret_shaped_value_is_written(self):
        _, new = self.patched()
        self.assertEqual(new["models"]["providers"]["ollama"]["apiKey"], "ollama-local")

    def test_verify_passes_on_the_patched_config(self):
        _, new = self.patched()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(L.verify("qwen2.5:1.5b-instruct", cfg=new, probe_net=False), 0)

    def test_without_the_gate_agent_the_patch_and_verify_still_work(self):
        cfg = base_config()
        del cfg["agents"]["entries"]["receptionist"]
        cfg["bindings"] = []
        cfg["agents"]["entries"]["main"]["name"] = "Receptionist"
        new = merge(cfg, L.build_patch(cfg, "qwen2.5:1.5b-instruct", "/s"))
        self.assertNotIn("receptionist", new["agents"]["entries"])
        self.assertEqual(new["agents"]["entries"]["main"], cfg["agents"]["entries"]["main"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(L.verify("qwen2.5:1.5b-instruct", cfg=new, probe_net=False), 0)

    def test_verify_fails_when_the_local_model_is_not_last(self):
        _, new = self.patched()
        new["agents"]["defaults"]["model"]["fallbacks"].append("google/gemini-x")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(L.verify("qwen2.5:1.5b-instruct", cfg=new, probe_net=False), 1)

    def test_verify_fails_when_a_tool_is_given_to_the_receptionist(self):
        _, new = self.patched()
        new["agents"]["entries"]["receptionist"]["tools"]["allow"] = ["read"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(L.verify("qwen2.5:1.5b-instruct", cfg=new, probe_net=False), 1)

    def test_verify_fails_when_a_paid_provider_appears(self):
        _, new = self.patched()
        new["models"]["providers"]["anthropic"] = {"baseUrl": "https://example.invalid"}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(L.verify("qwen2.5:1.5b-instruct", cfg=new, probe_net=False), 1)


if __name__ == "__main__":
    unittest.main()
