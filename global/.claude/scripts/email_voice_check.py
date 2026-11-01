#!/usr/bin/env python3
"""
Detect AI-generated / "AI-ish" voice in a CLIENT EMAIL before it is sent.

Email-tuned fork of good_prompt_bad_prompt/scripts/detect_llm.py. Same engine
(zero third-party deps, stdlib only), retargeted from code-eval rationale prose to
short client-facing emails. Prompted by Greg calling a Chattanooga.Digital update
"a bit AI-ish" (2026-07-24).

Two passes:

Lexical (surface words):
- Formal/transition phrases ("furthermore", "moreover", "utilizing", "it is worth noting")
- Email-classic tells ("I hope this email finds you well", "delve", "reach out", "not just X but Y")
- Hedging ("somewhat", "relatively", "appears to")
- Generic praise ("comprehensive", "impressive", "robust")
- Contraction avoidance ("did not" vs "didn't")

Structural (the AI skeleton that survives word-scrubbing):
- Closing parallel-verb chain (a tricolon at the end)
- Stacked encyclopedic appositives ("X, the Y," glosses, 2+)
- Even contraction spread (contractions sprinkled uniformly, not clumped)
- Em-dash overuse (3+ mid-sentence dashes — the single loudest email tell)

Strict only: offers of future work ("I could set up", "if you want", "plan to").
Link checks: recorded-work claims need a link nearby, and a reply cannot open with
a flat yes or no after a quoted question.

Model-eval-specific checks from the source (model-as-"they" pronoun, inverted
hedging on model-prediction sentences, cross-answer uniformity for graded batches)
were dropped as irrelevant to email. Passing this is necessary, not sufficient —
it means the prose doesn't *read* AI, not that the content is right.

Usage:
  python3 email_voice_check.py <text_file> [--strict]
  python3 email_voice_check.py --stdin [--strict]
"""

import re
import statistics
import sys
from html.parser import HTMLParser
from pathlib import Path


# --- Lexical: formal / transition phrases ---------------------------------
FORMAL_PHRASES = [
    r'\bI would recommend\b',
    r'\bIt is (suggested|recommended|worth noting|important to note)\b',
    r'\butilizing\b',
    r'\bfurthermore\b',
    r'\bmoreover\b',
    r'\bnevertheless\b',
    r'\bconsequently\b',
    r'\bthus,',
    r'\bhence,',
    r'\baccordingly\b',
    r'\bin conclusion\b',
    r'\bto summarize\b',
    r'\bit should be noted\b',
    r'\bone should consider\b',
    r'\bas such,',
    r'\bthat being said\b',
    r'\bneedless to say\b',
]

# --- Lexical: email-classic AI tells (the ones the source lacked) ---------
EMAIL_TELLS = [
    r'\bI hope this (email|message|note) finds you well\b',
    r'\bI wanted to (reach out|touch base|follow up|circle back)\b',
    r'\b(reach out|touch base|circle back)\b',
    r'\bat your earliest convenience\b',
    r'\bplease (do not|don\'?t) hesitate\b',
    r'\brest assured\b',
    r'\bdelve\b',
    r'\btapestry\b',
    r'\ba testament to\b',
    r'\bin (today\'?s|the) (fast[- ]paced|ever[- ]changing|modern|digital) (world|landscape|era)\b',
    r'\bnavigat(e|ing) the (complexit|landscape|world)',
    r'\bin the realm of\b',
    r'\bwhen it comes to\b',
    r'\bnot just [^,.;]{1,45}? but\b',
    r'\bit\'?s not (just )?about [^,.;]{1,45}?,? (it\'?s|but)\b',
    r'\bunlock(ing)? (the|your) (potential|power)\b',
    r'\belevate your\b',
    r'\bseamless(ly)?\b',
]

HEDGING_PHRASES = [
    r'\bsomewhat\b',
    r'\brelatively\b',
    r'\bappears to\b',
    r'\bseems to indicate\b',
    r'\bseems to suggest\b',
    r'\bmay potentially\b',
    r'\bcould potentially\b',
    r'\bmight potentially\b',
    r'\bit appears that\b',
]

GENERIC_PRAISE = [
    r'\bwell[- ]structured\b',
    r'\bcomprehensive\b',
    r'\bcommendable\b',
    r'\bnoteworthy\b',
    r'\bexemplary\b',
    r'\bimpressive\b',
    r'\brobust\b',
    r'\belegant solution\b',
]

# Contractions that humans use but AI prose often avoids.
CONTRACTION_PAIRS = [
    (r"\bdid not\b", "didn't"),
    (r"\bcannot\b", "can't"),
    (r"\bwill not\b", "won't"),
    (r"\bdo not\b", "don't"),
    (r"\bdoes not\b", "doesn't"),
    (r"\bis not\b", "isn't"),
    (r"\bare not\b", "aren't"),
    (r"\bwas not\b", "wasn't"),
    (r"\bwere not\b", "weren't"),
    (r"\bwould not\b", "wouldn't"),
    (r"\bcould not\b", "couldn't"),
    (r"\bshould not\b", "shouldn't"),
    (r"\bI am\b", "I'm"),
    (r"\bI have\b", "I've"),
    (r"\bI would\b", "I'd"),
    (r"\bit is\b", "it's"),
    (r"\bthat is\b", "that's"),
    (r"\bwe are\b", "we're"),
    (r"\byou are\b", "you're"),
]

# Mid-sentence dash used as a clause separator (en/em dash, or a SPACED ascii
# hyphen). Deliberately does NOT match "well-structured" (tight ascii hyphen).
# `[ \t]+` not `\s+`: \s spans NEWLINES, so two consecutive list items
#     - user_login_form
#     - search_form
# matched as one "mid-sentence dash" ('m' + '\n  - ' + 's'). That made any
# markdown or YAML bullet list read as em-dash overuse, and it fired three
# times in one session on text containing no em-dashes at all. A clause
# separator by definition sits within a line.
MID_SENTENCE_DASH = re.compile(r'\w(?:[ \t]+[-–—][ \t]+|[–—])\w')

# Contraction tokens used to measure how evenly contractions are spread.
CONTRACTION_TOKEN = re.compile(r"\b\w+'(s|t|re|ve|ll|d|m)\b", re.IGNORECASE)


def count_contractions(text):
    """Count expanded forms vs contractions. Returns (expanded, contracted)."""
    expanded = 0
    contracted = 0
    for expanded_pattern, contraction in CONTRACTION_PAIRS:
        expanded += len(re.findall(expanded_pattern, text, re.IGNORECASE))
        contracted += len(re.findall(re.escape(contraction), text, re.IGNORECASE))
    return expanded, contracted


def count_mid_sentence_dashes(text):
    """Count clause-separator dashes (the loudest AI-email tell)."""
    return len(MID_SENTENCE_DASH.findall(text))


def split_sentences(text):
    """Split into sentences on terminal punctuation. Good enough for prose checks."""
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]


def split_answers(text):
    """Split multi-draft input on a line that is only --- or ### (3+ of either)."""
    parts = re.split(r'(?m)^\s*(?:-{3,}|#{3,})\s*$', text)
    return [p.strip() for p in parts if p.strip()]


def has_closing_parallel_chain(text):
    """
    True if the last sentence is a parallel-verb chain / then-then sequence — the
    tricolon-at-the-climax habit ("I took a backup, caught the hiccup, and verified
    the site healthy"). A person writes one by accident; AI lands one at the end of
    every paragraph.
    """
    sents = split_sentences(text)
    if not sents:
        return False
    last = sents[-1].lower()
    if len(re.findall(r',\s+then\b', last)) >= 2:
        return True
    if re.search(r'(\b\w+(s|ed|ing)\b[^,]*,\s+){2,}.*\b(and|or)\s+\w+', last):
        return True
    return False


def count_stacked_appositives(text):
    """
    Count "X, the Y," / "X, a Y," encyclopedic glosses inserted mid-sentence. AI
    packs context this way ("the dhitchenor/hubzilla image, the one the admin guide
    recommends,"); a person assumes you know or explains in a separate sentence.
    """
    return len(re.findall(r',\s+(?:the|a|an)\s+[\w][\w\s/.\-]{2,40}?,', text))


def has_even_contraction_spread(text):
    """
    True if contractions exist and are sprinkled suspiciously evenly — the artifact
    of scrubbing AI prose for voice (one contraction every ~20-30 words at regular
    intervals) rather than clumping them the way natural writing does.
    """
    words = text.split()
    n = len(words)
    if n < 25:  # emails are short; lower than the source's 40
        return False
    positions = []
    running = 0
    for w in words:
        if CONTRACTION_TOKEN.search(w):
            positions.append(running)
        running += 1
    if len(positions) < 3:
        return False
    gaps = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
    mean_gap = statistics.mean(gaps)
    if mean_gap < 12 or mean_gap > 40:
        return False
    if len(gaps) < 2:
        return False
    cv = statistics.pstdev(gaps) / mean_gap if mean_gap else 1.0
    return cv < 0.45


def shape_signature(answer):
    """Coarse shape fingerprint: (sentence count, rounded word mean)."""
    sents = split_sentences(answer)
    if not sents:
        return (0, 0)
    lens = [len(s.split()) for s in sents]
    return (len(sents), round(statistics.mean(lens) / 5) * 5)


def answers_are_uniform(answers):
    """True if 3+ drafts share near-identical shape (one generation pass)."""
    if len(answers) < 3:
        return False
    sig_sent_counts = []
    means = []
    for a in answers:
        sents = split_sentences(a)
        if not sents:
            return False
        sig_sent_counts.append(len(sents))
        means.append(statistics.mean(len(s.split()) for s in sents))
    sent_spread = max(sig_sent_counts) - min(sig_sent_counts)
    mean_spread = max(means) - min(means)
    return sent_spread <= 1 and mean_spread <= 6


