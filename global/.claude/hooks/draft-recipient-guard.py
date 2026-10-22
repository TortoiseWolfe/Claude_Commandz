#!/usr/bin/env python3
"""Refuse the Gmail connector's create_draft / update_draft for any draft that is not to Jonathan himself.

Why: the connector rewrites every <a href> into a www.google.com/url redirect, so a client never sees a clean link
(22 Oct 2026: Greg's draft had 9 of them). Drafts to anyone else go through gmail_api.py draft-create. Drafts to Jonathan
(agent notes) stay on the connector.

block-outbound.sh pipes the hook payload here for the two draft tools. Exit 2 with the reason on stderr denies; exit 0 allows.
Fail closed: a payload this cannot read, or a recipient it cannot parse, is denied.

  update_draft keeps the recipients of the draft when it is given none, and that draft may be a client's, so an update must
  name its To explicitly and every address must be Jonathan's.
  create_draft with replyToMessageId and no To would be addressed by the connector to the sender of the original, so the
  same rule: To must be named and be Jonathan's.
"""
import email.utils
import json
import sys

SELF = "jonpohlner@gmail.com"
DRAFT_TOOLS = ("mcp__claude_ai_Gmail__create_draft", "mcp__claude_ai_Gmail__update_draft")
WHY = (
    "drafts to anyone but Jonathan go through python3 ~/.claude/scripts/gmail_api.py draft-create "
    "(--reply-to <message id> keeps the thread; --html FILE --text FILE; --to, --cc, --bcc; --inline CID=FILE; --attach FILE), "
    "because the connector rewrites every link into a google.com/url redirect. To fix a client draft that already exists: "
    "gmail_api.py draft-fix-links <draftId>. Agent notes to jonpohlner@gmail.com stay on the connector "
    "(update_draft must name to=[\"jonpohlner@gmail.com\"] explicitly)."
)


def addresses(value):
    """Every address in a To/Cc/Bcc field (a list of strings or one string), lower-cased. ValueError if unreadable."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("recipients are not a list")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("recipient is not a string")
        for _, addr in email.utils.getaddresses([item]):
            if addr.strip():
                out.append(addr.strip().lower())
            elif item.strip():
                raise ValueError("no address in %r" % item)
    return out


def check(payload):
    """Return a deny reason, or None to allow."""
    tool = payload.get("tool_name", "")
    if tool not in DRAFT_TOOLS:
        return None
    args = payload.get("tool_input") or {}
    try:
        fields = {k: addresses(args.get(k)) for k in ("to", "cc", "bcc")}
    except ValueError as e:
        return "%s: unreadable recipients (%s); %s" % (tool.rsplit("__", 1)[-1], e, WHY)
    name = tool.rsplit("__", 1)[-1]
    for field, addrs in fields.items():
        for a in addrs:
            if a != SELF:
                return "%s to %s (%s) is not to Jonathan; %s" % (name, a, field, WHY)
    must_name_to = name == "update_draft" or bool(args.get("replyToMessageId"))
    if must_name_to and SELF not in fields["to"]:
        why = ("an update keeps the draft's existing recipients, which may be a client's"
               if name == "update_draft" else "a reply is addressed to the original sender, who may be a client")
        return "%s without to=[\"%s\"]: %s; %s" % (name, SELF, why, WHY)
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except Exception as e:
        print("BLOCKED: draft guard could not read the hook payload (%s)" % e, file=sys.stderr)
        return 2
    reason = check(payload)
    if reason:
        print("BLOCKED: " + reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
