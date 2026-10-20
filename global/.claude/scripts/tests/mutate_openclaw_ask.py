#!/usr/bin/env python3
"""Mutation probe for the OpenClaw ask path (openclaw_pickup.py --act and openclaw_ask_hook/handler.js): break one
safety rule at a time in a temp copy, run the three test files against it, and count how many tests go red.
A mutant that leaves the suite green is a gap.

  python3 mutate_openclaw_ask.py
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
PICKUP, HOOK, INSTALL = "openclaw_pickup.py", "openclaw_ask_hook/handler.js", "openclaw_ask_install.sh"
DISPATCH = "openclaw_dispatch.py"
TESTS = ["test_state_guard", "test_openclaw_ask", "test_openclaw_ask_hook", "test_openclaw_ask_install", "test_openclaw_pickup", "test_openclaw_dispatch", "test_openclaw_reply", "test_openclaw_pass", "test_openclaw_lanes", "test_openclaw_nodrop", "test_openclaw_hublanes", "test_openclaw_queue", "test_reports_reach_him", "test_outbox_clean"]

MUTANTS = [
    # the outbox: one post at a time, never lost (19 Oct 4:59 PM and 5:03-5:24 PM)
    # one item per question, each short, and no flush after an outage (19 Oct 6:31 PM)
    # 19 Oct 11:10 PM: 10 of 20 waiting posts were acknowledgements, 6 repeats; his question about the chat sat behind the day's cap
    (DISPATCH, "clean: the hub-list acknowledgement is posted", "no pass: put on the hub's list (ask %s)\" % (stamp, ask_id), None", "no pass: put on the hub's list (ask %s)\" % (stamp, ask_id), \"On the hub's list: the hub has your message.\""),
    (DISPATCH, "clean: an acknowledgement is queued", '    if QUEUE_NOISE_RE.search(text or ""):\n        return None\n    with _queue_lock(path):', '    with _queue_lock(path):'),
    (DISPATCH, "clean: the same words wait twice", '        if any(not i["shown"] and _queue_same(i["text"], pieces[0]) for i in q["items"]):\n            return None\n', ''),
    (DISPATCH, "clean: old noise counts as waiting", '            if _queue_clean(q, now):', '            if False:'),
    (DISPATCH, "clean: old repeats count as waiting", 'repeat = not i["shown"] and any(_queue_same(i["text"], k["text"]) for k in keep)', 'repeat = False'),
    (DISPATCH, "clean: a lane report goes in parts", '        queue_add(path, text, now, key=key, whole=True)', '        queue_add(path, text, now)'),
    (DISPATCH, "clean: a rewritten report adds a second post", '        if key is not None:\n            old = next(', '        if False:\n            old = next('),
    (DISPATCH, "clean: a report is not shortened", '    while budget > 0 and len(out.split()) > limit:', '    budget = 10 ** 6\n    out = build(budget)\n    while False:'),
    (DISPATCH, "clean: a seen report is posted whole again", '                news = report_news(body, base) if base else body\n                if not news', '                news = body\n                if not news'),
    (DISPATCH, "clean: an unseen rewrite is posted as news only", '                base = rec.get("base") if waiting else rec.get("last")', '                base = rec.get("last")'),
    (DISPATCH, "clean: the sweep reposts what the lane already posted", '                answered_in_chat = True\n                line = "%s | report | %s finished; not posted: it already', '                line = "%s | report | %s finished; not posted: it already'),
    (DISPATCH, "clean: his chat question waits for the day's cap", 'day_cap=mail or not about_the_chat(question))', 'day_cap=True)'),
    (DISPATCH, "clean: no question waits for the day's cap", '    if today >= MAX_PER_DAY + (0 if day_cap else CHAT_EXTRA_PER_DAY):', '    if False:'),
    (DISPATCH, "clean: a chat question has no ceiling", '    if today >= MAX_PER_DAY + (0 if day_cap else CHAT_EXTRA_PER_DAY):', '    if today >= MAX_PER_DAY and day_cap:'),
    (DISPATCH, "clean: a mail question skips the cap by a chat word", 'day_cap=mail or not about_the_chat(question))', 'day_cap=not about_the_chat(question))'),
    (DISPATCH, "freeze: a long post goes out whole", '    if len(text) <= ITEM_MAX:\n        return [text]\n    pieces', '    if True:\n        return [text]\n    pieces'),
    (DISPATCH, "freeze: a huge post is dozens of nexts", '    if len(pieces) > QUEUE_MAX_PIECES:', '    if False:'),
    (DISPATCH, "freeze: the nudge is a bare count", 'ok = send(queue_nudge(waiting, nxt))', 'ok = send(queue_tail(waiting).strip())'),
    (DISPATCH, "freeze: a slow nudge is sent again", 'head["nudged"], nxt = now, q["items"][1]["text"]', 'nxt = q["items"][1]["text"]'),
    (DISPATCH, "freeze: held nexts skip a post", 'if why == "next" and q["items"] and q["items"][0]["shown"] and 0 <=', 'if False and q["items"] and q["items"][0]["shown"] and 0 <='),
    (DISPATCH, "items: the pump flushes a backlog", '        print(queue_pump(path, host.inject_now, now))', '        print([queue_next(path, host.inject_now, now) for _ in range(20)])'),
    (DISPATCH, "items: a post flushes the backlog", '        queue_pump(self.queue_path, self.inject_now, self.clock())\n        return True', '        [queue_next(self.queue_path, self.inject_now, self.clock()) for _ in range(20)]\n        return True'),
    (DISPATCH, "items: a repeat of the post showing goes out again", '            if head["shown"] and not (waiting', '            if False and head["shown"] and not (waiting'),
    (DISPATCH, "items: one answer may close many questions", '    if len(ids) > 1 and len(items) != len(ids):', '    if False:'),
    (DISPATCH, "items: no length cap", '    long = [n for n, p in enumerate(items, 1) if len(p) > ITEM_MAX]', '    long = []'),
    (DISPATCH, "items: say has no length cap", '    if len(text) > ITEM_MAX:\n        return False, "say: over', '    if False:\n        return False, "say: over'),
    (DISPATCH, "items: a multi-answer post is one chat item", '    for n, item in enumerate(items):\n        if not host.inject(item):', '    for n, item in enumerate(["\\n\\n".join(items)]):\n        if not host.inject(item):'),
    (DISPATCH, "items: the CLI post is not split", '        items = split_items(re.sub(', '        items = (lambda t: [t] if t else [])(re.sub('),
    (DISPATCH, "queue: a post that did not land is dropped", '                head["attempts"] += 1\n', '                q["items"].remove(head); head["attempts"] += 1\n'),
    (DISPATCH, "queue: a post that did not land counts as shown", "ok = bool(send(text + queue_tail(waiting)))", "ok = bool(send(text + queue_tail(waiting))) or True"),
    (DISPATCH, "queue: no pause between tries", 'head["next_try"] = now + QUEUE_BACKOFF[', 'head["next_try"] = now + 0 * QUEUE_BACKOFF['),
    (DISPATCH, "queue: the next post shows while one is up", '                return "showing"\n            if not head["shown"]', '                pass\n            if not head["shown"]'),
    (DISPATCH, "queue: posts do not say how many wait", 'return "\\n\\n(%d more waiting. Say next for the next one.)" % waiting', 'return "\\n\\n(Say next.)"'),
    (DISPATCH, "queue: a burst is not said to be waiting", "        if nudge:", "        if False:"),
    (DISPATCH, "queue: a failing post never wakes the hub", "now - since < QUEUE_WAKE_AFTER", "True"),
    (DISPATCH, "queue: the hub is woken on every poll", '(head.get("woke") and now - head["woke"] < QUEUE_REWAKE)', "False"),
    (DISPATCH, "queue: an unreadable queue is written over", "            p.replace(aside)\n", "            pass\n"),
    (DISPATCH, "queue: a post can be sent twice at once", '    with _queue_lock(path, "pump", block=False) as mine:\n        if not mine:', '    with _queue_lock(path, "pump", block=False) as mine:\n        if False:'),
    (DISPATCH, "queue: the CLI posts unqueued", "def queued(host):\n", "def queued(host):\n    return host\n"),
    (DISPATCH, "queue: a live test posts into his outbox", 'return p.with_name(p.stem + ".chat-queue.json")', 'return STATE / "openclaw-chat-queue.json"'),
    (PICKUP, "queue: the poller does not retry", "        dispatch.queue_pump(outbox, host.inject_now, now)\n", "        pass\n"),
    (PICKUP, "queue: next is not seen", "    if dispatch.NEXT_RE.match(item[\"text\"]):", "    if False:"),
    (PICKUP, "queue: his answer does not move on", '    dispatch.queue_next(outbox, host.inject_now, now, "answered")\n', "    pass\n"),
    (PICKUP, "queue: next also runs as a message", "            done.append((\"queue\", moved))\n            continue\n", "            done.append((\"queue\", moved))\n"),
    (DISPATCH, "queue: a next line counts as a drop", "| \\| next \\| \")),", "\")),"),
    # the guard: who may reach the action table
    (PICKUP, "source guard off (any source acts)", 'if item.get("source") != ACT_SOURCE or "text" not in item:', 'if "text" not in item:'),
    (PICKUP, "discord also counts as tray", 'if item.get("source") != ACT_SOURCE or', 'if item.get("source") not in (ACT_SOURCE, "discord") or'),
    (PICKUP, "twitch also counts as tray", 'if item.get("source") != ACT_SOURCE or', 'if item.get("source") not in (ACT_SOURCE, "twitch") or'),
    (PICKUP, "youtube also counts as tray", 'if item.get("source") != ACT_SOURCE or', 'if item.get("source") not in (ACT_SOURCE, "youtube") or'),
    (PICKUP, "source compared loosely (case/space)", 'if item.get("source") != ACT_SOURCE or', 'if str(item.get("source")).strip().lower() != ACT_SOURCE or'),
    (PICKUP, "rejected/duplicate items are acted on", 'if r["verdict"] != "accept":\n            continue', 'if False:\n            continue'),
    # the menu: what text may become an action
    (PICKUP, "menu matches a prefix, not the whole message", "rx.fullmatch(t)", "rx.match(t)"),
    (PICKUP, "status matches by substring", "if t in STATUS_FORMS:", "if any(f in t for f in STATUS_FORMS):"),
    (PICKUP, "alias to a site not on disk accepted", "and s in sites}", "}"),
    (PICKUP, "noise folders become sites", "if NOISE_SITE.match(d.name) or not ALIAS_RE.match(d.name.lower()):", "if not ALIAS_RE.match(d.name.lower()):"),
    (PICKUP, "symlinked site folders followed", "if p.is_dir() and not p.is_symlink()", "if p.is_dir()"),
    (PICKUP, "odd file names allowed", "or not SAFE_FILE.match(f.name):", ":"),
    (PICKUP, "blank.skp can be the newest", 'or f.name.lower() == "blank.skp"', ""),
    (PICKUP, "oldest model picked, not newest", "if best is None or m > best[0]:", "if best is None or m < best[0]:"),
    (PICKUP, "stale asks still run", 'fresh = "ts" in item and abs(now - item["ts"]) <= ACT_MAX_AGE', "fresh = True"),
    (PICKUP, "ts accepts any type", "type(ts) is not int or not 0 < ts < 4_000_000_000", "False"),
    # the PC side
    (PICKUP, "welcome closer never run", "        for pid in sorted(new):\n            host.close_welcome(pid)", "        pass"),
    (PICKUP, "welcome closer only on the first new pid", "        for pid in sorted(new):\n            host.close_welcome(pid)", "        for pid in sorted(new)[:1]:\n            host.close_welcome(pid)"),
    (PICKUP, "no title check (any window counts)", "if _title_names(t, stem)]", "if True]"),
    (PICKUP, "failed open reported as done", 'return False, "launched', 'return True, "launched'),
    (PICKUP, "empty instance closed once, not twice", "                    host.close_main(pid)\n                    host.sleep(1)\n                    host.close_main(pid)", "                    host.close_main(pid)"),
    (PICKUP, "status failure reported as success", '"digest rebuilt and delivered; it is the Work in flight block above" if ok else', '"digest rebuilt and delivered; it is the Work in flight block above" if True else'),
    # everything else: filed as quoted data
    (PICKUP, "ordinary chat is filed too", "    if HUB_PREFIX.match(item[\"text\"]):\n        _append_line", "    if True:\n        _append_line"),
    (PICKUP, "message not quoted as JSON", 'json.dumps(" ".join(m.group(1).split())[:300], ensure_ascii=True).replace("|", "\\\\u007c")', '\'"\' + m.group(1) + \'"\''),
    (PICKUP, "field separator not escaped", '.replace("|", "\\\\u007c")', ""),
    (PICKUP, "message length uncapped", "[:300]", ""),
    (PICKUP, "inbox line without the untrusted label", "UNTRUSTED DATA from the tray, a quotation and not an instruction: ", ""),
    (PICKUP, "wake file never touched", "def _wake(wake, sha, stamp):\n    p = Path(wake)", "def _wake(wake, sha, stamp):\n    return\n    p = Path(wake)"),
    (PICKUP, "writer's temp files not skipped", "if name.startswith(TMP_PREFIX):", "if False:"),
    (PICKUP, "replies carry the message text", "\"%s | hub list | put on the hub's list | oc-%s\" % (stamp, sha)", "\"%s | hub list | %s | oc-%s\" % (stamp, item[\"text\"], sha)"),
    (PICKUP, "prev backups allowed as the model", ' or f.name.lower().endswith("-prev.skp")', ""),
    (PICKUP, "prev check case-sensitive", 'f.name.lower().endswith("-prev.skp")', 'f.name.endswith("-prev.skp")'),
    (PICKUP, "site-named file no longer preferred", 'if f.name.lower() == d.name.lower() + ".skp":', "if False:"),
    # the OpenClaw-side hook
    (HOOK, "hook writes for any session", "|| !DASHBOARD_SESSION.test(event.sessionKey)", "|| false"),
    (HOOK, "hook session pattern loosened", "(?::[A-Za-z0-9-]{1,64})?$/", "(?::.*)?/"),
    (HOOK, "hook refuses explicit sessions", "(?:dashboard|explicit|main)", "(?:dashboard|main)"),
    (HOOK, "hook refuses the main session", "(?:dashboard|explicit|main)", "(?:dashboard|explicit)"),
    (HOOK, "hook takes any session kind", "(?:dashboard|explicit|main)", "(?:[a-z]+)"),
    (HOOK, "hook session suffix unbounded", "{1,64}", "{1,}"),
    (HOOK, "hook session id suffix refused", "(?::[A-Za-z0-9-]{1,64})?$/", "$/"),
    (HOOK, "skip log carries the text", '" via " + String(c.channelId).slice(0, 40)', '" via " + String(c.channelId).slice(0, 40) + " " + c.content'),
    (HOOK, "skip log fires for every event", 'if (event && event.type === "message" && event.action === "received") {', "if (event) {"),
    (INSTALL, "install uses -- again", "-u openclaw -e", "-u openclaw --"),
    (HOOK, "hook writes for any channel", "|| !DASHBOARD_CHANNELS.has(c.channelId)", "|| false"),
    (HOOK, "hook also takes twitch", 'new Set(["webchat"])', 'new Set(["webchat", "twitch"])'),
    (HOOK, "hook flood cap off", "if (recent.length >= PER_MINUTE) return;", ""),
    (HOOK, "hook text length uncapped", "|| text.length > MAX_TEXT", ""),
    (HOOK, "hook leaves control characters in", "const text = c.content.replace(/[\\u0000-\\u0008\\u000b-\\u001f\\u007f]/g, \" \").trim();", "const text = c.content.trim();"),
    (HOOK, "hook source is whatever the event says", 'source: "tray"', "source: c.channelId"),
    # the lane menu (openclaw_dispatch.py): where repos and lanes come from, the budget, the engine, the one chat
    (DISPATCH, "repo taken from the text, not the table", "hit = ctx.repos.get(repo_word.lower())", 'hit = ctx.repos.get(repo_word.lower()) or (repo_word, "/tmp/" + repo_word, "")'),
    (DISPATCH, "lane taken from the text, not the state file (reply)", "    name = hit[0] if hit else None\n    if not name:", "    name = lane_word\n    if not name:"),
    (DISPATCH, "symlinked repo folders listed", "not d.is_symlink() and", ""),
    (DISPATCH, "folder without .git listed", ' and (d / ".git").exists()', ""),
    (DISPATCH, "running-lane cap off", "if len(running) >= MAX_RUNNING:", "if False:"),
    (DISPATCH, "running-lane cap off by one", "if len(running) >= MAX_RUNNING:", "if len(running) > MAX_RUNNING:"),
    (DISPATCH, "daily cap off", "if today >= MAX_PER_DAY + (0 if day_cap else CHAT_EXTRA_PER_DAY):", "if False:"),
    (DISPATCH, "daily cap off by one", "if today >= MAX_PER_DAY + (0 if day_cap else CHAT_EXTRA_PER_DAY):", "if today > MAX_PER_DAY + (0 if day_cap else CHAT_EXTRA_PER_DAY):"),
    (DISPATCH, "Opus allowed past the budget limit", "        if over is not None:\n            if model == \"opus\":", "        if False:\n            if model == \"opus\":"),
    (DISPATCH, "budget note dropped for Sonnet", "            note = \" (Sonnet only: %s)\" % over", "            pass"),
    (DISPATCH, "projection limit raised to 100", "PROJECTION_LIMIT = 90.0", "PROJECTION_LIMIT = 100.0"),
    (DISPATCH, "used limit raised to 100", "USED_LIMIT = 95.0", "USED_LIMIT = 100.0"),
    (DISPATCH, "phase 2 holds only past the used limit", "over = pct is not None and pct >= limit", "over = pct is not None and pct > limit"),
    (DISPATCH, "phase 2 starts an hour late", "PHASE2_START = (0, 6, 0)", "PHASE2_START = (0, 7, 0)"),
    (DISPATCH, "phase 3 starts a day late", "PHASE3_START = (1, 6, 0)", "PHASE3_START = (2, 6, 0)"),
    (DISPATCH, "unknown budget never holds the room", "if held else None", "if False else None"),
    (DISPATCH, "room ignores the phase and holds on the projection", "reason = budget_over(budget, now, held=True)", "reason = budget_over(budget, 1_800_000_000, held=True)"),
    (DISPATCH, "cache from before the reset is trusted", "and not (isinstance(reset, (int, float)) and reset <= now)", ""),
    (DISPATCH, "client list unreadable counts as own", 'return "the client lists cannot be read"', "return None"),
    (DISPATCH, "client-senders list ignored", 'if len(f) > 1 and f[1].strip() and os.path.realpath(_expand(f[1])) == real:', "if False:"),
    (DISPATCH, "CD tree not checked", "if real == cd or real.startswith(cd + os.sep):", "if False:"),
    (DISPATCH, "foreign owner counts as own", "if not m or m.group(1).lower() != OWNER:", "if False:"),
    (DISPATCH, "client repos go to Opus always", "if JUDGMENT_RE.search(task):", "if True:"),
    (DISPATCH, "Docker work goes to Codex", "if DOCKER_RE.search(task) or", "if"),
    (DISPATCH, "Codex block ignored", 'state.get("codex_blocked_until", 0) > now', "False"),
    (DISPATCH, "Copilot block ignored", 'state.get("copilot_blocked_until", 0) > now', "False"),
    (DISPATCH, "pass start ignores the blocks", "engine, _why, note = resolve_engine(state, engine, now)", 'note = ""'),
    (DISPATCH, "waiting room ignores the blocks", "engine, _why, blocked_note = resolve_engine(state, engine, now)", 'blocked_note = ""'),
    (DISPATCH, "monthly quota blocks only 12 hours", "if MONTHLY_QUOTA_RE.search(tail):", "if False:"),
    (DISPATCH, "a later block is shortened", "state[key] = max(state.get(key, 0), until)", "state[key] = until"),
    (DISPATCH, "close without a report", 'if lane["status"] == "started" and not has_report:', "if False:"),
    (DISPATCH, "reply to a closed lane", 'closed_now = lane is None or lane.get("status") not in ("started", "reported")', 'closed_now = lane is None or lane.get("status") not in ("started", "reported", "closed")'),
    (DISPATCH, "empty report counts as reported", 'if rep.exists() and rep.stat().st_size > 0 and rep.stat().st_mtime', 'if rep.exists() and rep.stat().st_mtime'),
    (DISPATCH, "lost window never counts as dead", 'now - max(lane["started"], lane.get("reopened_at", 0)) > DEAD_GRACE', "False"),
    (DISPATCH, "reply sent bare, without the frame", "REPLY_FRAME + text.strip()", "text.strip()"),
    (DISPATCH, "report posted unquoted", '"> " + l if l.strip() else ">" for l in', 'l for l in'),
    (DISPATCH, "answers go to another session", 'MAIN_SESSION = "agent:main:main"', 'MAIN_SESSION = "agent:main:explicit:hub"'),
    (DISPATCH, "answers carry a session-key flag", "chat.inject --json --params", "chat.inject --session-key x --json --params"),
    (PICKUP, "lane menu acts on stale asks", "if lane_ask and fresh:", "if lane_ask:"),
    # the reply-to-his-message check (no yes: the guards live here)
    (PICKUP, "his message matched by time only (text ignored)", 'if m["role"] != "user" or _norm(m["text"]) != want:', 'if m["role"] != "user":'),
    (PICKUP, "his message matched by text only (time ignored)", "if d <= MATCH_TOL and (best is None or d < best[0]):", "if best is None or d < best[0]:"),
    (PICKUP, "his message = the newest message of his", "if d <= MATCH_TOL and (best is None or d < best[0]):", "if True:"),
    (PICKUP, "a hub line after his message is skipped, not a stop", '            return "none", "a hub line, not the model, came right after his message"', "            continue"),
    (PICKUP, "provider label no longer checked", 'm["provider"] != "openclaw" and not m["text"].lstrip()', 'not m["text"].lstrip()'),
    (PICKUP, "[hub] text no longer checked", ' and not m["text"].lstrip().startswith("[hub]")', ""),
    (PICKUP, "the reply runs on past his next message", '        if m["role"] == "user":\n            break\n', ""),
    (PICKUP, "two different markers allowed", "    if len(jobs) != 1:", "    if not jobs:"),
    (PICKUP, "two markers on one line allowed", "    if any(len(MARKER_ANY.findall(l)) != 1 for l in lines):", "    if False:"),
    (PICKUP, "a marker counts only as the last line", "if MARKER_START.match(l)]", "if MARKER_START.match(l)][-1:] if MARKER_START.match(run[-1][\"text\"].splitlines()[-1] if run and run[-1][\"text\"].splitlines() else \"\") else []"),
    # no message ends undelivered (19 Oct)
    (DISPATCH, "a refusal is not put on the hub's list", "    filed = hub_has_it(ctx, ask_id, words or \"\", now, why[:200], target_lane)\n", "    return \"hub\", line\n"),
    (DISPATCH, "a reply to a closed lane is refused", "            got = restart_lane(ctx, host, old, text, now, ask_id, context)", "            got = None"),
    (PICKUP, "same start can run twice", "if verb == \"start\" and via_chat and dispatch.duplicate_start(ctx, menu[2], now):", "if False:"),
    (PICKUP, "unanswered messages are dropped at once", '        if status == "wait":\n            waiting.append(e)\n            continue', '        if status == "wait":\n            continue'),
    (PICKUP, "waiting entries never expire", 'live = [e for e in queue if now - e["ts"] <= ACT_MAX_AGE]', "live = queue"),
    (PICKUP, "unreadable session loses the message", "                waiting.append(e)  # unreadable: try again next poll", "                pass  # unreadable: try again next poll"),
    (PICKUP, "missing message always waits", 'young = now - item["ts"] <= REPLY_WAIT', "young = True"),
    (PICKUP, "chat reply acted on without being queued by tray gate", 'if kind == "chat" and ctx is not None and type(item.get("ts")) in (int, float):', 'if kind == "chat" and ctx is not None:'),
    # self-closing lanes, honest failures, Copilot, stop
    (DISPATCH, "a finished Claude lane's window is killed", 'if wins and name in wins and lane.get("engine", "claude") != "claude":', "if wins and name in wins:"),
    (DISPATCH, "delivered lane stays open in state", '        lane["status"], lane["closed_at"] = "closed", now\n        lane["report_seen"]', '        lane["report_seen"]'),
    (DISPATCH, "failure needs only one of the two signs", "if not (BLOCKED_RE.search(flat) and NOTHING_RE.search(flat)):", "if not (BLOCKED_RE.search(flat) or NOTHING_RE.search(flat)):"),
    (DISPATCH, "failed report announced as finished", '"%s failed: %s (repo %s)." % (name, json.dumps(failure), lane["repo"]) if failure else', '"%s failed: %s (repo %s)." % (name, json.dumps(failure), lane["repo"]) if False else'),
    (DISPATCH, "detector never sees a failure", "    if not (BLOCKED_RE.search(flat) and NOTHING_RE.search(flat)):\n        return None", "    return None"),
    (DISPATCH, "denied no longer counts as blocked", r"\bdenied\b|\bblocked\b|", r"\bblocked\b|"),
    # ("could not complete" is also caught by the wider "not complete" wording added for partial reports, so dropping its old line changes nothing)
    (DISPATCH, "stuck prompt posted every poll", "        if lane.get(\"stuck\") == first:", "        if False:"),
    (DISPATCH, "lane started without the unattended flag", "env=dict(os.environ, AFA_LANE_UNATTENDED=\"1\")", "env=None"),
    (DISPATCH, "copilot told to use a worktree", 'work_rule = COPILOT_WORK_RULE.format(hand=hand) if engine == "copilot" else WORK_RULE', "work_rule = WORK_RULE"),
    (DISPATCH, "stop closes the oldest lane", "name = max(open_)[1]", "name = min(open_)[1]"),
    (DISPATCH, "stop matches long sentences", " and len(t) <= 20:", ":"),
    (DISPATCH, "undeliverable note still closes the lane", "        if not host.inject(chat):", "        if False:"),
    # the dispatch pass (frontdesk-4): the routing branch, the fallback deadline, claim / say, caps, dedup, partial = failed
    (PICKUP, "routing: jobs skip the pass and the script starts them", 'if verb == "start" and ctx.hand_off_first:', "if False:"),
    (PICKUP, "routing: every verb goes to the pass", 'if verb == "start" and ctx.hand_off_first:', "if ctx.hand_off_first:"),
    (PICKUP, "poller never runs the fallback", " + dispatch.fallback_due(ctx, host, now)\n                + dispatch.room_due", "\n                + dispatch.room_due"),
    (DISPATCH, "fallback: the deadline is ignored", 'elif now >= a.get("deadline", 0):', "elif False:"),
    (DISPATCH, "fallback: one second late", 'elif now >= a.get("deadline", 0):', 'elif now > a.get("deadline", 0):'),
    (DISPATCH, "fallback: wait stretched to 50 minutes", "ASK_WAIT = 5 * 60", "ASK_WAIT = 50 * 60"),
    (DISPATCH, "fallback: a failed pass waits out the deadline", 'if "exit" in p and p["exit"] != 0:', "if False:"),
    (DISPATCH, "fallback: a pass that ended unsettled waits out the deadline", 'elif "exit" in p:', "elif False:"),
    (DISPATCH, "fallback: also starts asks already claimed", 'if a.get("status") != "pending":\n                continue', "if False:\n                continue"),
    (DISPATCH, "fallback: ask not marked before the start (a late claim runs twice)", 'a["status"] = "fallback"  # before do_start', "pass  # before do_start"),
    (DISPATCH, "fallback: not said in the chat", 'host.inject(chat)\n        out.append((line.replace', "out.append((line.replace"),
    (DISPATCH, "pass timeout longer than the wait", "PASS_TIMEOUT = 240", "PASS_TIMEOUT = 340"),
    (DISPATCH, "claim: a settled ask can still be claimed", 'if ask["status"] != "pending" and not reclaim:', 'if False:'),
    (DISPATCH, "claim: lane need not be in the index", 'if row is None:\n            return False, "claim: lane', 'if False:\n            return False, "claim: lane'),
    (DISPATCH, "claim: does not cancel the fallback", 'ask["status"] = "claimed"', "pass"),
    (DISPATCH, "claim: lane not registered for the sweep", 'if lane_name not in state["lanes"]:\n            state["lanes"][lane_name] = {', 'if False:\n            state["lanes"][lane_name] = {'),
    (DISPATCH, "claim: bad lane name accepted", "not LANE_NAME_RE.fullmatch(lane_name or \"\")", "False"),
    (DISPATCH, "say: a settled ask can be answered again", 'if ask["status"] not in ("pending", "fallback"):  # fallback', 'if False:  # fallback'),
    (DISPATCH, "say: does not close the ask", 'ask["status"], ask["answered_at"] = "answered", now', 'ask["answered_at"] = now'),
    (DISPATCH, "say: ask closed even when the chat is unreachable", "if not host.inject(text):", "if False:"),
    (DISPATCH, "caps: waiting asks not counted against the running cap", "if len(running) + len(waiting) >= MAX_RUNNING:", "if len(running) >= MAX_RUNNING:"),
    (DISPATCH, "caps: the pass has no cap", "if len(running) + len(waiting) >= MAX_RUNNING:", "if False:"),
    (DISPATCH, "hand-off: same ask handed over twice", "if ask_id in asks:\n            return", "if False:\n            return"),
    (DISPATCH, "hand-off: spawn failure not recorded", '["pass"] = {"exit": -1, "error": "could not start the pass"}', '["pass"] = None'),
    (DISPATCH, "dedup: keyed on case and spacing", '" ".join(task.split()).lower().encode', '" ".join(task.split()).encode'),
    (DISPATCH, "dedup: window ignored (asks)", 'now - a.get("created", 0) < DEDUP_WINDOW', "True"),
    (DISPATCH, "dedup: window ignored (lanes)", 'now - l.get("started", 0) < DEDUP_WINDOW', "True"),
    (DISPATCH, "dedup: a failed ask can never be retried", "and not _ask_failed(state, a) and not a.get(\"lane_failed_of\"):", ":"),
    (DISPATCH, "dedup: a failed lane can never be retried", 'and not l.get("failed"):', ":"),
    (DISPATCH, "dedup: a stopped lane cannot be retried", '["failed"] = "closed", True, True', '["failed"] = "closed", True, False'),
    (DISPATCH, "dedup: a dead lane not marked failed", 'lane["detail"], lane["failed"] = detail, True', 'lane["detail"] = detail'),
    (DISPATCH, "dedup: a failed report not marked failed", 'if failure:\n                lane["failed"], changed = True, True', 'if failure:\n                changed = True'),
    (DISPATCH, "dedup: a failed fallback start cannot be retried", 'asks_of(state)[k]["status"] = "fallback-failed"', 'asks_of(state)[k]["status"] = "fallback"'),
    (DISPATCH, "partial: 'not a complete' is not undone", r"\bnot (?:an? )?(?:complete|full|exact|exhaustive)\b|", ""),
    (DISPATCH, "partial: the word partial is not undone", r"\bpartial(?:ly)?\b|", ""),
    (DISPATCH, "partial: 'needs read access' is not undone", r"needs? (?:\w+ ){0,2}(?:read |write )?access|", ""),
    (DISPATCH, "partial: 'to complete the exact request' is not undone", r"to complete the (?:exact |full )?(?:request|task)|", ""),
    (DISPATCH, "partial: 'instead of the local' is not undone", r"instead of (?:the )?(?:local|requested)", r"instead of (?:the )?(?:requested)"),
    (DISPATCH, "pass: the skip flag is ignored", 'if flag == "skip":', "if False:"),
    (DISPATCH, "pass: the fail flag is ignored", 'if flag == "fail":', "if False:"),
    (DISPATCH, "pass: tokens not recorded", '"tokens": toks', '"tokens": 0'),
    (DISPATCH, "pass: runs for an ask already settled", 'if ask is None or ask.get("status") != "pending":', "if ask is None:"),
    (DISPATCH, "pass: the lock is never taken", "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "pass"),
    (DISPATCH, "prompt: any lane name may be written", 'or not lane_name.startswith("oc-"):\n        return False, "prompt:', ':\n        return False, "prompt:'),
    (DISPATCH, "prompt: lane name unchecked", "if not LANE_NAME_RE.fullmatch(lane_name or \"\") or not lane_name.startswith", "if not lane_name.startswith"),
    (DISPATCH, "prompt: size unchecked", "or len(text) > PROMPT_MAX:", ":"),
    (DISPATCH, "pass: the task is not quoted as data", "task=json.dumps(ask[\"task\"], ensure_ascii=False), repo=ask[\"repo\"]", "task=ask[\"task\"], repo=ask[\"repo\"]"),
    # frontdesk-6: the cap counts real work only, the refusal names its cause
    (DISPATCH, "cap: a test start counts toward the day (script start)", 'if is_test():\n        state["lanes"][lane]["test"] = True\n    else:\n        state["starts"].append(now)',
     'state["starts"].append(now)\n    if is_test():\n        state["lanes"][lane]["test"] = True'),
    (DISPATCH, "cap: a test start counts toward the day (claim)", 'if is_test():\n                state["lanes"][lane_name]["test"] = True\n            else:\n                state["starts"].append(now)',
     'state["starts"].append(now)\n            if is_test():\n                state["lanes"][lane_name]["test"] = True'),
    (DISPATCH, "cap: the test flag is never seen", 'return os.environ.get(TEST_ENV, "") not in ("", "0")', "return False"),
    (DISPATCH, "cap: AFA_OC_TEST=0 counts as a test", 'return os.environ.get(TEST_ENV, "") not in ("", "0")', 'return os.environ.get(TEST_ENV, "") != ""'),
    (DISPATCH, "cap: a failed report never gives its start back", 'state["starts"].remove(lane["started"])\n    lane["refunded"] = True\n    return True', "return False"),
    (DISPATCH, "cap: the start is given back however late the report fails", 'if not 0 <= wrote - lane["started"] <= FAIL_REFUND or lane["started"] not in state["starts"]:', 'if lane["started"] not in state["starts"]:'),
    (DISPATCH, "cap: the start is given back only for a report within 4 minutes", "FAIL_REFUND = 5 * 60", "FAIL_REFUND = 4 * 60"),
    (DISPATCH, "cap: the start is given back more than once", 'if lane.get("refunded") or lane.get("test") or not isinstance', 'if lane.get("test") or not isinstance'),
    (DISPATCH, "cap: the start is given back for any report, not only a failed one", "            if failure:\n                lane[\"failed\"], changed = True, True\n                if _refund_start(state, lane, now):",
     "            if True:\n                lane[\"failed\"], changed = True, True\n                if _refund_start(state, lane, now):"),
    (DISPATCH, "cap: a written time before the start is given back", "if not 0 <= wrote", "if not -999999 <= wrote"),
    (DISPATCH, "refusal: the day-cap text drops its cause", '"This is not a Gemini or API limit." % (MAX_PER_DAY, _frees_at(now)))', '"" % (MAX_PER_DAY, _frees_at(now)))'),
    (DISPATCH, "refusal: the running-cap text drops its cause", '"being set up. Frees when one reports. This is not a Gemini or API limit." % (MAX_RUNNING, running))', '"being set up. Frees when one reports." % (MAX_RUNNING, running))'),
    (DISPATCH, "refusal: frees at the wrong midnight", "t.tm_mday + 1", "t.tm_mday"),
    (DISPATCH, "refusal: the day cap is named as a limit of someone else", "own safety cap: %d starts a day", "usage limit: %d starts a day"),
    # frontdesk-6: the pass's `start` and `claim`
    (DISPATCH, "pass start: the running cap is not applied", "if len(running) + len(beginning) >= MAX_RUNNING:\n            return False, \"start: refused: \"", "if False:\n            return False, \"start: refused: \""),
    (DISPATCH, "pass start: lanes being started are not counted", "if len(running) + len(beginning) >= MAX_RUNNING:\n            return False, \"start: refused: \"", "if len(running) >= MAX_RUNNING:\n            return False, \"start: refused: \""),
    (DISPATCH, "pass start: the prompt need not be the saved one", "    if not _prompt_ok(ctx, lane_name, prompt_path, now):", "    if False:"),
    (DISPATCH, "pass start: a second lane for one ask", "        if ask.get(\"starting\"):", "        if False:"),
    (DISPATCH, "pass start: a client repo may run on Codex", 'if engine != "claude" and (why or name == WORKSPACE):', "if False:"),
    (DISPATCH, "pass start: engine and model words unchecked", "if engine not in ENGINES or model not in MODELS:", "if False:"),
    (DISPATCH, "pass start: any folder will do", "    if hit is None and parent is None:\n        return \"%s is not a repo on the list", "    if False:\n        return \"%s is not a repo on the list"),
    (DISPATCH, "pass start: a relative folder will do", "    if not os.path.isabs(d):", "    if False:"),
    (DISPATCH, "claim: a lane the pass did not start for that ask", 'if not isinstance(begun, dict) or begun.get("lane") != lane_name:', "if False:"),
    (DISPATCH, "claim: a start from long ago", "if not 0 <= now - begun.get(\"at\", 0) <= CLAIM_WINDOW:", "if False:"),
    (DISPATCH, "claim: window stretched to a day", "CLAIM_WINDOW = 10 * 60", "CLAIM_WINDOW = 24 * 3600"),
    (DISPATCH, "claim: a claimed ask can be claimed again", 'if ask["status"] != "pending" and not reclaim:', 'if ask["status"] not in ("pending", "claimed") and not reclaim:'),
    (DISPATCH, "pass verbs: the pass may claim", 'PASS_VERBS = ("prompt", "say", "start", "approve")', 'PASS_VERBS = ("prompt", "say", "start", "approve", "claim")'),
    (DISPATCH, "pass verbs: the pass may post", 'PASS_VERBS = ("prompt", "say", "start", "approve")', 'PASS_VERBS = ("prompt", "say", "start", "approve", "post")'),
    (DISPATCH, "post: the pass may post", 'if os.environ.get("AFA_OC_PASS"):\n        return False, "post:', 'if False:\n        return False, "post:'),
    # frontdesk-6: the waiting room
    (DISPATCH, "room: an entry nobody approved starts", 'if not h.get("approved") or h.get("start-failed"):\n            continue', 'if h.get("start-failed"):\n            continue'),
    (DISPATCH, "room: an entry that did not start is retried every run", 'if not h.get("approved") or h.get("start-failed"):\n            continue', 'if not h.get("approved"):\n            continue'),
    (DISPATCH, "room: starts while its after-lane is still running", 'if st == "waiting":\n                continue', "if False:\n                continue"),
    (DISPATCH, "room: starts after a failed after-lane", 'if st == "failed":\n                with ctx.locked():', 'if False:\n                with ctx.locked():'),
    (DISPATCH, "room: the stays-queued notice repeats every run", 'if seen.get(e["name"]) == h["after"]:\n                        continue', 'if False:\n                        continue'),
    (DISPATCH, "room: the stays-queued notice is never recorded", 'seen[e["name"]] = h["after"]', "pass"),
    (DISPATCH, "room: a started entry can start twice", "        if name in started:", "        if False:"),
    (DISPATCH, "room: the start is not remembered", 'started[name] = {"at": now, "after": h.get("after")}', "pass"),
    (DISPATCH, "room: the started entry file stays", 'entry["path"].unlink()\n    ok, msg = host.start_lane(', "ok, msg = host.start_lane("),
    (DISPATCH, "room: the lane gets the header lines in its brief", 'prompt.write_text(entry["brief"].rstrip("\\n") + "\\n")', 'prompt.write_text(entry["path"].read_text())'),
    (DISPATCH, "room: the started line does not say what it waited for", 'behind = " (was queued behind %s)" % h["after"] if h.get("after") else ""', 'behind = ""'),
    (DISPATCH, "room: an empty brief starts", 'if problem or not entry["brief"].strip():', "if problem:"),
    (DISPATCH, "room: a folder off the list starts", 'problem, hit = vet_start(ctx, h.get("dir", ""), engine, model)', 'problem, hit = None, ("x", h.get("dir", ""), "")'),
    (DISPATCH, "room: a failed start is not kept in the room", 'entry["path"].write_text("\\n".join("%s: %s" % kv for kv in h.items() if kv[0] != "start-failed")', 'entry["path"].write_text("\\n".join("%s: %s" % kv for kv in h.items() if kv[0] != "dir")'),
    (DISPATCH, "room: an empty report counts as finished", 'if not body:\n        return "waiting", None', "if False:\n        return \"waiting\", None"),
    (DISPATCH, "room: a failed report counts as finished", 'return ("failed", why) if why else ("done", None)', 'return ("done", None)'),
    (DISPATCH, "room: the after-lane is found in another lane's folder", 'glob("????-??-??-%s/report.md" % after)', 'glob("????-??-??-*%s*/report.md" % after)'),
    (DISPATCH, "room: approve accepts words that do not name the entry", 'if not re.search(r"(?<![\\w-])%s(?![\\w-])" % re.escape(entry["name"]), ask.get("task", ""), re.I):', "if False:"),
    (DISPATCH, "room: approve accepts a part of a longer name", '(?<![\\w-])%s(?![\\w-])" % re.escape(entry["name"])', '%s" % re.escape(entry["name"])'),
    (DISPATCH, "room: approve leaves the ask open", 'ask["status"], ask["answered_at"], ask["room"] = "answered", now, entry["name"]', 'ask["room"] = entry["name"]'),
    (DISPATCH, "room: approve on a settled ask", 'if ask["status"] != "pending":\n            return False, "approve:', 'if False:\n            return False, "approve:'),
    (DISPATCH, "room: approve writes some other approval", '"Jonathan via receptionist, %s"', '"auto, %s"'),
    (DISPATCH, "room: queued answer misses 'by itself'", "it starts by itself when that finishes", "it will start"),
    (DISPATCH, "room: approve starts it now even when its after-lane is running", 'if st == "waiting":\n        chat = "%s is queued behind', 'if False:\n        chat = "%s is queued behind'),
    (PICKUP, "room: the pickup run never starts the room", "\n                + dispatch.room_due(ctx, host, now))", ")"),
    # frontdesk-7: lanes are found by any name they have, listed live, and stopped
    (DISPATCH, "find: an exact name does not win", "    if exact:\n        return exact[:1]", "    if False:\n        return exact[:1]"),
    (DISPATCH, "find: the ask id finds nothing", "    if ASK_ID_RE.fullmatch(w):\n        ask =", "    if False:\n        ask ="),
    (DISPATCH, "find: the suffix finds nothing", "    if SUFFIX_RE.fullmatch(w):\n        hit =", "    if False:\n        hit ="),
    (DISPATCH, "find: a suffix matches inside a name", 'n.lower().endswith("-" + w)', "w in n.lower()"),
    (DISPATCH, "find: a repo word finds closed lanes too", 'l.get("status") in ("started", "reported") and l.get("repo"', 'True and l.get("repo"'),
    (DISPATCH, "resolve: an ambiguous word picks the first", "return hit[0] if len(hit) == 1 else None", "return hit[0] if hit else None"),
    (DISPATCH, "close: an ambiguous word closes the first", "    if len(hit) > 1:\n        return \"%s | close | couldn't:", "    if False:\n        return \"%s | close | couldn't:"),
    (DISPATCH, "close: a running lane is not stopped", 'if lane["status"] == "started" and not has_report:', "if False:"),
    (DISPATCH, "close: a missing lane does not list the open ones", '_open_lane_names(load_state(ctx.state_path), live))', "'none right now')"),
    (DISPATCH, "claim: the start line is not posted", "    if host is not None and started:", "    if False:"),
    (DISPATCH, "claim: the start line drops how to stop", "started = \"%s%s Say stop to cancel. (%s on %s;", "started = \"%s%s (%s on %s;"),
    (DISPATCH, "lanes: a closed lane is listed", 'if l.get("status") != "closed"} | set(wins or ())', "} | set(wins or ())"),
    (DISPATCH, "lanes: a lane with no window is called running", 'st = "dead" if wins is not None and name not in wins else "running"', 'st = "running"'),
    (DISPATCH, "lanes: a failed report is called written", 'st = "failed" if lane.get("failed") or report_failure(body) else "report written"', 'st = "report written"'),
    (DISPATCH, "reports: a report is posted again on every run", "if posted.get(p) == mtime or now - mtime < REPORT_QUIET:", "if now - mtime < REPORT_QUIET:"),
    (DISPATCH, "reports: the first run posts old reports", "if seed or first:", "if seed:"),
    (DISPATCH, "reports: a report still being written is posted", "now - mtime < REPORT_QUIET:", "False:"),
    (DISPATCH, "reports: a failed post is recorded as sent", "news, p), key, now):\n                    posted[p], texts[p]", "news, p), key, now) or True:\n                    posted[p], texts[p]"),
    (DISPATCH, "reports: a dispatcher lane is posted twice", "                if name in own:", "                if False:"),
    (DISPATCH, "lanes: the task is not cut", "LANES_TASK_CHARS = 90", "LANES_TASK_CHARS = 6000"),
    (DISPATCH, "lanes: the list is not capped", "LANES_MAX_LINES = 20", "LANES_MAX_LINES = 1000"),
    (DISPATCH, "lanes: parse accepts it inside a sentence", 'in LANES_FORMS and len(t) <= 20', 'in LANES_FORMS or "lanes" in t'),
    (PICKUP, "lanes: the pickup does not answer the lanes marker", 'dispatch.do_lanes(', 'dispatch.do_nothing('),
    (DISPATCH, "workspace: a second write is allowed", "(add --dry-run first if you like) and nothing else that writes. ", "(add --dry-run first if you like). "),
]


def _copy():
    """The whole scripts folder (every module a test imports, e.g. hub_digest) and its tests, in a temp folder."""
    d = Path(tempfile.mkdtemp()) / "scripts"
    shutil.copytree(SCRIPTS, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    return d


def _run(d):
    return subprocess.run([sys.executable, "-m", "unittest", *TESTS], cwd=d / "tests", capture_output=True, text=True, timeout=600)


def main():
    # the unmutated copy must be green, or every "killed" below means nothing (19 Oct: a missing hub_digest made every test module
    # fail to import, and all 243 mutants read "killed")
    d = _copy()
    base = _run(d)
    shutil.rmtree(d.parent, ignore_errors=True)
    if base.returncode != 0:
        print("the unmutated copy is not green, so no mutant can be judged:\n" + base.stderr[-2000:])
        return 2
    red = 0
    rows = []
    only = os.environ.get("MUTANT_ONLY", "")  # a recheck: run just the mutants whose name holds this text
    mutants = [m for m in MUTANTS if only in m[1]]
    for rel, name, old, new in mutants:
        src = (SCRIPTS / rel).read_text()
        if old not in src:
            rows.append((name, "PATTERN NOT FOUND", "-"))
            continue
        d = _copy()
        (d / rel).write_text(src.replace(old, new, 1))
        r = _run(d)
        m = re.search(r"FAILED \((.*)\)", r.stderr)
        if "ImportError" in r.stderr or "ModuleNotFoundError" in r.stderr:
            rows.append((name, "BROKEN (import error)", "?"))
        elif r.returncode == 0:
            rows.append((name, "SURVIVED (suite green)", "0"))
        else:
            n = sum(int(x) for x in re.findall(r"=(\d+)", m.group(1))) if m else "?"
            rows.append((name, "killed", str(n)))
            red += 1
        shutil.rmtree(d.parent, ignore_errors=True)
    for name, verdict, n in rows:
        print(f"{verdict:24} red={n:>3}  {name}")
    print(f"\n{red} of {len(mutants)} mutants killed")
    return 0 if red == len(mutants) else 1


if __name__ == "__main__":
    sys.exit(main())
