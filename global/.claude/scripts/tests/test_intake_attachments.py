"""21 Oct 7:00 PM: he pasted a 6 KB text file into the chat. The gateway saved it (media/inbound) and the history row named it, but
the intake copy, the ask and context/<id>.json kept only the typed line ("absorb this ... ::"), so the hub got no content. A message with
an attachment now carries a small text attachment's words, or the path of a saved copy when it is large, into all three.
The gateway and the PC are fakes: nothing real is read."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
import test_openclaw_reply as tr  # noqa: E402

NOW, ASK_TS = tr.NOW, tr.ASK_TS
TYPED = "also onboard a new internship, absorb this at one level about Consemble ::"
NAME = "pasted-text-1792623577909---fa1406a8-81f0-423d-8137-0ce2fd47bfe2.txt"
LARGE = "This is the start of the #scoutjob-ai channel.\n" + "emhill: sorry, my ai had NOT been keeping github in sync\n" * 120
SMALL = "Use the new intern checklist, not the old one."


def media(name=NAME, kind="text/plain", size=6279, file="pasted-text-1792623577909.txt"):
    return {"url": "media://inbound/" + name, "contentType": kind, "kind": "document", "fileName": file, "origin": "paste", "sizeBytes": size,
            "hydrationSuppressed": True}


def him(text=TYPED, files=None, ts=ASK_TS + 1):
    m = tr.him(text, ts)
    m["media"] = pk._media_of({"__openclaw": {"media": files}}) if files else []
    return m


class AttachHost(tr.ReplyHost):
    def __init__(self, history, files=None, intake=None):
        super().__init__(history)
        self.files, self.fetched, self.intake_dir = files or {}, [], intake

    def fetch_media(self, name, max_bytes):
        self.fetched.append(name)
        return self.files.get(name)


class Attach(td.Base):
    def setUp(self):
        super().setUp()
        self.intake = self.tmp / "intake"
        self.intake.mkdir(mode=0o700)

    def go(self, history, files, text=TYPED, now=NOW):
        self.host = AttachHost(history, files, str(self.intake))
        item = self.item(text, ts=ASK_TS)
        (self.intake / (item["sha256_of_original"] + ".json")).write_text(json.dumps(item))
        res = [{"verdict": "accept", "stored": item}]
        with mock.patch.object(pk, "site_models", return_value={}):
            return pk.run_act(res, self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=now, ctx=self.ctx)

    def reply(self, text=None):
        return tr.msg("assistant", "Sending to the hub: " + (text or TYPED), ASK_TS + 4)

    def ask(self):
        asks = dp.asks_of(self.state())
        self.assertEqual(len(asks), 1, asks)
        k = next(iter(asks))
        return k, asks[k]

    def intake_item(self):
        return json.loads((self.intake / (self.item("x")["sha256_of_original"] + ".json")).read_text())

    def test_the_history_row_names_its_attachment(self):
        row = {"role": "user", "content": TYPED, "timestamp": 1792623579913, "__openclaw": {"media": [media()]}}
        (m,) = pk.normalize_history({"messages": [row]})
        self.assertEqual(m["media"], [{"name": NAME, "file": "pasted-text-1792623577909.txt", "type": "text/plain", "size": 6279}])
        self.assertEqual(pk.normalize_history({"messages": [{"role": "user", "content": "hi"}]})[0]["media"], [])

    def test_a_large_pasted_file_reaches_the_ask_by_its_saved_path_and_the_copy_is_whole(self):
        self.go([him(files=[media()]), self.reply()], {NAME: LARGE})
        saved = self.intake / "attachments" / NAME
        self.assertEqual(saved.read_text(), LARGE)
        self.assertEqual(saved.stat().st_mode & 0o077, 0)
        _, ask = self.ask()
        self.assertIn(TYPED, ask["task"])
        self.assertIn("[attached pasted-text-1792623577909.txt, 6279 bytes, saved at %s]" % saved, ask["task"])
        self.assertNotIn("emhill", ask["task"])  # a large file is a path, not 6 KB on one inbox line

    def test_the_context_file_and_the_intake_copy_carry_it_too(self):
        self.go([him(files=[media()]), self.reply()], {NAME: LARGE})
        k, _ = self.ask()
        ctxt = dp.load_context(self.ctx, k)
        mine = [m for m in ctxt["history"] if m["who"] == "Jonathan"][-1]
        self.assertTrue(mine["text"].startswith(TYPED))
        self.assertIn(str(self.intake / "attachments" / NAME), mine["text"])
        (att,) = self.intake_item()["attachments"]
        self.assertEqual((att["file"], att["size"], att["path"]), ("pasted-text-1792623577909.txt", 6279, str(self.intake / "attachments" / NAME)))
        self.assertNotIn("text", att)  # large: the saved copy holds it

    def test_a_small_pasted_file_travels_inside_his_message(self):
        self.go([him(files=[media(size=len(SMALL))]), self.reply()], {NAME: SMALL})
        k, ask = self.ask()
        self.assertIn(TYPED, ask["task"])
        self.assertIn("[attached pasted-text-1792623577909.txt, %d bytes]\n%s" % (len(SMALL), SMALL), ask["task"])
        mine = [m for m in dp.load_context(self.ctx, k)["history"] if m["who"] == "Jonathan"][-1]
        self.assertIn("\n" + SMALL, mine["text"])
        self.assertEqual(self.intake_item()["attachments"][0]["text"], SMALL)

    def test_a_message_the_poller_cannot_settle_still_files_his_attachment(self):
        self.go([him(files=[media()])], {NAME: LARGE}, now=NOW + 400)  # no reply from the front desk, and he has waited out REPLY_WAIT
        k, ask = self.ask()
        self.assertIn("saved at", ask["task"])
        mine = [m for m in dp.load_context(self.ctx, k)["history"] if m["who"] == "Jonathan"]
        self.assertEqual(len(mine), 1)
        self.assertIn(TYPED, mine[0]["text"])

    def test_a_picture_is_a_path_in_the_gateway_folder_and_is_never_fetched(self):
        png = "a-picture---0f1e2d3c.png"
        self.go([him(files=[media(png, "image/png", 52000, "shot.png")]), self.reply()], {})
        self.assertEqual(self.host.fetched, [])
        _, ask = self.ask()
        self.assertIn("[attached shot.png, 52000 bytes, saved at /home/openclaw/.openclaw/media/inbound/%s (in the OpenClawGateway distro)]" % png, ask["task"])

    def test_a_file_that_cannot_be_read_never_loses_his_message(self):
        self.go([him(files=[media()]), self.reply()], {})  # fetch gives None
        _, ask = self.ask()
        self.assertIn(TYPED, ask["task"])
        self.assertIn("/home/openclaw/.openclaw/media/inbound/" + NAME, ask["task"])

    def test_a_path_that_climbs_out_of_the_folder_is_ignored(self):
        row = {"__openclaw": {"media": [media("../../etc/passwd"), media("a b.txt"), media(NAME)]}}
        self.assertEqual([m["name"] for m in pk._media_of(row)], [NAME])
        self.assertIsNone(pk.Host().fetch_media("../x", 10))

    def test_a_message_with_no_attachment_is_unchanged(self):
        self.go([him(), self.reply()], {})
        _, ask = self.ask()
        self.assertEqual(ask["task"], TYPED)
        self.assertEqual(self.host.fetched, [])
        self.assertNotIn("attachments", self.intake_item())

    def test_binary_looking_text_is_not_carried(self):
        self.go([him(files=[media()]), self.reply()], {NAME: "ab\x00cd"})
        _, ask = self.ask()
        self.assertIn("/home/openclaw/.openclaw/media/inbound/" + NAME, ask["task"])
        self.assertFalse((self.intake / "attachments" / NAME).exists())


if __name__ == "__main__":
    unittest.main()
