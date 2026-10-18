#!/usr/bin/env python3
"""Mutation probe for the eight front-desk fixes (17 Oct), the follow-up fixes and the dupes fixes (D): break each change in a temp copy of the scripts folder and confirm a
test goes red. A mutant that leaves the tests green is a gap.

  python3 mutate_frontdesk_fixes.py              all mutants
  MUTANT_ONLY="cap" python3 mutate_frontdesk_fixes.py   just the mutants whose name holds that text

Python mutants run the three test files that cover the ask path; lane_watch mutants run tests/test_lane_watch.sh.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
D, W = "openclaw_dispatch.py", "lane_watch.sh"
PY_TESTS = ["test_openclaw_pass", "test_openclaw_dispatch", "test_openclaw_reply"]

MUTANTS = [
    # 1. skip the pass unless the message is plainly a GitHub-facts question
    ("1", D, "the pass runs for every question", 'kind = pass_kind(words) if ctx.hand_off_first else None', 'kind = "facts" if ctx.hand_off_first else None'),
    ("1", D, "any question shape counts as facts", 'return "facts" if FACTS_NOUN_RE.search(t) else None', 'return "facts"'),
    ("1", D, "a request to do something still runs the pass", "if not t or HUB_WORK_RE.search(t) or HUB_MAIL_RE.search(t) or not QUESTION_SHAPE_RE.search(t):", "if not t or HUB_MAIL_RE.search(t) or not QUESTION_SHAPE_RE.search(t):"),
    ("1", D, "a statement runs the pass", "if not t or HUB_WORK_RE.search(t) or HUB_MAIL_RE.search(t) or not QUESTION_SHAPE_RE.search(t):", "if not t or HUB_WORK_RE.search(t) or HUB_MAIL_RE.search(t):"),
    ("1", D, "a direct-to-hub question still waits for a pass", 'if not use_pass:  # straight to the hub: it is already waiting there', "if False:"),
    # 2. his exact words are the task
    ("2", D, "the task is the receptionist's rewording", "words = his_words(context, q)", "words = q"),
    ("2", D, "his words are never found in the context", "    return text or fallback", "    return fallback"),
    ("2", D, "the rewording is not kept as the marker", '"task": words, "marker": q,', '"task": words,'),
    ("2", D, "open repeats his words under the row", "        elif \" \".join(history[latest][\"text\"].split()) == question.replace(\"\\\\|\", \"|\"):", "        elif False:"),
    # 3. only an exact repeat of an unanswered question, said out loud
    ("3", D, "no repeat check at all", "repeat = _repeat_of_open_question(asks, key)", "repeat = None"),
    ("3", D, "an answered question counts as a repeat", 'UNANSWERED_STATUSES = ("pending", "deferred",', 'UNANSWERED_STATUSES = ("answered", "pending", "deferred",'),
    ("3", D, "any open question counts as a repeat", '(a.get("task_key") or task_key(a.get("task", ""))) == key', "True"),
    ("3", D, "a repeat is dropped without a word", "                    _already_with(ctx, k, a, now))", "                    None)"),
    ("3", D, "the repeat message hides the time", 'since = time.strftime("%H:%M", time.localtime(ask.get("created", 0)))', 'since = "?"'),
    ("3", D, "an old repeat never reminds the hub", "        _touch_wake(ctx, ask_id, now)\n        flagged =", "        flagged ="),
    # 4. one wake per burst (lane_watch.sh)
    ("4", W, "arm-time check does not hold", "    hold_burst\n    fresh=", "    fresh="),
    ("4", W, "the hold ends at once", 'while [ "$(wake_age)" -lt "$batch_quiet" ] &&', "while false &&"),
    ("4", W, "the hold has no cap", ' && [ $(( $(date +%s) - t0 )) -lt "$batch_cap" ]; do sleep', "; do sleep"),
    ("4", W, "the list is not re-read after the hold", '    fresh=$(python3 "$dispatch" open 2>/dev/null) && [ "$fresh" != none ] && questions=$fresh', "    :"),
    ("4", W, "a question filed while waiting wakes at once", "    if [ $other = 0 ]; then", "    if false; then"),
    # 5. no 300-character cut on the hub list
    ("5", D, "the hub list cuts at 300 characters", 'json.dumps(" ".join(question.split()), ensure_ascii=True).replace("|", "\\\\u007c")  # whole', 'json.dumps(" ".join(question.split())[:300], ensure_ascii=True).replace("|", "\\\\u007c")  # whole'),
    # 6. a message when a pass ends without an answer
    ("6", D, "a pass that ends without an answer says nothing", "        host.inject(chat)  # a pass that ends with no answer is never silent", "        pass"),
    ("6", D, "the notice leaves out the time", 'so your question is with the hub now (since %s). Your words went with it exactly as written." % (why, _clock_of(now))', 'so your question is with the hub now. Your words went with it exactly as written." % why'),
    # 7. the cap is said, and the question is filed
    ("7", D, "the cap is never reached", "full = kind is not None and len(waiting) >= MAX_QUESTIONS", "full = False"),
    ("7", D, "a capped question is not marked as such", 'ask["route"] = "hub, pass full" if full else "hub, no pass"', 'ask["route"] = "hub, no pass"'),
    ("7", D, "the cap message is not sent", '            return ("%s | question | %d questions already waiting: put on the hub\'s list (ask %s)" % (stamp, len(waiting), ask_id),\n                    "Claude\'s quick check', '            return ("%s | question | %d questions already waiting: put on the hub\'s list (ask %s)" % (stamp, len(waiting), ask_id),\n                    None and "Claude\'s quick check'),
    # 8. a finish time on every ask
    ("8", D, "finish is never recorded", '    ask.setdefault("finished_at", now if at is None else at)', "    pass"),
    ("8", D, "a later finish overwrites the first", '    ask.setdefault("finished_at", now if at is None else at)', '    ask["finished_at"] = now if at is None else at'),
    ("8", D, "a hub post records no finish", '                _finish(ask, now, "hub post")', "                pass"),
    ("8", D, "a pass answer records no finish", '_finish(ask, now, "pass say")', "pass"),
    ("8", D, "settling ignores the hub's own post time", '_finish(asks[ask_id], now, "hub post, settled later", posted)', '_finish(asks[ask_id], now, "settled")'),
    ("8", D, "the hub's post time is not remembered", '            state["last_hub_post_at"] = time.time()', "            pass"),
    ("8", D, "a claimed lane records no finish", '_finish(ask, now, "lane claimed")', "pass"),
    ("8", D, "a promote records no finish", '_finish(ask, now, "promoted to a job")', "pass"),
    ("8", D, "a held promote keeps its finish", '                ask.pop("finished_at", None)', "                pass"),
    # followup A. a posted lane report answers its ask; a second lane can join it
    ("A", D, "a posted report leaves its ask claimed", "        needs_hub = _settle_lane_ask(state, name, chat, now)\n", "        needs_hub = None\n"),
    ("A", D, "the ask is answered without the posted text", 'ask["answer"], ask["status"], ask["answered_at"] = text, "answered", now', 'ask["answer"], ask["status"], ask["answered_at"] = "", "answered", now'),
    ("A", D, "the ask is answered without the time", 'ask["answer"], ask["status"], ask["answered_at"] = text, "answered", now', 'ask["answer"], ask["status"] = text, "answered"'),
    ("A", D, "the first of two lanes settles the ask", '    if any(state["lanes"].get(n, {}).get("status") == "started" for n in ask.get("lanes", []) if n != lane_name):\n        return None\n', ""),
    ("A", D, "an answered ask is never reopened by a second lane", 'reclaim = (ask["status"] in ("claimed", "answered") and', 'reclaim = (ask["status"] in ("claimed",) and'),
    ("A", D, "a second lane is never added", 'ask["lanes"] = (ask.get("lanes") or []) + [lane_name] if reclaim else [lane_name]', 'ask["lanes"] = [lane_name]'),
    ("A", D, "a stale second lane is accepted", "                fresh = 0 <= now - float(row[8]) <= CLAIM_WINDOW", "                fresh = True"),
    ("A", D, "the pass may add a second lane too", 'and not os.environ.get("AFA_OC_PASS"))', ")"),
    ("A", D, "a lane that is already someone's is re-claimed", '            if lane_name in state["lanes"]:\n                return False, "claim: lane %s already belongs', '            if False:\n                return False, "claim: lane %s already belongs'),
    # followup B. a note to Hatch or a Gmail draft goes to the hub, which has the mail connector
    ("B", D, "a note to Hatch still starts a pass", '    if HUB_MAIL_RE.search(task):  # a lane here has no mail connector', '    if False:  # a lane here has no mail connector'),
    ("B", D, "Hatch is not recognised", r'\b(?:hatch|muse|h-7ch)\b|', r'\b(?:muse|h-7ch)\b|'),
    ("B", D, "a Gmail draft is not recognised", r'|\bgmail\b|\bdraft', r'|\bdraft'),
    ("B", D, "a drafted email is not recognised", r'\bdraft(?:s|ed|ing)?\s+(?:an?\s+|the\s+)?(?:e-?mail|reply|note)\b", re.I)', r'\bdraft(?:s|ed|ing)?\s+(?:an?\s+|the\s+)?(?:reply|note)\b", re.I)'),
    ("B", D, "the hub reason is not about mail", '"a note to Hatch or a Gmail draft: only the hub has the mail connector, so no lane or pass ran"', '"not a plain GitHub-facts question, so no pass ran"'),
    ("B", D, "a mail question can still start the facts pass", "HUB_WORK_RE.search(t) or HUB_MAIL_RE.search(t) or not QUESTION_SHAPE_RE", "HUB_WORK_RE.search(t) or not QUESTION_SHAPE_RE"),
    # dupes D. a failed lane never closes its ask; the hub can follow up on an answered one; the hub-list line is short
    ("D", D, "a failed lane's report closes its ask", '    if state["lanes"][lane_name].get("failed"):\n        return ask_id\n', ""),
    ("D", D, "a failed lane's ask never reaches the hub list", '    if state["lanes"][lane_name].get("failed"):\n        return ask_id\n', '    if state["lanes"][lane_name].get("failed"):\n        return None\n'),
    ("D", D, "a failed lane with a sibling still working goes to the hub", '    if any(state["lanes"].get(n, {}).get("status") == "started" for n in ask.get("lanes", []) if n != lane_name):\n        return None\n    if state["lanes"][lane_name].get("failed"):', '    if state["lanes"][lane_name].get("failed"):'),
    ("D", D, "the hub list line repeats his words", '"On the hub\'s list: the hub has your message."', '"On the hub\'s list: %s" % words'),
    ("D", D, "the cap line repeats his words", "(since %s); the hub has your message.\" % (\n                        len(waiting), _clock_of(now)))", "(since %s): %s\" % (\n                        len(waiting), _clock_of(now), words))"),
    ("D", D, "the open list hides a failed job ask", '                status = "lane-failed"  # its lane failed', '                continue  # its lane failed'),
    ("D", D, "the hub cannot post to an ask whose lane failed", '        ask.get("status") == "claimed" and _ask_failed(state, ask))', '        False)'),
    ("D", D, "the hub can post over a claimed ask with a lane working", '        ask.get("status") == "claimed" and _ask_failed(state, ask))', '        ask.get("status") == "claimed")'),
    ("D", D, "an answered ask refuses a follow-up", 'if not (_post_closes(state, ask) or ask.get("status") == "answered"):', 'if not _post_closes(state, ask):'),
    ("D", D, "a follow-up overwrites the answer", 'ask.setdefault("followups", []).append({"text": text, "at": now})', 'ask["answer"] = text'),
    ("D", D, "a follow-up is not recorded", 'ask.setdefault("followups", []).append({"text": text, "at": now})', 'pass'),
    ("D", D, "a follow-up is recorded without its time", '{"text": text, "at": now}', '{"text": text}'),
    ("D", D, "a follow-up reopens the ask", 'ask.setdefault("followups", []).append({"text": text, "at": now})', 'ask.setdefault("followups", []).append({"text": text, "at": now}); ask["status"] = "claimed"'),
    ("D", D, "a follow-up is posted twice", '    if not host.inject(chat_text):\n        return False, "post: could not post to the chat"', '    if not (host.inject(chat_text) and host.inject(chat_text)):\n        return False, "post: could not post to the chat"'),
    ("D", D, "the dispatch pass may post a follow-up", '        return False, "post: the dispatch pass may not post"\n    text = re.sub(r"^\\s*\\[hub\\]\\s*", "", (text or "").strip())\n    ids = _ask_ids', '        pass\n    text = re.sub(r"^\\s*\\[hub\\]\\s*", "", (text or "").strip())\n    ids = _ask_ids'),
    ("D", D, "settle refuses a claimed or lane-failed ask", '"fallback", "fallback-lane", "claimed"):  # claimed: incl. lane-failed', '"fallback", "fallback-lane"):  # claimed: incl. lane-failed'),
    ("D", D, "settle accepts an answered ask", '"fallback", "fallback-lane", "claimed"):  # claimed: incl. lane-failed', '"fallback", "fallback-lane", "claimed", "answered"):  # claimed: incl. lane-failed'),
]


def run(mutant_dir, shell):
    if shell:
        return subprocess.run(["bash", "tests/test_lane_watch.sh"], cwd=mutant_dir, capture_output=True, text=True, timeout=300)
    return subprocess.run([sys.executable, "-m", "unittest", *PY_TESTS], cwd=mutant_dir / "tests", capture_output=True, text=True, timeout=300)


def main():
    only = os.environ.get("MUTANT_ONLY", "")
    mutants = [m for m in MUTANTS if only in m[2] or only == m[0]]
    rows, killed = [], 0
    for change, rel, name, old, new in mutants:
        src = (SCRIPTS / rel).read_text()
        if src.count(old) != 1:
            rows.append((change, name, "PATTERN FOUND %d TIMES" % src.count(old), "-"))
            continue
        d = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(SCRIPTS, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "graphify-out"))
            (d / rel).write_text(src.replace(old, new, 1))
            r = run(d, rel == W)
            if r.returncode == 0:
                rows.append((change, name, "SURVIVED (tests green)", "0"))
            else:
                m = re.search(r"FAILED \((.*)\)", r.stderr)
                n = sum(int(x) for x in re.findall(r"=(\d+)", m.group(1))) if m else len(re.findall(r"^FAIL ", r.stdout, re.M)) or "?"
                rows.append((change, name, "killed", str(n)))
                killed += 1
        finally:
            shutil.rmtree(d, ignore_errors=True)
    for change, name, verdict, n in rows:
        print(f"change {change}  {verdict:24} red={n:>3}  {name}")
    print(f"\n{killed} of {len(mutants)} mutants killed")
    return 0 if killed == len(mutants) else 1


if __name__ == "__main__":
    sys.exit(main())
