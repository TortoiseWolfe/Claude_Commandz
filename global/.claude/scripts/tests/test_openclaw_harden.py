"""openclaw_harden.sh: the gate agent ("receptionist") is created only while an outside channel is configured or a
binding exists; the session cycle settings (daily 4 AM reset, Main named Receptionist, dreaming and skill reviews
off, the hand-off hook enabled) are always in the patch. Runs the script's own functions against a fake openclaw."""

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from tmpdirs import fresh_dir  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "openclaw_harden.sh"

FAKE = r"""#!/usr/bin/env bash
case "$1 $2" in
  "config file") echo "$FAKE_CFG" ;;
  "config schema") echo '{"properties":{"channels":{"properties":{"defaults":{},"modelByChannel":{},"slack":{},"discord":{}}}}}' ;;
  *) exit 9 ;;
esac
"""


def patch_for(cfg, env=None, hook_installed=False):
    d = Path(fresh_dir(None))
    (d / "bin").mkdir()
    f = d / "bin" / "openclaw"
    f.write_text(FAKE)
    f.chmod(f.stat().st_mode | stat.S_IXUSR)
    c = d / "openclaw.json"
    c.write_text(json.dumps(cfg))
    if hook_installed:
        (d / "hooks" / "session-handoff").mkdir(parents=True)
        (d / "hooks" / "session-handoff" / "handler.js").write_text("export default () => {}")
    body = "\n".join(SCRIPT.read_text().splitlines()[:-1])  # everything but the last line, which runs main
    run = f'source <(cat <<\'__H__\'\n{body}\n__H__\n)\nHOME="{d}"; init; write_workspace; patch_json; ls "{d}" >&2'
    e = dict(os.environ, PATH=f"{d}/bin:{os.environ['PATH']}", FAKE_CFG=str(c), HOME=str(d))
    e.update(env or {})
    r = subprocess.run(["bash", "-c", run], capture_output=True, text=True, env=e, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), d, r.stderr


BASE = {"agents": {"defaults": {"model": {"primary": "google/gemini-3.5-flash", "fallbacks": ["google/gemini-3.5-flash-lite", "google/gemini-3.6-flash", "ollama/q"]}}, "entries": {"main": {"workspace": "/w"}}}}
FREE_CHAIN = ["google/gemini-3.5-flash", "google/gemini-3.5-flash-lite", "google/gemini-3.6-flash"]


def merge_patch(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict):
            target[key] = merge_patch(target.get(key, {}), value)
        else:
            target[key] = value
    return target


def no_chain_config():
    cfg = json.loads(json.dumps(BASE))
    defaults = cfg["agents"]["defaults"]
    defaults.pop("model")
    defaults.pop("modelPolicy", None)
    defaults.pop("models", None)
    return cfg