def structural_matches(text):
    """Run the structural pass. Returns a list of ('structural', description) tuples."""
    found = []
    answers = split_answers(text)
    multi = len(answers) >= 2
    targets = answers if multi else [text]

    fired = {'parallel_chain': 0, 'appositives': 0, 'even_contractions': 0}
    for a in targets:
        if has_closing_parallel_chain(a):
            fired['parallel_chain'] += 1
        if count_stacked_appositives(a) >= 2:
            fired['appositives'] += 1
        if has_even_contraction_spread(a):
            fired['even_contractions'] += 1

    if fired['parallel_chain']:
        found.append(('structural', f"closing parallel-verb chain (x{fired['parallel_chain']})"))
    if fired['appositives']:
        found.append(('structural', f"stacked appositive glosses (x{fired['appositives']})"))
    if fired['even_contractions']:
        found.append(('structural', "even contraction spread (sprinkled, not clumped)"))

    # Em-dash overuse — the loudest single email tell.
    dashes = count_mid_sentence_dashes(text)
    if dashes >= 3:
        found.append(('structural', f"em-dash overuse ({dashes} mid-sentence dashes)"))

    if multi and answers_are_uniform(answers):
        found.append(('structural', f"cross-draft uniformity ({len(answers)} drafts, same shape)"))

    return found


# ── HOUSE-RULE CHECKS (recipient-scoped) ──────────────────────────────────────
#
# These are NOT AI-voice tells. They are the three things Greg has actually
# objected to, in his own words, and until 2026-08-21 this script measured none
# of them — so it printed PASS on a 407-word jargon-heavy draft that broke the
# written rule three ways. A rule that isn't measured isn't a rule.
#
# Per-recipient on purpose. feedback_client_email_house_format is explicit:
# "The format is per recipient. Do not apply one to both." A 350-word cap on
# William's technical mail would be wrong.

GREG_WORD_LIMIT = 350

# Seeded from the memory note's own list of terms Greg could not follow, plus the
# ones that showed up in the drafts he was about to receive. A term is FINE if it
# is glossed in the same sentence; see is_glossed().
JARGON_TERMS = [
    'PR', 'repo', 'repository', 'merge', 'merged', 'CI', 'relay', 'signalling',
    'dev branch', 'staging', 'orchestrator', 'Swarm', 'secret', 'composer',
    'stack', 'env', 'cron', 'handler', 'config', 'deploy', 'commit', 'branch',
    'endpoint', 'volume', 'container', 'schema', 'webform', 'recipe', 'purge',
    'honeypot', 'SPF', 'DKIM', 'IMAP', 'API', 'DNS', 'CSV',
]

# A gloss is any of these appearing in the same sentence — the writer explaining
# the term rather than assuming it.
GLOSS_MARKERS = re.compile(
    r'\b(that is|which is|which means|meaning|in other words|i\.e\.|'
    r'the thing that|basically|put simply|a kind of|what that means)\b',
    re.IGNORECASE)


def strip_quoted_and_signature(text):
    """Body words only: drop quoted reply lines and a trailing signature."""
    lines = []
    for line in text.split('\n'):
        if line.lstrip().startswith('>'):
            continue
        lines.append(line)
    body = '\n'.join(lines)
    # Trailing signature: a short final line that is just a name.
    #
    # BOUNDED. The unbounded version popped every trailing line of <= 2 words
    # and so ate whole documents: a reply of terse answers under `>` quotes --
    # the exact shape this gate exists to enforce -- stripped to nothing and
    # reported "0 words, PASS". It also swallowed short bullet lists, hiding
    # both their word count and any jargon in them. Measured 2026-08-22.
    #
    # A signature is at most two lines ("Jon", or "Jon" + a title), so removing
    # more than two is never removing a signature.
    parts = body.rstrip().split('\n')
    removed = 0
    while parts and removed < 2:
        last = parts[-1].strip()
        if not last:
            parts.pop()          # blank lines are free, they are not content
            continue
        if len(last.split()) <= 2 and not last.endswith(('.', '!', '?', ':')):
            parts.pop()
            removed += 1
            continue
        break
    return '\n'.join(parts)


def count_body_words(text):
    return len(strip_quoted_and_signature(text).split())


def is_glossed(sentence):
    return bool(GLOSS_MARKERS.search(sentence))


def find_unglossed_jargon(text):
    """Jargon terms used without a plain-English explanation in the same sentence."""
    body = strip_quoted_and_signature(text)
    hits = []
    for sentence in split_sentences(body):
        if is_glossed(sentence):
            continue
        for term in JARGON_TERMS:
            if re.search(r'\b' + re.escape(term) + r'\b', sentence):
                hits.append(term)
    # De-duplicate, keep order.
    seen, out = set(), []
    for h in hits:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


# --- Unexplained acronyms (27 Oct) ---------------------------------------------
# House rule (~/repos/CLAUDE.md): spell out each acronym the first time it appears,
# with a plain explanation in parentheses. A word of 2 to 5 capitals (digits allowed
# after the first letter) is flagged at its first use unless that use is explained:
#   CSS (Cascading Style Sheets, the code that controls how a page looks)
#   Cascading Style Sheets (CSS)          <- spelled out first
#   CSS, short for Cascading Style Sheets
# Only ordinary words, times, places and the like are allowed; real jargon never is.

ACRONYM_ALLOW = {
    # times and zones
    'AM': 'clock time', 'PM': 'clock time', 'ET': 'time zone', 'EST': 'time zone',
    'EDT': 'time zone', 'CT': 'time zone', 'CST': 'time zone', 'CDT': 'time zone',
    'PT': 'time zone', 'PST': 'time zone', 'PDT': 'time zone', 'UTC': 'time zone',
    # everyday words
    'OK': 'everyday word', 'ID': 'everyday word (identification)',
    'TV': 'everyday word', 'PC': 'everyday word (home computer)', 'RSVP': 'everyday word on invitations',
    'ASAP': 'everyday word', 'FYI': 'everyday word', 'PS': 'postscript, everyday',
    # places
    'US': 'country', 'USA': 'country', 'UK': 'country', 'EU': 'region',
    'DC': 'place', 'NYC': 'place',
    # numbering
    'II': 'roman numeral', 'III': 'roman numeral', 'IV': 'roman numeral',
    'VI': 'roman numeral',
}
# Ordinary words typed in capitals for emphasis; not acronyms.
ACRONYM_SHOUTED_WORDS = {
    'NOT', 'ALL', 'AND', 'THE', 'NEW', 'NOW', 'NO', 'YES', 'OFF', 'OUT', 'ANY',
    'ONE', 'TWO', 'TOP', 'TO', 'DO', 'IT', 'IS', 'IF', 'OR', 'ON', 'OF', 'IN', 'AT',
    'BY', 'UP', 'AS', 'BE', 'SO', 'WE', 'HE', 'ME', 'MY', 'GO', 'HI', 'HOLD', 'STOP',
    'WAIT', 'NOTE', 'DONE', 'FAIL', 'PASS', 'SAME', 'ONLY', 'NEVER', 'ALWAYS',
    'FREE', 'SAFE', 'LIVE', 'DRAFT', 'TODO', 'BLOCKED', 'READY',
}
_US_STATES = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID',
              'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS',
              'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK',
              'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV',
              'WI', 'WY'}
