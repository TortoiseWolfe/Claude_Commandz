#!/usr/bin/env python3
"""Render a Claude Code JSONL session as a scrubbed Markdown activity log."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PROJECTS_DIR = Path.home() / ".claude" / "projects"
SCRUBBER = Path.home() / ".claude" / "scripts" / "scrub_known_secrets.py"
INTERNAL_TAGS = (
    "system-reminder", "hook-output", "local-command-caveat",
    "local-command-stdout", "local-command-stderr", "command-name", "command-args",
)
TAG_RE = re.compile(
    r"<(?P<tag>" + "|".join(re.escape(tag) for tag in INTERNAL_TAGS)
    + r")\b[^>]*>.*?</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)
AGENT_ID_RE = re.compile(r"\bagentId\s*[:=]\s*([A-Za-z0-9_-]+)")
REPLY_LIMIT = 400


class ExportError(Exception):
    """An expected input or output error that should be shown without transcript data."""


def slugify(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def resolve_session(session):
    candidate = Path(session).expanduser()
    if candidate.exists() or candidate.suffix == ".jsonl":
        if not candidate.is_file():
            raise ExportError("transcript path is not a file")
        return candidate.resolve()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", session):
        raise ExportError("session id contains unsupported characters")

    matches = sorted(PROJECTS_DIR.glob(f"*/{session}.jsonl"))
    if not matches:
        raise ExportError("no transcript found for that session id")
    if len(matches) > 1:
        raise ExportError("session id matches more than one transcript; pass a path")
    return matches[0]


def resolve_latest(cwd):
    folder = Path(cwd).expanduser().resolve()
    if not folder.is_dir():
        raise ExportError("--latest requires an existing folder")
    project_dir = PROJECTS_DIR / slugify(folder)
    if not project_dir.is_dir():
        raise ExportError("no Claude Code project folder found for that cwd")
    sessions = [
        path for path in project_dir.glob("*.jsonl")
        if not path.name.startswith("agent-")
    ]
    if not sessions:
        raise ExportError("no sessions found for that cwd")
    return max(sessions, key=lambda path: path.stat().st_mtime_ns).resolve()


def read_records(path):
    records = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ExportError(f"invalid JSONL at line {line_number}") from exc
                if isinstance(record, dict):
                    records.append((line_number, record))
    except OSError as exc:
        raise ExportError(f"cannot read transcript ({exc.strerror or type(exc).__name__})") from exc
    except UnicodeDecodeError as exc:
        raise ExportError("transcript is not valid UTF-8") from exc
    records.sort(key=lambda item: (timestamp(item[1]) or datetime.min.replace(tzinfo=timezone.utc), item[0]))
    return [record for _, record in records]


def timestamp(record):
    value = record.get("timestamp")
    if not value:
        return None
    numeric = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip()):
        numeric = float(value)
    if numeric is not None:
        seconds = numeric / 1000 if abs(numeric) >= 100_000_000_000 else numeric
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def local_time(value):
    if value is None:
        return "unknown"
    return value.astimezone().strftime("%-I:%M %p")


def local_datetime(value):
    if value is None:
        return "unknown"
    return value.astimezone().strftime("%Y-%m-%d %-I:%M %p")


def duration_text(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def text_and_attachments(content):
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []
    texts, attachments = [], []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block.get("type") not in {"tool_use", "tool_result", "thinking"}:
            attachments.append(str(block.get("type") or "attachment"))
    return "\n".join(texts), attachments


def clean_internal(text):
    for tag in INTERNAL_TAGS:
        text = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>", "", text, flags=re.IGNORECASE | re.DOTALL
        )
    return text.strip()


def is_internal_record(record):
    kind = str(record.get("type", "")).casefold()
    marked = (
        bool(record.get("isMeta"))
        or bool(record.get("isMetaMessage"))
        or kind == "system"
        or kind.startswith("hook")
        or any(key in record for key in ("hookOutput", "hook_output", "hookEventName", "hookName"))
    )
    return marked


def has_internal_content(record):
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else record.get("content")
    text, _ = text_and_attachments(content)
    return bool(TAG_RE.search(text))


def tool_result_record(record):
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return (
        "toolUseResult" in record
        or "toolUseID" in record
        or (isinstance(content, list) and bool(content)
            and all(isinstance(item, dict) and item.get("type") == "tool_result" for item in content))
    )


def user_prompt(record, include_all):
    if str(record.get("type", "")).casefold() == "queue-operation":
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    if tool_result_record(record):
        return None
    kind = str(record.get("type", "")).casefold()
    if kind == "attachment" and not include_all:
        return None
    if is_internal_record(record) and not include_all:
        return None

    text, attachments = text_and_attachments(message.get("content"))
    if kind == "attachment":
        text, extra = text_and_attachments(record.get("content"))
        attachments.extend(extra)
        if not text:
            attachments.append(str(record.get("attachmentType") or "attachment"))
    if not include_all:
        text = clean_internal(text)
    if attachments and include_all:
        text = "\n".join(part for part in (text, *[f"[Attachment: {kind}]" for kind in attachments]) if part)
    if not text.strip():
        return None
    return text


def queued_prompt(record):
    if str(record.get("type", "")).casefold() != "queue-operation":
        return None
    if str(record.get("operation", "")).casefold() != "enqueue":
        return None
    content = record.get("content")
    if content is None:
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
    text, _ = text_and_attachments(content)
    text = clean_internal(text)
    return text if text.strip() else None


def fenced(text, language="text"):
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def first_line(text):
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return "(no prompt text)"


def one_line(value, limit=240):
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def link_text(value):
    return one_line(value).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def tool_target(name, inputs):
    inputs = inputs if isinstance(inputs, dict) else {}
    if name.casefold() in {"bash", "shell"} and inputs.get("command"):
        return one_line(str(inputs["command"]).splitlines()[0])
    for key in ("file_path", "path", "url", "query", "pattern", "description", "command"):
        value = inputs.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return one_line(str(value).splitlines()[0])
    return ""


def is_subagent_tool(name):
    return name.casefold() in {"task", "agent", "spawnagent"}


def prompt_from_input(inputs):
    if not isinstance(inputs, dict):
        return ""
    for key in ("prompt", "task", "instructions", "description"):
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_ids(value):
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"agentId", "agent_id", "agentID"} and isinstance(child, str):
                found.add(child)
            found.update(extract_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(extract_ids(child))
    elif isinstance(value, str):
        found.update(AGENT_ID_RE.findall(value))
    return found


def prompt_text_from_records(records):
    for record in records:
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "user" or tool_result_record(record):
            continue
        content, _ = text_and_attachments(message.get("content"))
        content = clean_internal(content)
        if content:
            return content
    return ""


def discover_agent_files(source):
    # Claude Code stores these beside the session JSONL in its session-scoped subagents folder.
    session_dir = source.parent / source.stem / "subagents"
    candidates = set(session_dir.glob("agent-*.jsonl"))
    candidates.update(session_dir.glob("workflows/wf_*/agent-*.jsonl"))
    candidates.update((source.parent / "subagents").glob("agent-*.jsonl"))
    return sorted(path for path in candidates if path.is_file())


def load_subagents(source, records, calls):
    calls = [call for call in calls if is_subagent_tool(call["name"])]
    files = discover_agent_files(source)
    if not files or not calls:
        return
    parsed = []
    for path in files:
        child_records = read_records(path)
        child_ids = {path.stem.removeprefix("agent-")}
        for record in child_records[:10]:
            child_ids.update(extract_ids(record))
        child_prompt = prompt_text_from_records(child_records)
        times = [value for record in child_records if (value := timestamp(record)) is not None]
        parsed.append({
            "path": path,
            "records": child_records,
            "ids": child_ids,
            "prompt": child_prompt,
            "start": min(times) if times else None,
            "end": max(times) if times else None,
        })

    result_ids = {}
    for record in records:
        tool_id = record.get("toolUseID") or record.get("tool_use_id")
        if tool_id:
            result_ids.setdefault(str(tool_id), set()).update(extract_ids(record))
    unused = set(range(len(parsed)))
    for call in calls:
        if not unused:
            break
        match = next(
            (idx for idx in unused if parsed[idx]["ids"].intersection(
                call["agent_ids"] | result_ids.get(call["tool_id"], set())
            )),
            None,
        )
        if match is None and call["prompt"]:
            line = first_line(call["prompt"])
            match = next((idx for idx in unused
                          if first_line(parsed[idx]["prompt"]) == line), None)
        if match is None:
            session_scoped = [idx for idx in unused if parsed[idx]["path"].is_relative_to(
                source.parent / source.stem / "subagents"
            )]
            if len(session_scoped) == len(unused) == len(calls):
                def distance(idx):
                    child_start, call_time = parsed[idx]["start"], call["time"]
                    return abs((child_start - call_time).total_seconds()) if child_start and call_time else 0

                match = min(session_scoped, key=distance)
        if match is not None:
            call["transcript"] = parsed[match]
            unused.remove(match)


def response_texts(record, include_all):
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return []
    content = message.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    result = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_use":
            name = str(block.get("name") or "tool")
            inputs = block.get("input")
            target = tool_target(name, inputs)
            call = {
                "name": name,
                "target": target,
                "tool_id": str(block.get("id") or ""),
                "prompt": prompt_from_input(inputs) if is_subagent_tool(name) else "",
                "agent_ids": extract_ids(inputs),
                "time": timestamp(record),
                "transcript": None,
            }
            result.append(("tool", call))
        elif kind == "text" and isinstance(block.get("text"), str):
            text = block["text"]
            if not include_all:
                text = clean_internal(text)
            if text.strip():
                result.append(("reply", text))
    return result


def anchor_id(call, index):
    base = call.get("tool_id") or f"subagent-{index}"
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", base)
    return f"subagent-{safe}"


def render_subagent(call, index, with_subagents):
    transcript = call.get("transcript")
    prompt = call["prompt"]
    if not prompt and transcript:
        prompt = transcript["prompt"]
    label = first_line(prompt)
    anchor = anchor_id(call, index)
    if transcript and transcript["start"] and transcript["end"]:
        duration = duration_text((transcript["end"] - transcript["start"]).total_seconds())
    else:
        duration = "duration unavailable"
    out = [f"- {call['name']}: [{link_text(label)}](#{anchor}) — {duration}",
           f'<a id="{anchor}"></a>',
           f"#### Subagent: {one_line(label)}",
           f"- Duration: {duration}"]
    if with_subagents and prompt:
        start = transcript["start"] if transcript else call.get("time")
        out.extend([f"- Prompt time: {local_time(start)}", "", fenced(prompt)])
    if with_subagents and transcript:
        out.extend(render_activity(transcript["records"], include_all=False, subagents=False))
    return out


def render_activity(records, source=None, include_all=False, with_subagents=False, subagents=True):
    calls = []
    events = []
    for record in records:
        message = record.get("message")
        role = message.get("role") if isinstance(message, dict) else None
        if role == "assistant":
            found = response_texts(record, include_all)
            events.extend(found)
            calls.extend(value for kind, value in found if kind == "tool")
        elif include_all and (
            is_internal_record(record)
            or has_internal_content(record)
            or str(record.get("type", "")).casefold() in {"attachment", "hook", "hook_output", "system"}
        ):
            content = message.get("content") if isinstance(message, dict) else record.get("content")
            text, attachments = text_and_attachments(content)
            label = str(record.get("type") or "record")
            if text.strip():
                events.append(("extra", (label, text.strip())))
            for attachment in attachments:
                events.append(("extra", (label, f"[Attachment: {attachment}]")))
    if source and subagents:
        load_subagents(source=source, records=records, calls=calls)

    out = []
    tool_index = 0
    for kind, value in events:
        if kind == "tool":
            target = value["target"]
            if is_subagent_tool(value["name"]):
                out.extend(render_subagent(value, tool_index, with_subagents))
                tool_index += 1
            else:
                suffix = f" — {target}" if target else ""
                out.append(f"- {value['name']}{suffix}")
        elif kind == "reply":
            full = len(value)
            shown = value[:REPLY_LIMIT]
            tail = "…" if full > REPLY_LIMIT else ""
            out.append(f"- Reply ({full} characters; showing {min(full, REPLY_LIMIT)}):")
            out.extend(f"  > {line}" for line in (shown + tail).splitlines())
        elif kind == "extra":
            label, text = value
            out.append(f"- {label}:")
            out.extend(f"  > {line}" for line in text.splitlines())
    return out


def render_markdown(source, records, with_subagents=False, include_all=False):
    all_records = records
    if not include_all:
        records = [record for record in records if not is_internal_record(record)]
    metadata = next((record for record in all_records if record.get("sessionId")), {})
    cwd = next((record.get("cwd") for record in all_records if record.get("cwd")), "")
    branch = next((record.get("gitBranch") for record in all_records if record.get("gitBranch")), "")
    session_id = str(metadata.get("sessionId") or source.stem)
    times = [value for record in all_records if (value := timestamp(record)) is not None]
    start = min(times) if times else None
    end = max(times) if times else None
    length = duration_text((end - start).total_seconds()) if start and end else "unknown"
    folder = Path(cwd).name if cwd else source.parent.name
    out = [
        "# Claude Code session log",
        "",
        f"- Session: `{session_id}`",
        f"- Folder: `{folder}`",
        f"- Branch: `{branch or 'unknown'}`",
        f"- Start: {local_datetime(start)}",
        f"- End: {local_datetime(end)}",
        f"- Length: {length}",
        "",
    ]

    turns = []
    queued = []
    pending_queued = {}
    leading_events = []
    current = None
    for record in records:
        kind = str(record.get("type", "")).casefold()
        if kind == "queue-operation":
            prompt = queued_prompt(record)
            if prompt is not None:
                entry = {"record": record, "prompt": prompt, "matched": False}
                queued.append(entry)
                pending_queued.setdefault(prompt, []).append(entry)
            continue
        if include_all and (
            is_internal_record(record) or has_internal_content(record) or kind == "attachment"
        ):
            if current is not None:
                current["events"].append(record)
            else:
                leading_events.append(record)
            continue
        prompt = user_prompt(record, include_all)
        if prompt is not None:
            matches = pending_queued.get(prompt)
            if matches:
                matches.pop(0)["matched"] = True
            current = {"record": record, "prompt": prompt, "events": []}
            turns.append(current)
            continue
        if current is not None and tool_result_record(record):
            current["events"].append(record)
            continue
        if current is None:
            continue
        message = record.get("message")
        role = message.get("role") if isinstance(message, dict) else None
        if role == "assistant":
            current["events"].append(record)

    if leading_events:
        out.extend(["## Session context", ""])
        out.extend(render_activity(leading_events, include_all=True, subagents=False))
        out.append("")

    for number, turn in enumerate(turns, 1):
        out.extend([f"## {local_time(timestamp(turn['record']))} — Prompt {number}", "",
                    fenced(turn["prompt"]), ""])
        out.extend(render_activity(turn["events"], source=source, include_all=include_all,
                                   with_subagents=with_subagents))
        if turn["events"]:
            out.append("")
    for entry in queued:
        if entry["matched"]:
            continue
        out.extend([
            f"## {local_time(timestamp(entry['record']))} — Queued, not sent",
            "",
            fenced(entry["prompt"]),
            "",
        ])
    return "\n".join(out).rstrip() + "\n"


def write_scrubbed(text, source, out_path=None, stdout=None):
    if out_path:
        destination = Path(out_path).expanduser().resolve()
        if destination == source.resolve():
            raise ExportError("output must not overwrite the source transcript")
        if not destination.parent.is_dir():
            raise ExportError("output directory does not exist")
    with tempfile.TemporaryDirectory(prefix="session-to-md-") as temp_dir:
        staged = Path(temp_dir) / "session.md"
        staged.write_text(text, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(SCRUBBER), "--min-age", "0", str(staged)],
                capture_output=True, text=True, check=False,
            )
        except OSError as exc:
            raise ExportError(f"secret scrub could not run ({type(exc).__name__}); output was not written") from exc
        if result.returncode:
            raise ExportError(f"secret scrub failed (exit {result.returncode}); output was not written")
        cleaned = staged.read_text(encoding="utf-8")
        if out_path:
            fd, temporary = tempfile.mkstemp(prefix=".session-to-md-", dir=destination.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(cleaned)
                os.replace(temporary, destination)
            except OSError:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        else:
            (stdout or sys.stdout).write(cleaned)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", nargs="?", help="session id or path to a .jsonl transcript")
    parser.add_argument("--latest", metavar="CWD", help="use the newest session for this working directory")
    parser.add_argument("--with-subagents", action="store_true", help="include subagent prompts and activity")
    parser.add_argument("--all", action="store_true", dest="include_all",
                        help="include system reminders, hook output, and attachment markers")
    parser.add_argument("--out", help="write the scrubbed Markdown to this file (default: stdout)")
    args = parser.parse_args(argv)
    try:
        if bool(args.session) == bool(args.latest):
            parser.error("provide either a session id/path or --latest <cwd>")
        source = resolve_latest(args.latest) if args.latest else resolve_session(args.session)
        records = read_records(source)
        text = render_markdown(source, records, with_subagents=args.with_subagents,
                               include_all=args.include_all)
        write_scrubbed(text, source, args.out)
        return 0
    except ExportError as exc:
        print(f"session_to_md.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