class Harden(unittest.TestCase):
    def test_no_channel_and_no_binding_means_no_gate_agent_and_no_bindings(self):
        p, d, err = patch_for(dict(BASE, channels={}, bindings=[]))
        self.assertNotIn("receptionist", p["agents"]["entries"])
        self.assertNotIn("bindings", p)
        self.assertNotIn("workspace-receptionist", err)

    def test_a_configured_channel_creates_the_gate_with_a_binding_per_channel(self):
        p, d, err = patch_for(dict(BASE, channels={"slack": {"enabled": True}}, bindings=[]))
        g = p["agents"]["entries"]["receptionist"]
        self.assertEqual(g["name"], "Outside gate")
        self.assertEqual(g["tools"]["allow"], [])
        self.assertEqual(p["tools"]["alsoAllow"], ["read"])
        self.assertEqual(p["tools"]["fs"], {"workspaceOnly": True})
        self.assertNotIn("group:fs", p["tools"]["deny"])
        self.assertTrue({"write", "edit", "apply_patch"} <= set(p["tools"]["deny"]))
        self.assertEqual({b["match"]["channel"] for b in p["bindings"]}, {"slack", "discord"})
        self.assertIn("workspace-receptionist", err)

    def test_an_existing_binding_keeps_the_gate(self):
        b = [{"type": "route", "agentId": "receptionist", "match": {"channel": "slack", "accountId": "*"}}]
        p, _, _ = patch_for(dict(BASE, bindings=b))
        self.assertIn("receptionist", p["agents"]["entries"])

    def test_the_operator_can_force_the_gate(self):
        p, _, _ = patch_for(dict(BASE, bindings=[]), env={"OPENCLAW_HARDEN_GATE": "1"})
        self.assertIn("receptionist", p["agents"]["entries"])

    def test_the_session_cycle_settings_are_always_in_the_patch(self):
        p, _, _ = patch_for(dict(BASE, bindings=[]))
        self.assertEqual(p["agents"]["entries"]["main"], {"name": "Receptionist"})
        self.assertEqual(p["session"], {"reset": {"mode": "daily", "atHour": 4}})
        self.assertIs(p["plugins"]["entries"]["memory-core"]["config"]["dreaming"]["enabled"], False)
        self.assertEqual(p["skills"]["workshop"]["autonomous"]["mode"], "off")

    def test_main_is_never_pinned_to_a_model(self):
        p, _, _ = patch_for(dict(BASE, bindings=[]))
        self.assertNotIn("model", p["agents"]["entries"]["main"])

    def test_the_hook_is_enabled_only_once_it_is_installed(self):
        p, _, _ = patch_for(dict(BASE, bindings=[]))
        self.assertNotIn("hooks", p)
        p, _, _ = patch_for(dict(BASE, bindings=[]), hook_installed=True)
        self.assertIs(p["hooks"]["internal"]["entries"]["session-handoff"]["enabled"], True)

    def test_the_reset_hour_can_be_changed(self):
        p, _, _ = patch_for(dict(BASE, bindings=[]), env={"OPENCLAW_HARDEN_RESET_HOUR": "5"})
        self.assertEqual(p["session"]["reset"]["atHour"], 5)

    def test_an_unconfigured_chain_gets_the_free_default(self):
        p, _, _ = patch_for(dict(no_chain_config(), bindings=[]))
        m = p["agents"]["defaults"]["model"]
        self.assertEqual([m["primary"]] + m["fallbacks"], FREE_CHAIN)
        self.assertEqual(p["agents"]["defaults"]["modelPolicy"]["allow"], FREE_CHAIN)
        self.assertEqual(list(p["agents"]["defaults"]["models"]), FREE_CHAIN)

    def test_fix_and_apply_leave_a_configured_claude_chain_and_providers_unchanged(self):
        cfg = json.loads(json.dumps(BASE))
        defaults = cfg["agents"]["defaults"]
        defaults["model"] = {
            "primary": "claude/claude-haiku-5-5",
            "fallbacks": ["claude/claude-sonnet-5-5"],
        }
        defaults["modelPolicy"] = {"allow": ["claude/claude-haiku-5-5", "claude/claude-sonnet-5-5"]}
        defaults["models"] = {
            "claude/claude-haiku-5-5": {"alias": "haiku"},
            "claude/claude-sonnet-5-5": {"alias": "sonnet"},
        }
        cfg["models"] = {"providers": {"claude": {"apiKey": {"source": "store"}, "models": [{"id": "claude-haiku-5-5"}]}}}

        before = {
            "model": json.dumps(defaults["model"], separators=(",", ":")),
            "modelPolicy": json.dumps(defaults["modelPolicy"], separators=(",", ":")),
            "models": json.dumps(defaults["models"], separators=(",", ":")),
            "providers": json.dumps(cfg["models"]["providers"], separators=(",", ":")),
        }
        patch, _, _ = patch_for(cfg)
        updated = merge_patch(json.loads(json.dumps(cfg)), patch)
        after = updated["agents"]["defaults"]
        self.assertEqual(json.dumps(after["model"], separators=(",", ":")), before["model"])
        self.assertEqual(json.dumps(after["modelPolicy"], separators=(",", ":")), before["modelPolicy"])
        self.assertEqual(json.dumps(after["models"], separators=(",", ":")), before["models"])
        self.assertEqual(json.dumps(updated["models"]["providers"], separators=(",", ":")), before["providers"])
        self.assertNotIn("defaults", patch["agents"])
        self.assertNotIn("models", patch)


