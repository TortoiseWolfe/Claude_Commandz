"""Fixtures for class_resolver.py: one per signal, plus an unlisted repo, an unreadable list and a mixed
public-and-client item. Every rule also has a mutation probe: the rule is switched off and its fixture must
then fail (go red), which shows the fixture really depends on the rule. Fake lists live in a temp home."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import class_resolver as cr  # noqa: E402

SENDERS = "client\trepo_dir\tkind\tgmail_from\nAcme Co\t~/repos/acme-site\tmail\tlist.acme-mail.example\nAcme Co\t~/repos/acme-site\tmail\tboss@acme-mail.example\n"
CLIENT_REPOS = "# comment\n~/repos/harmonic-egg\n\n~/repos/listed-pub-but-client\n"
CLASSES = {"repos": {"ScriptHammer": "public", "AI_Workflow": "public", "TurtleWolfe-Expo": "own", "acme-site": "client",
                     "listed-pub-but-client": "public", "harmonic-egg": "public", "mapclient": "client"}}
TERMS = "# private\nzebracorp\nJane Doe\n"


class Env:
    """A temp home with the five lists, each one writable or removable by the test."""

    def __init__(self):
        self.home = tempfile.mkdtemp()
        h = Path(self.home)
        (h / "repos/hub").mkdir(parents=True)
        (h / ".config/panel").mkdir(parents=True)
        (h / "repos/CD/cd-civicrm").mkdir(parents=True)
        (h / "repos/hub/client-senders.tsv").write_text(SENDERS)
        (h / "repos/hub/client-repos.txt").write_text(CLIENT_REPOS)
        (h / ".config/panel/classes.json").write_text(json.dumps(CLASSES))
        (h / ".config/panel/pii-terms.txt").write_text(TERMS)
        self.cfg = cr.Config(
            clients=str(h / "repos/hub/client-senders.tsv"), repos=str(h / "repos/hub/client-repos.txt"),
            cd=str(h / "repos/CD"), classes=str(h / ".config/panel/classes.json"),
            terms=str(h / ".config/panel/pii-terms.txt"), home=self.home)

    def resolve(self, item):
        return cr.resolve(item, self.cfg)

    def close(self):
        shutil.rmtree(self.home, ignore_errors=True)


def rules_of(res):
    return {r["rule"] for r in res["rules"]}


# One fixture per rule: (rule name, item, expected class, rule that must show in the result).
FIXTURES = {
    "sender": ({"sender": "Boss <boss@acme-mail.example>", "repos": ["AI_Workflow"]}, "client", "sender"),
    "client-repo": ({"repos": ["listed-pub-but-client"]}, "client", "client-repo"),
    "class-map": ({"repos": ["mapclient"]}, "client", "class-map"),
    "terms": ({"text": "please ask ZebraCorp about it", "repos": ["AI_Workflow"]}, "client", "terms"),
    "prior-class": ({"prior_class": "client", "repos": ["AI_Workflow"]}, "client", "prior-class"),
}


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)

    def test_each_signal_fixture(self):
        for name, (item, cls, rule) in FIXTURES.items():
            with self.subTest(rule=name):
                res = self.env.resolve(item)
                self.assertEqual(res["class"], cls)
                self.assertIn(rule, rules_of(res))

    def test_cd_path_is_client(self):
        res = self.env.resolve({"paths": ["~/repos/CD/cd-civicrm/src/x.php"]})
        self.assertEqual(res["class"], "client")
        self.assertIn("client-repo", rules_of(res))

    def test_cd_name_in_text_is_client(self):
        self.assertEqual(self.env.resolve({"text": "the cd-civicrm upgrade"})["class"], "client")

    def test_client_repo_path_in_text_is_client(self):
        self.assertEqual(self.env.resolve({"text": "see /home/x/repos/harmonic-egg/README.md"})["class"], "client")

    def test_unlisted_repo_is_client(self):
        res = self.env.resolve({"repos": ["brand-new-thing"]})
        self.assertEqual(res["class"], "client")
        self.assertIn("class-map", rules_of(res))

    def test_unlisted_repo_path_in_text_is_client(self):
        self.assertEqual(self.env.resolve({"text": "look in ~/repos/other-thing/src"})["class"], "client")

    def test_worktree_path_resolves_to_its_repo(self):
        res = self.env.resolve({"text": "in ~/repos/.worktrees/AI_Workflow--openclaw-spec/specs"})
        self.assertEqual(res["class"], "public")

    def test_public_item(self):
        res = self.env.resolve({"repos": ["ScriptHammer"], "text": "bump the footer"})
        self.assertEqual(res["class"], "public")
        self.assertFalse(res["fail_closed"])

    def test_own_item(self):
        self.assertEqual(self.env.resolve({"repos": ["TurtleWolfe-Expo"]})["class"], "own")

    def test_no_repo_is_own_not_public(self):
        res = self.env.resolve({"text": "what is the lane status?"})
        self.assertEqual(res["class"], "own")
        self.assertIn("no-repo-default", rules_of(res))

    def test_mixed_public_and_client_is_client_with_both_rules(self):
        res = self.env.resolve({"repos": ["ScriptHammer", "mapclient"], "text": "compare ScriptHammer with mapclient"})
        self.assertEqual(res["class"], "client")
        classes = {r["class"] for r in res["rules"]}
        self.assertIn("public", classes)
        self.assertIn("client", classes)

    def test_word_boundary_for_terms(self):
        self.assertEqual(self.env.resolve({"text": "macmeal zebracorpse", "repos": ["AI_Workflow"]})["class"], "public")
        self.assertEqual(self.env.resolve({"text": "getZebracorpUser()", "repos": ["AI_Workflow"]})["class"], "client")

    def test_text_is_never_in_the_result(self):
        res = self.env.resolve({"text": "SECRETMARKER zebracorp", "sender": "boss@acme-mail.example"})
        self.assertNotIn("SECRETMARKER", json.dumps(res))
        self.assertNotIn("zebracorp", json.dumps(res).lower())

    def test_bad_item_is_client(self):
        self.assertEqual(self.env.resolve("not an object")["class"], "client")
        self.assertEqual(self.env.resolve(None)["class"], "client")

    def test_rule_that_raises_is_client(self):
        with mock.patch.dict(cr.RULES, {"terms": mock.Mock(side_effect=RuntimeError("boom"))}):
            res = self.env.resolve({"repos": ["AI_Workflow"]})
        self.assertEqual(res["class"], "client")


class FailClosed(unittest.TestCase):
    LISTS = {"clients": "repos/hub/client-senders.tsv", "repos": "repos/hub/client-repos.txt", "cd": "repos/CD",
             "classes": ".config/panel/classes.json", "terms": ".config/panel/pii-terms.txt"}
    PUBLIC = {"repos": ["AI_Workflow"], "text": "bump the footer"}

    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)
        self.assertEqual(self.env.resolve(self.PUBLIC)["class"], "public")   # the clean baseline

    def _each(self, damage):
        for name, rel in self.LISTS.items():
            with self.subTest(list=name):
                env = Env()
                try:
                    damage(Path(env.home) / rel)
                    res = env.resolve(self.PUBLIC)
                    self.assertEqual(res["class"], "client")
                    self.assertTrue(res["fail_closed"])
                    self.assertIn(f"list-unreadable:{name}", rules_of(res))
                finally:
                    env.close()

    def test_missing_list_is_client(self):
        self._each(lambda p: shutil.rmtree(p) if p.is_dir() else p.unlink())

    def test_directory_in_place_of_a_file_is_client(self):
        def swap(p):
            if p.is_dir():
                shutil.rmtree(p)
                p.write_text("x")   # a file where the folder should be
            else:
                p.unlink()
                p.mkdir()
        self._each(swap)

    def test_unreadable_file_is_client(self):
        if os.geteuid() == 0:
            self.skipTest("root reads everything")

        def lock(p):
            p.chmod(0o000)
        for name in ("clients", "repos", "classes", "terms"):
            with self.subTest(list=name):
                env = Env()
                try:
                    lock(Path(env.home) / self.LISTS[name])
                    res = env.resolve(self.PUBLIC)
                    self.assertEqual(res["class"], "client")
                    self.assertIn(f"list-unreadable:{name}", rules_of(res))
                finally:
                    env.close()

    def test_malformed_class_map_is_client(self):
        (Path(self.env.home) / self.LISTS["classes"]).write_text("{not json")
        res = self.env.resolve(self.PUBLIC)
        self.assertEqual(res["class"], "client")
        self.assertIn("list-unreadable:classes", rules_of(res))
        (Path(self.env.home) / self.LISTS["classes"]).write_text('{"repos": ["a"]}')
        self.assertEqual(self.env.resolve(self.PUBLIC)["class"], "client")

    def test_unknown_class_value_in_map_is_client(self):
        (Path(self.env.home) / self.LISTS["classes"]).write_text('{"repos": {"AI_Workflow": "friendly"}}')
        self.assertEqual(self.env.resolve(self.PUBLIC)["class"], "client")


class ModelRaisesNeverLowers(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)

    def test_model_can_raise(self):
        res = self.env.resolve({"repos": ["AI_Workflow"], "model_class": "own"})
        self.assertEqual(res["class"], "own")
        self.assertIn("model-raised", rules_of(res))
        self.assertEqual(self.env.resolve({"repos": ["AI_Workflow"], "model_class": "client"})["class"], "client")

    def test_model_cannot_lower(self):
        for lowered in ("public", "own"):
            with self.subTest(model=lowered):
                res = self.env.resolve({"sender": "boss@acme-mail.example", "model_class": lowered})
                self.assertEqual(res["class"], "client")
        self.assertEqual(self.env.resolve({"repos": ["TurtleWolfe-Expo"], "model_class": "public"})["class"], "own")

    def test_unknown_model_class_counts_as_client(self):
        self.assertEqual(self.env.resolve({"repos": ["AI_Workflow"], "model_class": "friendly"})["class"], "client")


class MutationProbes(unittest.TestCase):
    """Switch one rule off and its fixture must go red (no longer yield the expected class)."""

    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)

    def test_each_rule_fixture_goes_red_when_the_rule_is_off(self):
        for name, (item, cls, _rule) in FIXTURES.items():
            with self.subTest(rule=name):
                self.assertEqual(self.env.resolve(item)["class"], cls)   # green first
                with mock.patch.dict(cr.RULES, {name: lambda *a: []}):
                    self.assertNotEqual(self.env.resolve(item)["class"], cls, f"fixture for {name} stayed green with the rule off")

    def test_cd_fixture_goes_red_without_client_repo_rule(self):
        item = {"paths": ["~/repos/CD/cd-civicrm/x"]}
        with mock.patch.dict(cr.RULES, {"client-repo": lambda *a: []}):
            self.assertNotEqual(self.env.resolve(item)["class"], "client")

    def test_unlisted_repo_goes_red_without_class_map_rule(self):
        item = {"repos": ["brand-new-thing"]}
        with mock.patch.dict(cr.RULES, {"class-map": lambda *a: []}):
            self.assertNotEqual(self.env.resolve(item)["class"], "client")

    def test_fail_closed_goes_red_when_load_errors_are_swallowed(self):
        env = Env()
        self.addCleanup(env.close)
        (Path(env.home) / ".config/panel/pii-terms.txt").unlink()
        item = {"repos": ["AI_Workflow"]}
        self.assertEqual(env.resolve(item)["class"], "client")
        real = cr.load_lists

        def swallow(cfg=None):
            L = real(cfg)
            L.bad = []
            return L
        with mock.patch.object(cr, "load_lists", swallow):
            self.assertNotEqual(cr.resolve(item, env.cfg)["class"], "client")

    def test_model_lowering_goes_red_when_stricter_is_broken(self):
        item = {"sender": "boss@acme-mail.example", "model_class": "public"}
        self.assertEqual(self.env.resolve(item)["class"], "client")
        with mock.patch.object(cr, "stricter", lambda a, b: b):
            self.assertNotEqual(self.env.resolve(item)["class"], "client")


if __name__ == "__main__":
    unittest.main()
