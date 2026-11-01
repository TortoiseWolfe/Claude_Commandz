#!/usr/bin/env python3
"""Queue new co-op Talk mentions for the hub without passing chat text to a model.

Private client and self-identification settings stay outside this public backup.
"""

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata


MESSAGE_ID = re.compile(r"^(?:talk|deck):[A-Za-z0-9_-]+:[0-9]+$")
TAG = re.compile(r"\b@?TurtleWolfe\b|@Jon\b|\bJonathan\b|\bJon's\b", re.IGNORECASE)
TALK_ROOMS_PATH = "/ocs/v2.php/apps/spreed/api/v4/room?format=json"
NOTIFICATIONS_PATH = "/ocs/v2.php/apps/notifications/api/v2/notifications?format=json"


def expanded_env_path(name, default):
    return Path(os.path.expanduser(os.environ.get(name, default)))


def safe_text(value):
    text = " ".join(str(value).splitlines())
    text = text.replace("|", "/")
    return "".join(" " if char in "\t\v\f" else char
                   for char in text if unicodedata.category(char) != "Cc")


def message_date(value):
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def date_order(value):
    parsed = message_date(value)
    if parsed is None:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def load_cache(raw_dir):
    messages = []
    for pattern in ("channel-talk-*.json", "channel-deck-*.json"):
        for path in sorted(raw_dir.glob(pattern)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                print("channel watch: unable to read a cache file", file=sys.stderr)
                continue
            provenance = data.get("_provenance") or {}
            for message in data.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                ident = message.get("id")
                if not isinstance(ident, str) or not MESSAGE_ID.fullmatch(ident):
                    continue
                subject = message.get("subject") or ""
                label = provenance.get("room_name") or subject or path.stem
                sender = message.get("sender") or ""
                body = message.get("plaintextBody") or ""
                if not isinstance(body, str):
                    body = ""
                messages.append({
                    "id": ident,
                    "date": message.get("date") or "",
                    "sender": safe_text(sender),
                    "body": body,
                    "label": safe_text(label),
                    "group": path.name,
                })
    return messages


def ocs_data(response):
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    if hasattr(response, "json"):
        response = response.json()
    if isinstance(response, dict) and isinstance(response.get("ocs"), dict):
        response = response["ocs"].get("data")
    elif isinstance(response, dict) and "data" in response:
        response = response["data"]
    if isinstance(response, dict):
        for key in ("rooms", "notifications"):
            if isinstance(response.get(key), list):
                return response[key]
    if not isinstance(response, list):
        raise ValueError("unexpected Nextcloud response")
    return response


def load_puller_module(puller):
    spec = importlib.util.spec_from_file_location("channel_pull_module", puller)
    if spec is None or spec.loader is None:
        raise ImportError("unable to load channel puller")
    sys.path.insert(0, str(puller.parent))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cheap_look(puller_path, watched, notified):
    puller = load_puller_module(puller_path)
    base, user, password = puller.load_creds()
    client = puller.NC(base, user, password)
    rooms = ocs_data(client.get(TALK_ROOMS_PATH))
    notifications = ocs_data(client.get(NOTIFICATIONS_PATH))

    highest_by_token = {}
    for ident in watched:
        parts = ident.split(":")
        if len(parts) == 3 and parts[0] == "talk" and parts[2].isdecimal():
            highest_by_token[parts[1]] = max(int(parts[2]), highest_by_token.get(parts[1], 0))

    changed_room = False
    for room in rooms:
        if not isinstance(room, dict):
            raise ValueError("unexpected room response")
        token = room.get("token")
        if not isinstance(token, str) or "lastMessage" not in room:
            raise ValueError("unexpected room response")
        last_message = room["lastMessage"]
        if last_message is None:
            continue
        if not isinstance(last_message, dict):
            raise ValueError("unexpected room response")
        message_id = last_message.get("id")
        if message_id is not None and int(message_id) > highest_by_token.get(token, 0):
            changed_room = True

    new_notifications = []
    for item in notifications:
        if not isinstance(item, dict):
            raise ValueError("unexpected notification response")
        if item.get("app") not in {"spreed", "deck"}:
            continue
        if item.get("id") is None:
            raise ValueError("notification has no id")
        if str(item["id"]) not in notified:
            new_notifications.append(item)
    return changed_room, new_notifications


def notification_text(value):
    if isinstance(value, dict):
        return " ".join(notification_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(notification_text(item) for item in value)
    return str(value) if value is not None else ""


def deck_notification_tag(notification):
    subject = safe_text(notification.get("subject") or "")
    searchable = " ".join(
        notification_text(notification.get(key))
        for key in ("subject", "subjectRich", "subjectRichParameters", "message", "messageRichParameters")
    )
    assigned_to_self = (
        re.search(r"\bassign(?:ed|ment)?\b", searchable, re.IGNORECASE)
        and re.search(r"\b(?:you|your)\b", searchable, re.IGNORECASE)
    )
    if not TAG.search(searchable) and not assigned_to_self:
        return None
    raw_id = str(notification.get("id", ""))
    if not raw_id or not re.fullmatch(r"[A-Za-z0-9_-]+", raw_id):
        return None
    return f"deck-notif:{raw_id}", f'TAG deck: "{subject[:150]}"'


def sender_local(sender):
    return sender.split("@", 1)[0]


def inbox_line(detail, ident, moment, client):
    return f"- [ ] {moment} | Client talk: {client} | {detail} | {ident}"


def make_lines(messages, since, self_address, client):
    groups = {}
    tags = []
    eligible = []
    for message in messages:
        if message["sender"].casefold() == self_address.casefold() or not message["body"].strip():
            continue
        dated = message_date(message["date"])
        if since is not None and (dated is None or dated.date() < since):
            continue
        eligible.append(message)
        excerpt = safe_text(message["body"])
        if TAG.search(message["body"]):
            detail = f'TAG {sender_local(message["sender"])}, {message["label"]}: "{excerpt[:150]}"'
            tags.append((message["id"], detail))
        else:
            groups.setdefault(message["group"], []).append(message)

    items = [(detail, ident) for ident, detail in tags]
    for grouped in groups.values():
        newest = max(grouped, key=lambda message: date_order(message["date"]))
        senders = list(dict.fromkeys(sender_local(message["sender"]) for message in grouped))
        detail = (f'{grouped[0]["label"]}: {len(grouped)} new ({", ".join(senders)}), newest: '
                  f'"{safe_text(newest["body"])[:80]}"')
        items.append((detail, newest["id"]))
    moment = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [inbox_line(detail, ident, moment, client) for detail, ident in items]
    return lines, tags, eligible, items


def main():
    home = Path.home()
    try:
        machine = (home / ".config/afa/machine").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return 0
    if machine != "NX-01":
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since", help="dry-run only: include messages dated on or after YYYY-MM-DD")
    args = parser.parse_args()
    if args.since and not args.dry_run:
        parser.error("--since is only available with --dry-run")
    since = None
    if args.since:
        try:
            since = dt.date.fromisoformat(args.since)
        except ValueError:
            parser.error("--since must be YYYY-MM-DD")

    config_path = expanded_env_path("CHANNEL_CONFIG", "~/.config/afa/channel-watch.json")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("channel watch: unable to read private channel settings", file=sys.stderr)
        return 1
    if not isinstance(config, dict):
        print("channel watch: private channel settings must be a JSON object", file=sys.stderr)
        return 1
    self_address = os.environ.get("CHANNEL_SELF", config.get("self", ""))
    client = os.environ.get("CHANNEL_CLIENT", config.get("client", "co-op chat"))
    if not isinstance(self_address, str) or not self_address:
        print("channel watch: configure CHANNEL_SELF before processing chat", file=sys.stderr)
        return 1
    self_address = self_address.strip()
    if not self_address:
        print("channel watch: configure CHANNEL_SELF before processing chat", file=sys.stderr)
        return 1
    if not isinstance(client, str) or not client:
        print("channel watch: private client label must be a non-empty string", file=sys.stderr)
        return 1
    client = safe_text(client).strip()
    if not client:
        print("channel watch: private client label must be a non-empty string", file=sys.stderr)
        return 1

    raw_dir = expanded_env_path("CHANNEL_RAW", "~/repos/digital-clones/raw")
    puller = expanded_env_path("CHANNEL_PULLER", "~/repos/digital-clones/bin/pull_channels.py")
    state = expanded_env_path("STATE", "~/.claude/state")
    record = expanded_env_path("MUSE_RECORD", str(Path(__file__).with_name("muse_poll_record.sh")))

    watched_path = state / "client-talk-watched.txt"
    watched_exists = watched_path.exists()
    watched = set()
    if watched_exists:
        try:
            watched = set(watched_path.read_text(encoding="utf-8").splitlines())
        except OSError:
            print("channel watch: unable to read watched state", file=sys.stderr)
            return 1

    notified_path = state / "client-talk-notified.txt"
    notified = set()
    if notified_path.exists():
        try:
            notified = set(notified_path.read_text(encoding="utf-8").splitlines())
        except OSError:
            print("channel watch: unable to read notification state", file=sys.stderr)
            return 1

    try:
        changed_room, new_notifications = cheap_look(puller, watched, notified)
        pull_kinds = set()
        if changed_room or any(item["app"] == "spreed" for item in new_notifications):
            pull_kinds.add("talk")
        if any(item["app"] == "deck" for item in new_notifications):
            pull_kinds.add("deck")
        if not pull_kinds:
            return 0
    except Exception:
        print("channel watch: cheap look failed; falling back to full pull", file=sys.stderr)
        new_notifications = []
        pull_kinds = {"talk", "deck"}

    only = ",".join(kind for kind in ("talk", "deck") if kind in pull_kinds)
    try:
        subprocess.run(["python3", str(puller), "--since-days", "3", "--only", only],
                       timeout=180, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print("channel watch: puller timed out after 180 seconds; diffing cache", file=sys.stderr)
    except (OSError, subprocess.CalledProcessError):
        print("channel watch: puller failed; diffing cache", file=sys.stderr)

    messages = load_cache(raw_dir)
    seed_only = not watched_exists and not (args.dry_run and since is not None)

    candidates = [] if seed_only else [
        message for message in messages
        if (since is not None and args.dry_run and
            message_date(message["date"]) is not None and
            message_date(message["date"]).date() >= since)
        or (message["id"] not in watched and (since is None or not args.dry_run))
    ]
    lines, tags, eligible, items = make_lines(candidates, since, self_address, client)
    notification_tags = []
    for notification in new_notifications:
        if notification.get("app") != "deck":
            continue
        tagged = deck_notification_tag(notification)
        if tagged:
            ident, detail = tagged
            notification_tags.append(tagged)
            items.append((detail, ident))
            lines.append(inbox_line(detail, ident, dt.datetime.now().strftime("%Y-%m-%d %H:%M"), client))
    if args.dry_run:
        for line in lines:
            print(line)
        if seed_only:
            print(f"would seed {len({message['id'] for message in messages})}")
        return 0

    if seed_only and not notification_tags:
        state.mkdir(parents=True, exist_ok=True)
        watched_path.write_text("".join(f"{ident}\n" for ident in dict.fromkeys(
            message["id"] for message in messages)), encoding="utf-8")
        if new_notifications:
            with notified_path.open("a", encoding="utf-8") as notified_file:
                for ident in dict.fromkeys(str(item["id"]) for item in new_notifications
                                           if item.get("id") is not None):
                    notified_file.write(f"{ident}\n")
        print(f"seeded {len({message['id'] for message in messages})}")
        return 0

    state.mkdir(parents=True, exist_ok=True)
    for detail, ident in items:
        result = subprocess.run(["bash", str(record), "client-talk", client, detail, ident],
                                env=dict(os.environ, MUSE_STATE=str(state)),
                                capture_output=True, text=True)
        if result.returncode:
            print("channel watch: unable to queue a client-talk line", file=sys.stderr)
            return 1

    all_tags = [*tags, *notification_tags]
    if all_tags:
        wake = state / "client-tag.wake"
        try:
            wake.write_text(f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n" +
                            "".join(f"{ident}\n" for ident, _ in all_tags), encoding="utf-8")
        except OSError:
            print("channel watch: unable to write the client-tag wake file", file=sys.stderr)
            return 1

    if new_notifications:
        with notified_path.open("a", encoding="utf-8") as notified_file:
            for ident in dict.fromkeys(str(item["id"]) for item in new_notifications
                                       if item.get("id") is not None):
                notified_file.write(f"{ident}\n")

    if seed_only:
        watched_path.write_text("".join(f"{ident}\n" for ident in dict.fromkeys(
            message["id"] for message in messages)), encoding="utf-8")
        print(f"seeded {len({message['id'] for message in messages})}")
        return 0

    processed_ids = list(dict.fromkeys(message["id"] for message in messages if message["id"] not in watched))
    with watched_path.open("a", encoding="utf-8") as watched_file:
        for ident in processed_ids:
            watched_file.write(f"{ident}\n")
    print(f"client talk count: {len(eligible)} new, {len(all_tags)} tags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