class VerifyGate(unittest.TestCase):
    def verify_output(self, cfg):
        cfg = json.loads(json.dumps(cfg))
        defaults = cfg["agents"]["defaults"]
        model = defaults.get("model") or {}
        chain = ([model["primary"]] if model.get("primary") else []) + (model.get("fallbacks") or [])
        defaults.setdefault("models", {}).update({m: {} for m in chain})
        defaults.setdefault("modelPolicy", {}).setdefault("allow", chain)
        providers = cfg.setdefault("models", {}).setdefault("providers", {})
        for m in chain:
            providers.setdefault(m.split("/", 1)[0], {})
        d = Path(fresh_dir(self))
        (d / "bin").mkdir()
        f = d / "bin" / "openclaw"
        f.write_text(FAKE)
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
        c = d / "openclaw.json"
        c.write_text(json.dumps(cfg))
        body = "\n".join(SCRIPT.read_text().splitlines()[:-1])
        run = f'source <(cat <<\'__H__\'\n{body}\n__H__\n)\nHOME="{d}"; init; do_verify'
        e = dict(os.environ, PATH=f"{d}/bin:{os.environ['PATH']}", FAKE_CFG=str(c), HOME=str(d))
        r = subprocess.run(["bash", "-c", run], capture_output=True, text=True, env=e, timeout=60)
        return r.stdout

    def test_verify_fails_when_a_channel_is_configured_but_the_gate_agent_is_missing(self):
        out = self.verify_output(dict(BASE, channels={"slack": {"enabled": True}}, bindings=[]))
        self.assertIn("FAIL gate.present_with_channel", out)

    def test_verify_does_not_ask_for_a_gate_when_no_channel_is_configured(self):
        out = self.verify_output(dict(BASE, channels={}, bindings=[]))
        self.assertIn("PASS gate.absent_without_channel", out)
        self.assertNotIn("gate.present_with_channel", out)

    def test_verify_accepts_a_configured_claude_chain_with_a_provider(self):
        cfg = json.loads(json.dumps(BASE))
        cfg["agents"]["defaults"]["model"] = {
            "primary": "claude/claude-haiku-5-5",
            "fallbacks": ["claude/claude-sonnet-5-5"],
        }
        cfg["agents"]["defaults"]["modelPolicy"] = {
            "allow": ["claude/claude-haiku-5-5", "claude/claude-sonnet-5-5"]
        }
        cfg["models"] = {"providers": {"claude": {"apiKey": {"source": "store"}}}}
        out = self.verify_output(dict(cfg, channels={}, bindings=[]))
        self.assertIn("PASS chain.allow_and_provider_entries 2 models checked", out)

    def test_verify_fails_when_a_write_tool_is_not_denied(self):
        cfg = dict(BASE, channels={}, bindings=[])
        cfg["tools"] = {"deny": ["group:runtime", "edit", "apply_patch", "group:web", "group:ui", "group:automation",
                                  "group:nodes", "group:plugins", "group:messaging", "sessions_spawn", "sessions_send"],
                        "alsoAllow": ["read"], "fs": {"workspaceOnly": True}}
        out = self.verify_output(cfg)
        self.assertIn("FAIL tools.deny", out)

    def test_verify_fails_when_workspace_only_is_disabled(self):
        cfg = dict(BASE, channels={}, bindings=[])
        cfg["tools"] = {"deny": ["group:runtime", "write", "edit", "apply_patch", "group:web", "group:ui", "group:automation",
                                  "group:nodes", "group:plugins", "group:messaging", "sessions_spawn", "sessions_send"],
                        "alsoAllow": ["read"], "fs": {"workspaceOnly": False}}
        out = self.verify_output(cfg)
        self.assertIn("FAIL tools.read_only_workspace", out)

    def test_verify_fails_when_also_allow_adds_anything_besides_read(self):
        cfg = dict(BASE, channels={}, bindings=[])
        cfg["tools"] = {"deny": ["group:runtime", "write", "edit", "apply_patch", "group:web", "group:ui", "group:automation",
                                  "group:nodes", "group:plugins", "group:messaging", "sessions_spawn", "sessions_send"],
                        "alsoAllow": ["read", "write"], "fs": {"workspaceOnly": True}}
        out = self.verify_output(cfg)
        self.assertIn("FAIL tools.read_only_workspace", out)


if __name__ == "__main__":
    unittest.main()
