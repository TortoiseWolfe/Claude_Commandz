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

SCRIPT = Path(__file__).resolve().parent.parent / "openclaw_harden.sh"

FAKE = r"""#!/usr/bin/env bash
case "$1 $2" in
  "config file") echo "$FAKE_CFG" ;;
  "config schema") echo '{"properties":{"channels":{"properties":{"defaults":{},"modelByChannel":{},"slack":{},"discord":{}}}}}' ;;
  *) exit 9 ;;
esac
"""


def patch_for(cfg, env=None, hook_installed=False):
    d = Path(tempfile.mkdtemp())
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

    def test_the_chain_is_the_default_one(self):
        p, _, _ = patch_for(dict(BASE, bindings=[]))
        m = p["agents"]["defaults"]["model"]
        self.assertEqual([m["primary"]] + m["fallbacks"], ["google/gemini-3.5-flash", "google/gemini-3.5-flash-lite", "google/gemini-3.6-flash", "ollama/q"])


class VerifyGate(unittest.TestCase):
    def verify_output(self, cfg):
        cfg = json.loads(json.dumps(cfg))
        cfg["agents"]["defaults"]["models"] = {m: {} for m in ["google/gemini-3.5-flash", "google/gemini-3.5-flash-lite", "google/gemini-3.6-flash", "ollama/q"]}
        d = Path(tempfile.mkdtemp())
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


if __name__ == "__main__":
    unittest.main()