# 2 to 5 capitals/digits, optional plural: PR, PRs, CSS, S3, API's
_ACRONYM = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,4}(?:s|'s)?(?![A-Za-z0-9])")
_ACRONYM_SMALL_WORDS = {'of', 'the', 'and', 'for', 'a', 'an', 'to', 'in', 'on', 'or', 'as'}
_ACRONYM_SKIP = [
    re.compile(r'(?is)<(pre|code|style|script)\b.*?</\1\s*>'),   # code in HTML
    re.compile(r'(?s)```.*?```'),                                 # fenced code
    re.compile(r'`[^`\n]*`'),                                     # inline code
    re.compile(r'(?:https?://|www\.)[^\s<>")]+'),                 # URLs
    re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+'),                  # email addresses
    re.compile(r'\[[A-Za-z0-9-]+>[A-Za-z0-9-]+\]'),               # fleet tags [CC>MUSE]
    re.compile(r'(?<![\w])(?:~|\.{0,2})/[^\s<>")]+'),             # file paths
    re.compile(r'(?i)\b[\w-]+\.(?:html?|md|py|js|json|tsv|csv|txt|pdf|docx?|xlsx?|png|jpe?g|sh)\b'),
]
_HTML_HINT = re.compile(r'</?(?:p|div|br|li|ul|h[1-6]|span|b|a|td|tr|table)\b', re.I)


def _acronym_prose(text):
    """Prose only: quoted lines, code, URLs, addresses, fleet tags and HTML tags removed."""
    text = '\n'.join(ln for ln in text.split('\n') if not ln.lstrip().startswith('>'))
    if _HTML_HINT.search(text):
        # Drop code blocks and attributes first, then turn block tags into line breaks.
        text = re.sub(r'(?is)<(pre|code|style|script)\b.*?</\1\s*>', ' ', text)
        text = re.sub(r'(?i)</(?:p|div|li|h[1-6]|tr|td)>|<br\s*/?>', '\n', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        import html as _html
        text = _html.unescape(text)
    for pat in _ACRONYM_SKIP:
        text = pat.sub(' ', text)
    return text


def _initials_match(letters, words):
    """True if the words, ignoring little ones, end with initials spelling the acronym."""
    sig = [w for w in words if w.lower() not in _ACRONYM_SMALL_WORDS]
    for ws in (sig, words):
        inits = ''.join(w[0].upper() for w in ws if w[:1].isalnum())
        if inits.endswith(letters.upper()):
            return True
    return False


def _acronym_explained(prose, m, base):
    after = prose[m.end():m.end() + 400]
    before = prose[max(0, m.start() - 160):m.start()]
    # CSS (Cascading Style Sheets, the code ...): a parenthesis holding two or more words.
    pm = re.match(r'\s*\(([^()\n]{3,300})\)', after)
    if pm and len(re.findall(r'[A-Za-z]{2,}', pm.group(1))) >= 2:
        return True
    # CSS, short for ... / CSS stands for ... / CSS means ...
    if re.match(r'\s*,?\s*(?:which\s+)?(?:is\s+short\s+for|short\s+for|stands?\s+for|means)\b', after, re.I):
        return True
    # Cascading Style Sheets (CSS): spelled out just before, then the acronym in parentheses.
    if before.rstrip().endswith('('):
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", before.rstrip()[:-1])
        if words and _initials_match(base, words[-(len(base) + 3):]):
            return True
    return False


def find_unexplained_acronyms(text):
    """Acronyms (2-5 capitals) whose first use has no plain explanation: [(acronym, context)]."""
    prose = _acronym_prose(text)
    seen, out = set(), []
    for m in _ACRONYM.finditer(prose):
        tok = m.group()
        base = re.sub(r"(?:'s|s)$", '', tok) if not tok.isupper() else tok
        if base in seen:
            continue
        if base in ACRONYM_ALLOW or base in ACRONYM_SHOUTED_WORDS:
            seen.add(base)
            continue
        s, e = m.start(), m.end()
        prev = prose[s - 1] if s else ''
        nxt = prose[e] if e < len(prose) else ''
        # a piece of a name or path: DOMAIN_NAME, A/B, EBU-design, ACE-Step, user.EBU, app.JS
        if prev in '/@#$' or nxt in '/@':
            continue
        if (prev in '-_.' and s > 1 and prose[s - 2].isalnum()) or \
           (nxt in '-_.' and e + 1 < len(prose) and prose[e + 1].isalnum()):
            continue
        # a size right after its number: 2.1 GB
        if base in ('KB', 'MB', 'GB', 'TB') and re.search(r'\d\s?$', prose[max(0, s - 3):s]):
            continue
        # quarters: Q1, H2
        if re.fullmatch(r'[QH][1-4]', base):
            seen.add(base)
            continue
        # a state after a comma: Chattanooga, TN
        if base in _US_STATES and re.search(r',\s$', prose[max(0, s - 3):s]):
            continue
        # a line of nothing but capitals is a heading, not jargon
        ls = prose.rfind('\n', 0, s) + 1
        le = prose.find('\n', e)
        line = prose[ls:le if le != -1 else len(prose)]
        letters = [c for c in line if c.isalpha()]
        if len(letters) >= 8 and all(c.isupper() for c in letters):
            continue
        seen.add(base)
        if _acronym_explained(prose, m, base):
            continue
        ctx = ' '.join(prose[max(0, s - 40):e + 40].split())
        out.append((base, ctx))
    return out


def acronym_messages(text):
    return [f'{a}: first use has no plain explanation. Write "{a} (what it means, in plain words)" '
            f'or spell it out first, "... ({a})". Seen in: "...{ctx}..."'
            for a, ctx in find_unexplained_acronyms(text)]


# --- Bare ticket references (29 Oct, "team text is teaching text") -------------
# Under --recipient team a ticket number never stands alone in a sentence. The words
# carry the link and the number rides at the end:
#   [the change that moved the shop onto one price list (#274)](https://github.com/o/r/pull/274)
# Flagged: a reference outside a markdown link, and a link whose text is only a
# reference (or has fewer than two words besides it). Never flagged: inline code,
# fenced code, and a link with real words in it.

_FENCE = re.compile(r'(?ms)^[ \t]*```.*?^[ \t]*```[ \t]*$')
_INLINE_CODE = re.compile(r'`[^`\n]+`')
_MD_LINK = re.compile(r'\[([^\]\n]*)\]\(([^)\n]*)\)')
_TICKET_REF = re.compile(
    r'(?:(?:\bPRs?|\bpull requests?|\bissues?)\s+)?'
    r'(?:[\w.-]+/[\w.-]+#\d+|(?<![\w&/])#\d+)(?!\w)', re.IGNORECASE)


# GitHub links a pull request to its issue only from a line like "Closes #N", so
# that syntax is allowed to stay bare.
_CLOSING_LINE = re.compile(
    r'(?im)^[ \t]*(?:[-*][ \t]+)?(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?[ \t].*$')


def _blank(m):
    return re.sub(r'[^\n]', ' ', m.group())


def mask_code(text):
    """Same text, same length, with fenced and inline code blanked out."""
    return _INLINE_CODE.sub(_blank, _FENCE.sub(_blank, text))


def mask_quotes(text):
    """Same text, same length, with blockquoted lines (starting with '>') blanked out."""
    return '\n'.join(' ' * len(line) if line.lstrip().startswith('>') else line
                    for line in text.split('\n'))


def is_mostly_quoted(text):
    """True if more than half of non-blank lines (outside code) are blockquoted."""
    lines = mask_code(text).split('\n')
    non_blank, quoted = 0, 0
    for line in lines:
        if not line.strip():
            continue
        non_blank += 1
        if line.lstrip().startswith('>'):
            quoted += 1
    return non_blank > 0 and quoted > (non_blank / 2)


def count_skipped(text):
    """(quoted_lines, code_blocks) skipped under team rules."""
    quoted = sum(1 for line in text.split('\n') if line.lstrip().startswith('>'))
    fenced = len(_FENCE.findall(text))
    return quoted, fenced


def _ref_context(text, start, end):
    return ' '.join(text[max(0, start - 20):end + 20].split())


def bare_ref_matches(text):
    """Ticket references that are not carried by words. Returns [message]."""
    masked = _CLOSING_LINE.sub(_blank, mask_quotes(mask_code(text)))
    out = []
    outside = list(masked)
    for m in _MD_LINK.finditer(masked):
        label = m.group(1)
        if _TICKET_REF.search(label):
            words = re.findall(r"[A-Za-z][A-Za-z'’-]*", _TICKET_REF.sub(' ', label))
            if len(words) < 2:
                out.append(f'link text "[{label}]" is only a ticket number (or has fewer than two words '
                           f'besides it). Put words in the link and the number at the end, e.g. '
                           f'"[the change that moved the shop onto one price list (#274)](url)". '
                           f'Seen in: "...{_ref_context(text, m.start(), m.end())}..."')
        for i in range(m.start(), m.end()):
            if outside[i] != '\n':
                outside[i] = ' '
    outside = _FACT_URL.sub(_blank, ''.join(outside))   # a web address is not a ticket number
    for m in _TICKET_REF.finditer(outside):
        out.append(f'{m.group().strip()}: a ticket number outside a link. Write it as words that carry the '
                   f'link with the number at the end, e.g. "[the change that moved the shop onto one price '
                   f'list (#274)](url)". Seen in: "...{_ref_context(text, m.start(), m.end())}..."')
    return out


# --- Fact keeper (29 Oct) ---------------------------------------------------------
# A rewrite may reword and reorder; it may not lose a fact. --keep-facts-from <orig>
# extracts the facts from the original and fails when the new text drops one or adds
# an @-mention (an added @-mention sends someone a notification). A fact is kept if
# it appears anywhere in the new text, <details> blocks and link text included.

_MONTHS = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*'
_FACT_URL = re.compile(r'https?://[^\s<>()\[\]"\'`]+')
_FACT_PATH = re.compile(
    r'(?<![\w/.])(?:~/[\w.@/-]*\w|(?:\.{0,2}/)?[\w.@-]+(?:/[\w.@-]+)+\.[A-Za-z]\w{0,5}(?!\w))')
_FACT_UNIT = re.compile(
    r'(?<![\w.])\d[\d,]*(?:\.\d+)?\s?(?:%|(?:ms|s|KB|MB|GB|px|h|min|hours?|days?)(?!\w))'
    r'|\$\d[\d,]*(?:\.\d+)?', re.IGNORECASE)
_FACT_DATE = re.compile(
    r'\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s' + _MONTHS + r'\b|\b' + _MONTHS + r'\s\d{1,2}\b'
    r'|\b\d{1,2}:\d{2}\s?(?:AM|PM)\b', re.IGNORECASE)
_FACT_REF = re.compile(r'(?:[\w.-]+/[\w.-]+#|(?<![\w&/])#)(\d+)(?!\w)')
_MENTION = re.compile(r'(?<![\w@/.])@[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[\w-]+)?')
_CHECKED = re.compile(r'(?m)^[ \t]*[-*+][ \t]+\[[xX]\]')
_UNCHECKED = re.compile(r'(?m)^[ \t]*[-*+][ \t]+\[ \]')


def _strip_url(u):
    return u.rstrip('.,;:!?)*_\'"')


def _code_facts(text):
    facts = []
    for m in _FENCE.finditer(text):
        body = m.group().split('\n')[1:-1] if m.group().count('\n') else []
        facts.extend(ln.strip() for ln in body if ln.strip())
    rest = _FENCE.sub(' ', text)
    facts.extend(m.group()[1:-1].strip() for m in _INLINE_CODE.finditer(rest)
                 if m.group()[1:-1].strip())
    return facts


def _squash(tok):
    return re.sub(r'\s+', '', tok.lower())


def extract_facts(text):
    """Facts in a text: {kind: [token, ...]} in first-seen order, no duplicates."""
    facts = {'code': _code_facts(text)}
    prose = mask_code(text)
    urls = [_strip_url(m.group()) for m in _FACT_URL.finditer(prose)]
    facts['url'] = urls
    prose = _FACT_URL.sub(lambda m: ' ' * len(m.group()), prose)
    facts['ref'] = [m.group(1) for m in _FACT_REF.finditer(prose)]
    facts['path'] = [m.group().rstrip('.,;:') for m in _FACT_PATH.finditer(prose)]
    prose = _FACT_PATH.sub(lambda m: ' ' * len(m.group()), prose)
    facts['unit'] = [m.group().strip() for m in _FACT_UNIT.finditer(prose)]
    facts['date'] = [m.group().strip() for m in _FACT_DATE.finditer(prose)]
    for k, v in facts.items():
        seen, uniq = set(), []
        for t in v:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        facts[k] = uniq
    return facts


def dropped_facts(original, new):
    """Problems when `new` drops a fact of `original` or adds an @-mention. Returns [message]."""
    problems = []
    low = new.lower()
    squashed = _squash(new)
    old = extract_facts(original)
    for u in old['url']:
        if u.lower() not in low:
            problems.append(f'dropped URL: {u}')
    for n in old['ref']:
        if not (re.search(rf'#{n}(?!\w)', new) or re.search(rf'/(?:issues|pull)/{n}(?!\w)', new)):
            problems.append(f'dropped ticket reference: #{n} (keep "#{n}" in link text or its issues/pull URL)')
    for c in old['code']:
        if c not in new:
            problems.append(f'dropped code: {c}')
    for pth in old['path']:
        if pth.lower() not in low:
            problems.append(f'dropped file path: {pth}')
    for kind, label in (('unit', 'number'), ('date', 'date or time')):
        for t in old[kind]:
            if t.lower() not in low and _squash(t) not in squashed:
                problems.append(f'dropped {label}: {t}')
    oc, ou = len(_CHECKED.findall(mask_code(original))), len(_UNCHECKED.findall(mask_code(original)))
    nc, nu = len(_CHECKED.findall(mask_code(new))), len(_UNCHECKED.findall(mask_code(new)))
    if (oc, ou) != (nc, nu):
        problems.append(f'checkbox counts differ: original {oc} checked / {ou} unchecked, '
                        f'new {nc} checked / {nu} unchecked')
    had = {m.group().lower() for m in _MENTION.finditer(original)}
    for m in _MENTION.finditer(mask_code(new)):
        if m.group().lower() not in had:
            problems.append(f'new @-mention: {m.group()} (an added @-mention sends that person a notification)')
            had.add(m.group().lower())
    return problems


def has_quoted_points(text):
    """True when at least one line quotes the recipient with '>'."""
    return any(line.lstrip().startswith('>') for line in text.split('\n'))



# ── THIRD-PERSON SELF-REFERENCE ───────────────────────────────────────────────
#
# The author is Jon. A draft whose body names him is written *about* him rather
# than *by* him, and the recipient notices immediately. Caught 2026-09-17 in a
# draft to Greg reading "Jon has the password" directly above Jon's own
# signature. This applies to EVERY recipient: it is a correctness fault, not a
# per-recipient preference, and no other check in this file looks for it.

# A bare first name only counts as self-reference when it is NOT part of someone
# else's full name. "Jonathan Harkins" is a membership applicant; "Jon has the
# password" is the author talking about himself in the third person. The negative
# lookahead for a following capitalised word draws that line, with the author's
# own surnames allowed through so "Jonathan Pohlner" still trips it.
SELF_NAMES = re.compile(
    r'\b(?:Jon|Jonathan|Turtle\s?Wolfe|TortoiseWolfe)\b'
    r'(?!\s+(?!Pohlner\b|Wolfe\b)[A-Z][a-z])'
)


URL_IN_PROSE = re.compile(r'https?://\S+')


def third_person_self(text):
    """Body PROSE naming the author. Sign-off, quotes and code blocks are exempt.

    Fenced blocks are skipped because the author's own username is legitimate
    DATA -- a participant roster, a log line, a uid. Both false positives this
    check has produced were of that shape ("Jonathan Harkins" the applicant, and
    "TurtleWolfe" in a call roster), never prose.
    """
    lines = text.split('\n')
    last = max((i for i, l in enumerate(lines) if l.strip()), default=-1)
    hits, fenced = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith('```'):
            fenced = not fenced
            continue
        if fenced:                         # data, not prose
            continue
        if i == last:                      # a bare name in the sign-off is correct
            continue
        if line.lstrip().startswith('>'):  # they may refer to Jon when quoted
            continue
        if line.startswith('    ') or line.startswith('\t'):
            continue                       # indented code block, same reasoning
        # A web address is not prose: github.com/TortoiseWolfe/... is a repo
        # path, not the author naming himself (26 Oct, a Raised Paws ticket link).
        if SELF_NAMES.search(URL_IN_PROSE.sub('', line)):
            hits.append(line.strip()[:70])
    return hits


# ── SENDER "WE" ───────────────────────────────────────────────────────────────
#
# Jonathan is a one-person studio. Client mail in his name says I / me / my.
# A draft that said "We recommend leaving them off. What we'll do, free: a daily
# check that tells us..." got "who is the royal we here?" (2026-10-12). For the
# co-op (greg, william, devops) "we" can honestly mean the co-op, so there it is
# only a warning.

CO_OP_RECIPIENTS = {'greg', 'william', 'devops'}

SENDER_WE = re.compile(
    r"\b(?:we(?:['\u2019](?:ll|d|ve|re))?|us|our|ours)\b", re.IGNORECASE)
LET_US = re.compile(r"\blet us\b", re.IGNORECASE)
# Words inside quote marks are somebody else's (the client's own words).
QUOTED_SPAN = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d')

WE_SUGGEST = {
    'we': 'I', "we'll": "I'll", "we'd": "I'd", "we've": "I've", "we're": "I'm",
    'us': 'me', 'our': 'my', 'ours': 'mine', 'let us': 'let me',
}


def sender_we(text):
    """Prose lines where the sender says "we". Returns [(phrase, suggestion, line)].

    Skipped: quoted reply lines (">"), fenced and indented code, and anything in
    quotation marks.
    """
    hits, fenced = [], False
    for line in text.split('\n'):
        if line.lstrip().startswith('```'):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith('>'):
            continue
        if line.startswith('    ') or line.startswith('\t'):
            continue
        prose = QUOTED_SPAN.sub(' ', line)
        for m in LET_US.finditer(prose):
            hits.append((m.group(), WE_SUGGEST['let us'], line.strip()[:80]))
        for m in SENDER_WE.finditer(prose):
            if LET_US.match(prose[max(0, m.start() - 4):m.end()]):
                continue  # the "us" of "let us", already reported
            key = m.group().lower().replace('\u2019', "'")
            hits.append((m.group(), WE_SUGGEST.get(key, 'I/me/my'), line.strip()[:80]))
    return hits


def sender_we_messages(text):
    return [f'"{ph}" in: "{ln}" -> write "{sg}" (one-person studio: I / me / my)'
            for ph, sg, ln in sender_we(text)]


# ── PROMISES FUTURE WORK ──────────────────────────────────────────────────────
#
# Standing rule (Jonathan, 2026-10-14): finish the open work before writing
# short-term emails about what we might do soon. Client drafts kept offering
# future work ("a fuller comparison if you want it", "I could set up", "plan to
# move"). An offer in a client email is either work that should already be done
# or a line that should be cut. Runs under --strict only.

PROMISE_PHRASES = [
    r"\bI(?:'|\u2019)ll\b",
    r"\bI will\b",
    r"\bI can(?!(?:'|\u2019)t|not)\b",
    r"\bI could\b",
    r"\bI(?:'|\u2019)d be happy to\b",
    r"\bhappy to\b",
    r"\bif you(?: want|(?:'|\u2019)d like| would like)\b",
    r"\bplan(?:ning)? to\b",
    r"\bsoon\b",
    r"\bnext week\b(?!(?:'|\u2019)s)",
    r"\bgoing to\b",
    r"\boffer\b",
]

# A line led by one of these is Jonathan's own words, quoted verbatim.
HIS_OWN_LINE = re.compile(r"^\s*(?:Jon|Jonathan)(?:\s+said)?\s*:", re.IGNORECASE)
# The quoted question above an answer, when it is not a ">" line.
QUESTION_LABEL = re.compile(r"^\s*(?:Q\d*\s*[:.)]|You (?:asked|wrote)\s*:)", re.IGNORECASE)


# "Can you...?" asks the reader for something. "Want a plan to move...?" is an
# offer in question form, so only the first shape is exempt.
QUESTION_TO_READER = re.compile(r"\s*(?:can|could|would|will) you\b", re.IGNORECASE)


def _prose_sentences(text):
    """Yield (sentence, line) for prose only. Skipped: quoted reply lines (">"),
    fenced and indented code, labelled quoted questions, lines marked as
    Jonathan's verbatim words ("Jon: ..."), and words inside quotation marks."""
    fenced = False
    for line in text.split('\n'):
        if line.lstrip().startswith('```'):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith('>'):
            continue
        if line.startswith('    ') or line.startswith('\t'):
            continue
        if HIS_OWN_LINE.match(line) or QUESTION_LABEL.match(line):
            continue
        prose = QUOTED_SPAN.sub(' ', line)
        for sentence in re.findall(r'[^.!?\n]+[.!?]?', prose):
            yield sentence, line


def promises_future_work(text):
    """Prose offers of future work. Returns [(phrase, line)].

    Also skipped: any "Can/Could/Would/Will you...?" question to the reader.
    """
    hits = []
    for sentence, line in _prose_sentences(text):
        if QUESTION_TO_READER.match(sentence) and sentence.rstrip().endswith('?'):
            continue
        for pattern in PROMISE_PHRASES:
            for m in re.finditer(pattern, sentence, re.IGNORECASE):
                hits.append((m.group(), line.strip()[:80]))
    return hits


# Standing rule (Jonathan, 2026-10-19): finish the open work before staging any
# outgoing mail. A draft that says what is "still open" tells the client what we
# did not do; it belongs at the top of the to-do list, and the draft waits. This
# is a HOLD, not a style nit. Never trim the line to hide the item. Runs under
# --strict only.
OPEN_WORK_PHRASES = [
    r"\bstill open\b",
    r"\bstill (?:to be|need(?:s|ed)? to be) (?:done|built|written|fixed|set up|added)\b",
    r"\bnot (?:done )?yet\b",
    r"\bnext up\b",
    r"\b(?:we|I)(?:'|\u2019)ll get to\b",
    r"\b(?:we|I) plan\b",
    r"\bon (?:the|my) (?:list|to-?do)\b",
    r"\bhaven(?:'|\u2019)t (?:yet )?(?:done|finished|started|built)\b",
]


def open_work_hits(text):
    """Phrases that report unfinished work. Returns [(phrase, line)]."""
    hits = []
    for sentence, line in _prose_sentences(text):
        for pattern in OPEN_WORK_PHRASES:
            for m in re.finditer(pattern, sentence, re.IGNORECASE):
                hits.append((m.group(), line.strip()[:80]))
    return hits


def reader_questions(text):
    """Prose sentences that ask the reader something. Returns [sentence].

    We research and decide technical questions ourselves; a question stays only
    when just the reader can know the answer (then pass --only-they-know).
    """
    return [sentence.strip()[:80] for sentence, _ in _prose_sentences(text)
            if sentence.rstrip().endswith('?')]


def promise_messages(text):
    return [f'"{ph}" in: "{ln}" -> do the work first, or cut the line'
            for ph, ln in promises_future_work(text)]


def open_work_messages(text):
    return [f'"{ph}" in: "{ln}" -> HOLD: that is open work. Do it first (a lane, or the top of '
            f'the roadmap to-do list), then say what was done. Never trim the line or tell '
            f'the client what was not done' for ph, ln in open_work_hits(text)]


def question_messages(text):
    return [f'"{q}" -> HOLD: research and decide it ourselves. Keep a question only if just '
            f'the reader can know the answer, then rerun with --only-they-know'
            for q in reader_questions(text)]


RECORD_CLAIM_PATTERNS = [
    r"\b(?:I|we|you|they|he|she|it|this|that)(?:['’](?:ve|s|d))?\s+"
    r"(?:(?:have|has|had|is|are|was|were)\s+)?"
    r"(?:recorded|noted|filed|logged|tracked|ticketed)\b",
    r"\b(?:recorded|noted|filed|logged|tracked|ticketed)\s+"
    r"(?:it|this|that|these|those|as|in|on|to)\b",
    r"\b(?:wrote|write|written)\s+(?:it|this|that)\s+up\b",
    r"\b(?:it|this|that)(?:['’]s)?\s+(?:(?:is|was|has been|had been|went|goes)\s+)?on\s+"
    r"(?:the|my|our|your)\s+(?:to-?do\s+)?list\b",
    r"\bput\s+(?:it|this|that|(?:the|a|an|my|our|your)\s+\w+)\s+on\s+"
    r"(?:the|my|our|your|a|an)\s+"
    r"(?:to-?do\s+)?list\b",
]


def _record_claim_matches(text):
    matches = sorted((m for pattern in RECORD_CLAIM_PATTERNS
                      for m in re.finditer(pattern, text, re.IGNORECASE)),
                     key=lambda m: (m.start(), -(m.end() - m.start())))
    unique = []
    for match in matches:
        if any(match.start() < prior.end() and match.end() > prior.start() for prior in unique):
            continue
        following = text[match.end():]
        if re.match(r"\s+(?:(?:a|an|the)\s+)?(?:recorded\s+)?"
                    r"(?:call|video|meeting|audio|screen|session|demo|conversation|interview)\b",
                    following, re.IGNORECASE):
            continue
        unique.append(match)
    return unique


def record_claim_messages(text, html=None):
    """Recorded-work claims without a link in the same paragraph or list item."""
    if html is None:
        phrases = [match.group() for sentence, _ in _prose_sentences(text)
                   for match in _record_claim_matches(sentence)]
        return [f'"{phrase}" -> add the URL where this is recorded' for phrase in phrases]

    lines, _ = letter_lines(html)
    grouped = {}
    for index, line in enumerate(lines):
        if line.get('li_id') is not None:
            key = ('li', line['li_id'])
        elif line.get('paragraph_id') is not None:
            key = ('p', line['paragraph_id'])
        else:
            key = ('line', index)
        group = grouped.setdefault(key, {'text': [], 'anchors': []})
        group['text'].append(line['text'])
        group['anchors'].extend(line.get('anchors', []))
    messages = []
    for group in grouped.values():
        if any(anchor.get('href') for anchor in group['anchors']):
            continue
        paragraph = ' '.join(group['text'])
        for match in _record_claim_matches(paragraph):
            messages.append(f'"{match.group()}" -> add a link in the same paragraph or list item '
                            f'where this is recorded ("{paragraph[:80]}")')
    return messages


class _ReplyParagraph(HTMLParser):
    """Capture the first paragraph after a blockquote in an HTML email."""

    def __init__(self):
        super().__init__()
        self.quote_depth = 0
        self.after_quote = False
        self.paragraph_depth = 0
        self.paragraph_found = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'blockquote':
            self.quote_depth += 1
        elif tag == 'p' and self.after_quote and self.quote_depth == 0 \
                and not self.paragraph_found and self.paragraph_depth == 0:
            self.paragraph_depth = 1
            self.paragraph_found = True
        elif tag == 'p' and self.paragraph_depth:
            self.paragraph_depth += 1

    def handle_endtag(self, tag):
        if tag == 'blockquote' and self.quote_depth:
            self.quote_depth -= 1
            if self.quote_depth == 0:
                self.after_quote = True
        elif tag == 'p' and self.paragraph_depth:
            self.paragraph_depth -= 1

    def handle_data(self, data):
        if self.paragraph_depth:
            self.text.append(data)


def flat_answer_message(html):
    """Warn when the first reply paragraph starts with a bare yes or no."""
    parser = _ReplyParagraph()
    parser.feed(html)
    paragraph = re.sub(r'\s+', ' ', ''.join(parser.text)).strip()
    if re.match(r'^(?:no|yes|nope|yep)[.!?](?:\s|$)', paragraph, re.IGNORECASE):
        return (f'"{paragraph[:80]}" -> say how far it got instead of opening with a flat '
                'yes or no (for example, "It\'s prepared but not running: <what proves it>")')
    return None


# ── ASKS THE READER FOR ACCESS ────────────────────────────────────────────────
#
# Standing rule (Jonathan, 31 Oct 2026): never ask a client for access until our own
# records and a live check prove we lack it. A mail lane drafted a reply asking Gail to
# add us as a Viewer on her Google Analytics; we had held that access since her 15 Oct
# call, and the lane had checked only one route. Any sentence that asks the reader for
# access, an invite, a sign-in or a password is flagged. Under --strict it is a hard FAIL
# unless the caller passes --access-proof "<the access record and the live check>".
# Without --strict it only warns. Off for --recipient team (tickets, not client mail).
ACCESS_ASK_PATTERNS = [
    r"\badd\s+(?:me|us|jonathan|\S+@\S+)\s+(?:as|to)\b",
    r"\badd\s+(?:me|us)\b",
    r"\bas\s+(?:a\s+|an\s+)?(?:viewer|read-only|editor|administrator|admin|collaborator)\b",
    r"\binvite\s+(?:me|us|jonathan)\b",
    r"\b(?:give|grant|get|provide)\s+(?:me|us)\b(?:\s+\w+){0,3}\s+(?:access|login|password|credentials)\b",
    r"\bgrant\b(?:\s+\w+){0,2}\s+access\b",
    r"\bshare\b(?:\s+\w+){0,2}\s+access\b",
    r"\b(?:sign-?in|log-?in|login)\s+for\b",
    r"\badmin(?:istrator)?\s+access\b",
    r"\bsend\s+(?:me|us)\s+(?:\w+\s+){0,2}(?:password|login|credentials)\b",
    r"\b(?:I|we)\s+(?:need|want|would\s+like)(?:\s+to\s+get)?\s+(?:\w+\s+)?access\b",
]
ACCESS_PROOF_MIN = 20


def access_ask_hits(text):
    """Prose sentences that ask the reader for access. Returns [sentence]."""
    hits = []
    for sentence, _ in _prose_sentences(text):
        if any(re.search(p, sentence, re.IGNORECASE) for p in ACCESS_ASK_PATTERNS):
            hits.append(sentence.strip()[:120])
    return hits


def access_ask_messages(text):
    return [f'"{sent}" -> asks the reader for access. Read the client repo\'s access record '
            f'(Harmonic Egg: docs/access-inventory.md), the call decisions and hub-ask.py, and '
            f'try the access live. Then rerun with --access-proof "<the record and the live '
            f'check that show we lack it>", or cut the ask' for sent in access_ask_hits(text)]


def access_proof_ok(proof):
    return proof is not None and len(proof.strip()) >= ACCESS_PROOF_MIN



# ── CO-OP MAIL FORMAT (HTML) ──────────────────────────────────────────────────
#
# Since 17 Oct 2026 mail to Greg and the devops list is a letter first, with real
# <h2> headings, bold links and flat <ul> bullets allowed; open questions stay
# typed, numbered plain text; never tables, cards or markdown symbols. Twice a lane
# staged it in the old plain shape anyway (18 Oct; 21 Oct draft r-6335421503936443088:
# plain lines joined by <br/>, titles as plain lines, "- " typed bullets, a bare
# link) and this checker passed it, because it only ever read the text. So the gate
# reads the HTML (--html <file>). The quoted part of a reply never counts: lines
# starting ">", any <blockquote>, and everything from the "On ... wrote:" line down.
#
# Links are a separate, wider rule (Jonathan, 21 Oct 5:40 PM, for ALL mail): anything that
# can be a clickable link is one, in bold. With an HTML body, every recipient's address or URL
# must sit in <b><a href="https://...">..</a></b> (link_matches). The Gmail connector still
# rewrites hrefs into google.com/url redirects (probed 21 Oct), so a lane checks RAW afterwards.

FORMAT_RECIPIENTS = {'greg', 'devops'}

_BLOCK_TAGS = {'p', 'div', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
               'br', 'tr', 'table', 'pre', 'section'}
_HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
_ON_WROTE = re.compile(r'^On .{5,250} wrote:$')


class _MailLines(HTMLParser):
    """Split a mail body into lines, remembering what each line sat inside."""

    def __init__(self):
        super().__init__()
        self.lines = []          # dicts: segs [(text, in_a, in_bold)], in_li
        self.cur = []
        self.cur_li = False
        self.li_stack = []
        self.next_li_id = 0
        self.paragraph_stack = []
        self.next_paragraph_id = 0
        self.cur_anchors = []    # anchors that start on the current line
        self.open_anchor = None
        self.a = self.bold = self.li = self.quote = self.code = 0
        self.headings = 0
        self.quote_at = []       # len(self.lines) where each top-level <blockquote> starts

    def _flush(self):
        segs = [(re.sub(r'\s+', ' ', t), a, b) for t, a, b in self.cur]
        segs = [x for x in segs if x[0].strip()]
        if segs or self.cur_anchors:
            self.lines.append({'segs': segs, 'in_li': self.cur_li, 'li_id': self.li_stack[-1]
                               if self.li_stack else None, 'paragraph_id': self.paragraph_stack[-1]
                               if self.paragraph_stack else None, 'anchors': self.cur_anchors})
        self.cur, self.cur_li, self.cur_anchors = [], False, []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag == 'blockquote':
            if not self.quote:
                self.quote_at.append(len(self.lines))
            self.quote += 1
        elif tag == 'a':
            self.a += 1
            if not self.quote:
                self.open_anchor = {'href': (dict(attrs).get('href') or '').strip(), 'text': '',
                                    'bold_start': self.bold > 0, 'data_bold': True, 'has_data': False}
                self.cur_anchors.append(self.open_anchor)
        elif tag in ('b', 'strong'):
            self.bold += 1
        elif tag == 'code':
            self.code += 1          # a value to type (a DNS target), not a page to open
        elif tag == 'li':
            self.li += 1
            self.next_li_id += 1
            self.li_stack.append(self.next_li_id)
        elif tag == 'p':
            self.next_paragraph_id += 1
            self.paragraph_stack.append(self.next_paragraph_id)
        elif tag in _HEADING_TAGS and not self.quote:
            self.headings += 1

    def handle_endtag(self, tag):
        if tag == 'br':
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag == 'blockquote':
            self.quote = max(0, self.quote - 1)
        elif tag == 'a':
            self.a = max(0, self.a - 1)
            self.open_anchor = None
        elif tag in ('b', 'strong'):
            self.bold = max(0, self.bold - 1)
        elif tag == 'code':
            self.code = max(0, self.code - 1)
        elif tag == 'li':
            self.li = max(0, self.li - 1)
            if self.li_stack:
                self.li_stack.pop()
        elif tag == 'p':
            if self.paragraph_stack:
                self.paragraph_stack.pop()

    def handle_data(self, data):
        if self.quote or not data.strip():
            return
        if not self.cur:
            self.cur_li = self.li > 0
        self.cur.append((data, self.a > 0 or self.code > 0, self.bold > 0))
        if self.open_anchor is not None and data.strip():
            self.open_anchor['text'] += data
            self.open_anchor['has_data'] = True
            self.open_anchor['data_bold'] = self.open_anchor['data_bold'] and self.bold > 0


def letter_lines(html):
    """(lines, heading_count) of the letter part of an HTML body, quote removed."""
    parser = _MailLines()
    parser.feed(html)
    parser.close()
    parser._flush()
    out = []
    for ln in parser.lines:
        text = ''.join(t for t, _, _ in ln['segs']).strip()
        if _ON_WROTE.match(text):
            break                       # everything below is the quoted thread
        if text.startswith('>'):
            continue                    # a quoted point
        ln['text'] = text
        out.append(ln)
    return out, parser.headings


def _is_title_line(ln, is_last):
    text = ln['text']
    words = text.split()
    if is_last or ln['in_li'] or not 2 <= len(words) <= 12:
        return False
    if text[-1] in '.?!:,;' or re.match(r'^\d+[.)]\s', text):
        return False
    if all(b for _, _, b in ln['segs']):
        return False                    # a bold line is a bold link/title, allowed
    return True


# A web address in the letter: a URL, or a bare hostname with a common TLD and optional path.
_ADDRESS = re.compile(r'(?:https?://|www\.)[^\s<>")]+|'
                      r'(?<![@\w.-])[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:digital|com|net|org|io|dev|app|co|edu|gov)\b(?:/[^\s<>")]*)?',
                      re.IGNORECASE)


# A ticket named in words ("cd-nextcloud issue 18", "PR 24", "Deck card 16", "#312", "owner/repo#12") can be a
# link, so outside an anchor it fails like a bare address (Jonathan, 1 Nov 2026, on a Greg draft that passed:
# "anything that can be a clickable link should be ... cd-nextcloud issue 18 and Chattanooga-Digital issue 312").
_TICKET_WORDS = re.compile(
    r'(?i)\b(?:issues?|PRs?|pull\s+requests?|tickets?|(?:deck\s+)?cards?)\s+#?\d+\b'
    r'|\b[\w.-]+/[\w.-]+#\d+\b'
    r'|(?<![\w&/#])#\d+\b')


def quoted_topics(html):
    """How many of his points the letter quotes (top-level <blockquote> or "&gt;" lines), above "On ... wrote:"."""
    parser = _MailLines()
    parser.feed(html)
    parser.close()
    parser._flush()
    cut = len(parser.lines)
    for i, ln in enumerate(parser.lines):
        if _ON_WROTE.match(''.join(t for t, _, _ in ln['segs']).strip()):
            cut = i
            break
    gt = sum(1 for ln in parser.lines[:cut] if ''.join(t for t, _, _ in ln['segs']).strip().startswith('>'))
    return sum(1 for q in parser.quote_at if q < cut) + gt


def format_matches(html):
    """Old plain-text shape in the HTML of a co-op letter. Returns [(category, message)]."""
    lines, headings = letter_lines(html)
    found = []
    bullets = [l['text'] for l in lines
               if not l['in_li'] and re.match(r'^[-*\u2022]\s+\S', l['text'])]
    if bullets:
        found.append(('format_bullets', f'{len(bullets)} typed bullet line(s), e.g. "{bullets[0][:60]}" '
                      '-> use a real <ul><li> list, not "- " or "* " at the start of a line'))
    md = [l['text'] for l in lines if '**' in l['text'] or re.match(r'^#{1,6}\s', l['text'])]
    if md:
        found.append(('format_markdown', f'markdown symbols in the HTML, e.g. "{md[0][:60]}" '
                      '-> use real <h2> for headings and <b> for bold, never "#" or "**"'))
    last = len(lines) - 1
    titles = [l['text'] for i, l in enumerate(lines) if _is_title_line(l, i == last)]
    if len(titles) >= 2 and not headings:
        found.append(('format_headings', f'{len(titles)} topic sections written as plain lines '
                      f'(e.g. "{titles[0][:50]}") and no <h2> -> give each topic a real <h2> heading'))
    # A many-topic letter with no headings and no list reads as a wall of text (Jonathan, 1 Nov 2026, on a Greg
    # reply of seven quoted points and nine paragraphs: "it's the lack of formatting"). His quotes are not headings.
    topics = quoted_topics(html)
    has_list = any(l['in_li'] for l in lines)
    if not headings and not has_list and (topics >= 3 or len(lines) >= 8) \
            and not any(c == 'format_headings' for c, _ in found):
        found.append(('format_headings', f'{topics} quoted point(s) and {len(lines)} paragraph(s) with no <h2> heading '
                      'and no list -> give each topic a real <h2> heading (his quote can sit under it), put any '
                      'list in <ul><li>, keep every link bold'))
    return found


def link_matches(html):
    """Every recipient with an HTML body: anything clickable is a bold, working link.

    An address or URL in the letter must sit in an <a href="https://..."> wrapped in <b> or
    <strong>. Returns [(category, message)]. The quoted part never counts."""
    lines, _ = letter_lines(html)
    bare, plain_anchors, bad_href, refs = [], [], [], []
    for ln in lines:
        for text, in_a, _ in ln['segs']:
            if not in_a:
                bare.extend(_ADDRESS.findall(text))
                refs.extend(m.group() for m in _TICKET_WORDS.finditer(text))
        for a in ln.get('anchors', []):
            label = (a['text'].strip() or a['href'])[:60]
            if not re.match(r'(?i)^https?://\S+', a['href']):
                if not a['href'].lower().startswith('mailto:'):
                    bad_href.append(label)
            elif not (a['bold_start'] or (a['has_data'] and a['data_bold'])):
                plain_anchors.append(label)
    found = []
    if bare:
        found.append(('format_links', f'{len(bare)} address(es) or URL(s) not a link, e.g. "{bare[0][:60]}" '
                      '-> make each a clickable link in bold: <b><a href="https://...">text</a></b>'))
    if refs:
        found.append(('format_links', f'{len(refs)} ticket reference(s) not a link, e.g. "{refs[0][:60]}" '
                      '-> link each one in bold to its page: <b><a href="https://github.com/OWNER/REPO/issues/N">'
                      'repo issue N</a></b>'))
    if plain_anchors:
        found.append(('format_links', f'{len(plain_anchors)} link(s) not bold, e.g. "{plain_anchors[0]}" '
                      '-> wrap each link in <b> (or <strong>)'))
    if bad_href:
        found.append(('format_links', f'{len(bad_href)} link(s) with no real https:// address, e.g. "{bad_href[0]}" '
                      '-> the href must start with https:// (a bare host becomes javascript:void(0) in the connector)'))
    return found


def house_rule_matches(text, recipient='greg', is_reply=False, recipient_explicit=True, html=None):
    """
    Hard failures for a named recipient. Returns [(category, message)].
    Empty for recipients with no house limit (William, and anyone unlisted).
    """
    found = []
    selfref = third_person_self(text)
    if selfref:
        found.append(('third_person', f'author named in the body: "{selfref[0]}"'))
    # "we" is a hard fail for clients and for an unnamed recipient; the co-op
    # list gets a warning instead (see detect_llm_signals).
    # Team text is written by staff in their own voice, so the rule is off there (29 Oct).
    if not (recipient_explicit and (recipient or '').lower() in CO_OP_RECIPIENTS) \
            and (recipient or '').lower() != 'team':
        for msg in sender_we_messages(text):
            found.append(('sender_we', msg))
    if html is not None:
        if (recipient or '').lower() in FORMAT_RECIPIENTS:
            found.extend(format_matches(html))
        found.extend(link_matches(html))
    rcpt = (recipient or '').lower()
    if rcpt not in ('greg', 'team'):
        return found
    if rcpt == 'team' and is_mostly_quoted(text):
        found.append(('mostly_quoted', "mostly quoted: write the opening in our own words"))
    words = count_body_words(text)
    if rcpt == 'greg' and words > GREG_WORD_LIMIT:
        found.append(('length', f"{words} words, limit {GREG_WORD_LIMIT}"))
    # Team text keeps its commands in code; a literal command is not prose to gloss.
    jargon = find_unglossed_jargon(mask_quotes(mask_code(text)) if rcpt == 'team' else text)
    if jargon:
        found.append(('jargon', ', '.join(f'"{j}"' for j in jargon[:8])
                      + " with no gloss in the same sentence. Keep the term if it is the right one "
                        "and explain it in that sentence (e.g. \"..., which means ...\"). "
                        "Never swap a correct technical term for a vaguer word."))
    if rcpt == 'greg' and is_reply and not has_quoted_points(text):
        found.append(('no_quoting', "a reply with no '>' quoted points to answer under"))
    return found


def detect_llm_signals(text, strict=False, recipient='greg', is_reply=False,
                       recipient_explicit=True, only_they_know=False, html=None,
                       access_proof=None):
    """
    Analyze an email body for AI-ish voice.

    Returns dict: signal_count, structural_count, matches [(category, match)],
    contraction_ratio, dash_count, verdict (PASS / FLAG / MAJOR FLAG / FAIL).
    """
    matches = []
    recipient_low = (recipient or '').lower()
    is_team = recipient_low == 'team'
    check_text = mask_quotes(mask_code(text)) if is_team else text

    skipped_note = None
    if is_team:
        q, c = count_skipped(text)
        if q or c:
            parts = []
            if q: parts.append(f"{q} quoted line{'s' if q != 1 else ''}")
            if c: parts.append(f"{c} code block{'s' if c != 1 else ''}")
            skipped_note = f"note: {' and '.join(parts)} not judged (someone else's words)"

    for pattern in FORMAL_PHRASES:
        for m in re.finditer(pattern, check_text, re.IGNORECASE):
            matches.append(('formal_phrase', m.group()))
    for pattern in EMAIL_TELLS:
        for m in re.finditer(pattern, check_text, re.IGNORECASE):
            matches.append(('email_tell', m.group()))
    for pattern in HEDGING_PHRASES:
        for m in re.finditer(pattern, check_text, re.IGNORECASE):
            matches.append(('hedging', m.group()))
    for pattern in GENERIC_PRAISE:
        for m in re.finditer(pattern, check_text, re.IGNORECASE):
            matches.append(('generic_praise', m.group()))

    expanded, contracted = count_contractions(check_text)
    contraction_ratio = f"{expanded} expanded / {contracted} contracted"
    if expanded >= 3 and contracted == 0:
        matches.append(('no_contractions', f"{expanded} expanded forms, 0 contractions"))
    elif expanded >= 5 and contracted <= 1:
        matches.append(('few_contractions', f"{expanded} expanded forms, only {contracted} contractions"))

    structural = structural_matches(check_text)
    matches.extend(structural)

    # Under --recipient team these four email-only rules are off: a ticket is where plans
    # live and TechJoy's PR template asks the author reflection questions (29 Oct).
    if strict and (recipient or '').lower() != 'team':
        for msg in promise_messages(text):
            matches.append(('promises_future_work', msg))
        for msg in open_work_messages(text):
            matches.append(('open_work', msg))
        if not only_they_know:
            for msg in question_messages(text):
                matches.append(('question_to_reader', msg))

    signal_count = len(matches)
    structural_count = len(structural)

    if signal_count <= 2:
        verdict = "PASS"
    elif signal_count <= 4:
        verdict = "FLAG"
    else:
        verdict = "MAJOR FLAG"

    # Any structural tell forces at least FLAG; 3+ forces MAJOR FLAG.
    if structural_count >= 1 and verdict == "PASS":
        verdict = "FLAG"
    if structural_count >= 3:
        verdict = "MAJOR FLAG"

    # Strict = zero-tolerance self-gate for an email you're about to send.
    if strict and signal_count >= 1:
        verdict = "FAIL"

    # House rules are NOT voice tells and are never advisory. Any one of them is
    # a hard FAIL, above every other verdict, because these are the three things
    # the recipient has objected to in his own words.
    house = house_rule_matches(text, recipient=recipient, is_reply=is_reply,
                               recipient_explicit=recipient_explicit, html=html)
    warnings = []
    if html is None and (recipient or '').lower() in FORMAT_RECIPIENTS:
        unchecked = ('mail to Greg or the devops list: the HTML format '
                     '(real <h2>, <ul>, bold addresses) was NOT checked. Pass the HTML body with --html <file>')
        if strict:
            house = list(house) + [('format_unchecked', unchecked)]
        else:
            warnings.append(('format_unchecked', unchecked))
    if recipient_explicit and (recipient or '').lower() in CO_OP_RECIPIENTS:
        warnings += [('sender_we', m) for m in sender_we_messages(text)]
    # An unexplained acronym warns; under --strict it is a hard FAIL (27 Oct rule).
    acr = [('acronym', m) for m in acronym_messages(text)]
    if strict:
        house = list(house) + acr
    else:
        warnings += acr
    # Asking the reader for access (31 Oct): hard FAIL under --strict without proof; else a warning.
    access_note = None
    if (recipient or '').lower() != 'team':
        asks = access_ask_messages(text)
        if asks and strict and access_proof_ok(access_proof):
            access_note = f'access ask(s) allowed on proof: {access_proof.strip()}'
        elif asks and strict:
            house = list(house) + [('access_ask', m) for m in asks]
        else:
            warnings += [('access_ask', m) for m in asks]
    # Team text is teaching text (29 Oct): a ticket number is never bare. Hard FAIL.
    if (recipient or '').lower() == 'team':
        house = list(house) + [('bare_ref', m) for m in bare_ref_matches(text)]
    record_claims = record_claim_messages(text, html=html)
    if strict:
        house = list(house) + [('record_claim', m) for m in record_claims]
    else:
        warnings += [('record_claim', m) for m in record_claims]
    if html is not None:
        flat_answer = flat_answer_message(html)
        if flat_answer and strict:
            house = list(house) + [('flat_answer', flat_answer)]
        elif flat_answer:
            warnings.append(('flat_answer', flat_answer))
    if house:
        verdict = "FAIL"

    return {
        'signal_count': signal_count,
        'structural_count': structural_count,
        'matches': matches,
        'house': house,
        'warnings': warnings,
        'body_words': count_body_words(text),
        'recipient': recipient,
        'contraction_ratio': contraction_ratio,
        'dash_count': count_mid_sentence_dashes(check_text),
        'verdict': verdict,
        'strict': strict,
        'skipped_note': skipped_note,
        'access_note': access_note,
    }


def main():
    args = sys.argv[1:]
    strict = "--strict" in args
    is_reply = "--reply" in args
    only_they_know = "--only-they-know" in args
    # A value starting with '--' is a flag, never a recipient. Without this,
    # `--recipient --strict` consumed '--strict' as the recipient name, every
    # house rule was skipped as "not greg", and the script printed PASS and
    # exited 0. Silent pass on a flag-ordering accident: the worst failure a
    # gate can have. Measured 2026-08-22.
    recipient = 'greg'
    recipient_explicit = False
    for i, a in enumerate(args):
        if a == '--recipient':
            if i + 1 < len(args) and not args[i + 1].startswith('--'):
                recipient = args[i + 1]
                recipient_explicit = True
            else:
                print("ERROR: --recipient needs a name (e.g. --recipient greg).")
                sys.exit(2)
    html = None
    html_path = None
    for i, a in enumerate(args):
        if a == '--html':
            if i + 1 < len(args) and not args[i + 1].startswith('--'):
                html_path = args[i + 1]
            else:
                print("ERROR: --html needs a file holding the HTML body.")
                sys.exit(2)
    if html_path is not None:
        hp = Path(html_path)
        if not hp.exists():
            print(f"ERROR: --html file not found: {html_path}")
            sys.exit(2)
        html = hp.read_text()
    access_proof = None
    for i, a in enumerate(args):
        if a == '--access-proof':
            if i + 1 < len(args) and not args[i + 1].startswith('--'):
                access_proof = args[i + 1]
            else:
                print('ERROR: --access-proof needs text naming the access record and the live check.')
                sys.exit(2)
    facts_path = None
    for i, a in enumerate(args):
        if a == '--keep-facts-from':
            if i + 1 < len(args) and not args[i + 1].startswith('--'):
                facts_path = args[i + 1]
            else:
                print("ERROR: --keep-facts-from needs a file holding the original text.")
                sys.exit(2)
    facts_original = None
    if facts_path is not None:
        fp = Path(facts_path)
        if not fp.exists():
            print(f"ERROR: --keep-facts-from file not found: {facts_path}")
            sys.exit(2)
        facts_original = fp.read_text()
    skip = {"--strict", "--reply", "--only-they-know", "--recipient", recipient, "--html",
            "--keep-facts-from", "--access-proof"}
    if access_proof is not None:
        skip.add(access_proof)
    if html_path is not None:
        skip.add(html_path)
    if facts_path is not None:
        skip.add(facts_path)
    args = [a for a in args if a not in skip]

    if not args:
        print("Usage: python3 email_voice_check.py <text_file> [--strict]")
        print("       python3 email_voice_check.py --stdin [--strict] [--recipient greg|devops --html <body.html>]")
        print("\nScreens a client-email body for AI-ish voice (lexical + structural).")
        print("\nVerdicts (default):")
        print("  PASS       (0-2 signals, 0 structural)")
        print("  FLAG       (3-4 signals, OR any 1-2 structural tells)")
        print("  MAJOR FLAG (5+ signals, OR 3+ structural tells)")
        print("\n--strict: zero-tolerance self-gate — ANY signal = FAIL. Use before sending.")
        print("--recipient <name>: house rules apply to 'greg' (default). Others exempt.")
        print("--reply: this is a reply, so it must quote the recipient's points with '>'.")
        print("\nHouse rules for greg (each is a hard FAIL, not a flag):")
        print(f"  length  — body over {GREG_WORD_LIMIT} words")
        print("  jargon  — technical terms with no plain-English gloss in the same sentence.")
        print("            Keep a correct term and explain it in that sentence (\", which means ...\");")
        print("            never swap it for a vaguer word.")
        print("  quoting — a reply that never quotes his points back with '>'")
        print("\n--html <file>: the HTML body, for greg and devops. Hard FAIL on the old plain shape (17 Oct rule):")
        print("  [format_bullets] typed '- ' / '* ' bullets   [format_markdown] '#' or '**'")
        print("  [format_headings] 2+ topic titles as plain lines with no <h2>   [format_links] (every recipient) an address or URL not a bold <a href=\"https://...\"> link")
        print("  The quoted part ('>' lines, <blockquote>, below 'On ... wrote:') never counts. Without --html a")
        print("  warning says the format was not checked.")
        print("\nRecorded-work claims (every recipient): [record_claim] add a URL or HTML link beside anything you say")
        print("  you recorded, noted, wrote up, filed, logged, tracked or put on a list. Warns by default; fails under --strict.")
        print("  [flat_answer] in HTML replies, do not open the first paragraph after a blockquote with a bare Yes/No.")
        print("  Say how far it got instead (for example, \"It's prepared but not running: <what proves it>\").")
        print("  Warns by default; fails under --strict.")
        print("\nAcronyms (every recipient): [acronym] a word of 2-5 capitals needs a plain explanation at its first use,")
        print("  e.g. CSS (Cascading Style Sheets, the code that controls how a page looks), or spelled out first:")
        print("  Cascading Style Sheets (CSS). Warns by default, hard FAIL under --strict. Skipped: URLs, addresses,")
        print("  code, '>' lines, [CC>MUSE] tags, and a short allow list (AM PM OK US ID ET ...).")
        print("\nTeam text (--recipient team, 29 Oct 'team text is teaching text'): hard FAIL on [bare_ref] a ticket number")
        print("  (#274, PR #274, owner/repo#274) outside a link, or a link whose text is only the number or has fewer")
        print("  than two words besides it; and [jargon] as for greg (no word limit). Code is exempt.")
        print("  A line starting Closes/Fixes/Resolves #N (any case) is exempt: GitHub needs that syntax. Under team the")
        print("  email-only rules are OFF: [question_to_reader], [promises_future_work], [open_work], [sender_we].")
        print("  --keep-facts-from <original>: FAIL, under 'FACTS DROPPED:', when the new text loses a URL, ticket")
        print("  number, code, file path, number with unit, date or time, changes the checkbox counts, or adds an @-mention.")
        print("\nSender \"we\" (every recipient except greg/william/devops, and when no --recipient is given):")
        print("  [sender_we] we/we'll/us/our/let us in prose = hard FAIL. Jonathan is one person: I / me / my.")
        print("  For greg/william/devops it is a warning only. Quoted ('>') lines and \"quoted\" words are skipped.")
        print("\nPromises future work (--strict only):")
        print("  [promises_future_work] I'll / I can / I could / happy to / if you want / plan to / soon /")
        print("  next week / going to / offer. Do the work first, or cut the line. Skipped: '>' lines,")
        print("  questions to the reader, \"quoted\" words, and lines starting 'Jon:' (his verbatim words).")
        print("\nOpen work and questions (--strict only; a HOLD, not a style nit):")
        print("  [open_work] still open / not yet / next up / we plan / on my list. Do the work first, then")
        print("  report it done; never trim the line. [question_to_reader] any prose question to the reader:")
        print("  research and decide it ourselves; add --only-they-know once only the reader can answer.")
        print("\nAsks the reader for access (every recipient except team):")
        print("  [access_ask] a sentence asking for access: add <address> as a Viewer, invite me, give me access,")
        print("  grant/share access, sign-in/login for, admin access, add me to, send me the password. Warns by")
        print("  default; under --strict a hard FAIL naming each sentence unless --access-proof \"<the access")
        print("  record and the live check that show we lack it>\" is given (20+ characters).")
        print("\nExit codes: 0 = PASS. Non-zero for FLAG, MAJOR FLAG and FAIL alike,")
        print("so `check && post` is safe. Previously a FLAG exited 0.")
        sys.exit(1)

    if args[0] == '--stdin':
        text = sys.stdin.read()
    else:
        path = Path(args[0])
        if not path.exists():
            print(f"File not found: {args[0]}")
            sys.exit(1)
        text = path.read_text()

    result = detect_llm_signals(text, strict=strict, recipient=recipient, is_reply=is_reply,
                               recipient_explicit=recipient_explicit,
                               only_they_know=only_they_know, html=html,
                               access_proof=access_proof)
    dropped = dropped_facts(facts_original, text) if facts_original is not None else []
    if dropped:
        result['verdict'] = "FAIL"

    print("\n=== Email Voice Check ===")
    if strict:
        print("Mode: STRICT (zero-tolerance self-gate)")
    print(f"Signal count: {result['signal_count']} ({result['structural_count']} structural)")
    print(f"Verdict: {result['verdict']}")
    if result.get('skipped_note'):
        print(result['skipped_note'])
    if result.get('access_note'):
        print(result['access_note'])
    print(f"Contraction ratio: {result['contraction_ratio']}")
    print(f"Mid-sentence dashes: {result['dash_count']}")
    print(f"Body words: {result['body_words']} (recipient: {result['recipient']})")

    if result['house']:
        print("\nHOUSE RULE FAILURES (these are why the recipient complained):")
        for category, message in result['house']:
            print(f"  [{category}] {message}")

    if dropped:
        print("\nFACTS DROPPED:")
        for problem in dropped:
            print(f"  {problem}")

    if result['warnings']:
        print("\nWARNINGS (do not fail; [sender_we]: does it mean the co-op? [acronym] fails under --strict):")
        for category, message in result['warnings']:
            print(f"  [{category}] {message}")

    if result['matches']:
        print("\nMatches:")
        for category, match in result['matches']:
            print(f"  [{category}] \"{match}\"")
    else:
        print("\nNo AI-voice signals detected.")

    # A FLAG used to exit 0, so `check && post` posted flagged text. Anything that
    # is not a clean PASS now exits non-zero, so the exit code can be trusted and
    # callers no longer have to know to grep for "Verdict: PASS".
    if result['verdict'] != "PASS":
        sys.exit(2)


if __name__ == '__main__':
    main()
