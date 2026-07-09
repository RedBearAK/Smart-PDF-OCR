"""Error patterns: strings the operator declares can never be right.

smart_pdf_ocr/correct/errors.py

Consensus can only judge what it can vote on. A three-letter port code buried in a
routing line that differs on every page has no siblings to outvote it, and at three
characters plain edit distance carries no signal at all: `KOO` sits exactly as close
to `KOD` as it does to `KOB` or `KOZ`. Nothing the corpus knows can settle it.

So the operator declares it. An error pattern is a deterministic trigger: no vote,
no confidence gate, no similarity threshold. Where it appears, something is wrong.

What it is *not* is a replacement. The correction is drawn from the known-patterns
file -- the nearest member of that vocabulary, and only when the nearest member is
unambiguous. Two known patterns equally close means the tool does not know, and a
tool that does not know should say so rather than guess. Every encounter is
recorded either way, so a rule that fires leaves a trace even when it changes
nothing.

An error pattern that also appears in the known-patterns file is a contradiction,
and the two files are refused together rather than reconciled silently.

A pattern names exactly one term. `DAIAN` names one; so does `/KOJ/`, whose slashes
are context rather than content, and so does `WORL.WIDE`, where a scanner has put a
full stop where a `D` belongs -- an everyday OCR failure that punctuation should not
be allowed to hide from. What a pattern may not contain is whitespace between its
letters: `Wire information:` names two words, and a rule that silently corrected only
the first of them would be worse than no rule at all.

Delimiters are stripped from the ends and kept in the middle. `/KOJ/` resolves `KOJ`;
`WORL.WIDE` resolves `WORL.WIDE`, one edit from `WORLDWIDE`, because the full stop is
part of what went wrong.
"""

from smart_pdf_ocr.patterns.field_rgx import CORE_rgx, DIGIT_rgx, WHITESPACE_rgx


SHORT_LEN = 6
MAX_EDITS_SHORT = 1
MAX_EDITS_LONG = 2

KIND_ERROR = "error"
KIND_ERRORFIX = "errorfix"

NO_CANDIDATE = "no known pattern of comparable length"
TOO_FAR = "nearest known pattern is %d edits away (budget %d)"
AMBIGUOUS = "ambiguous: %s are equally close"
DIGITS_WOULD_CHANGE = "correction would alter digits"
NOT_ONE_TERM = "must name a single term, with no whitespace between its letters"


def _strip_comment(line):
    """Drop a trailing comment, but never a '#' that belongs to the term itself.

    `Fax#` and `ABA#` are real terms. A comment is a '#' preceded by whitespace.
    """
    for marker in (" #", "\t#"):
        cut = line.find(marker)
        if cut != -1:
            line = line[:cut]
    return line.strip()


def load_error_patterns(path):
    """Read a newline-delimited list of strings that can never be correct."""
    patterns = []
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            if line.lstrip().startswith("#"):
                continue
            token = _strip_comment(line.rstrip("\n").rstrip("\r"))
            if token:
                patterns.append(token)
    return patterns


def malformed(error_patterns):
    """Patterns naming more than one word, which cannot be applied unambiguously."""
    broken = []
    for pattern in error_patterns:
        core = _core(pattern)
        if not core or WHITESPACE_rgx.search(core):
            broken.append(pattern)
    return broken


def contradictions(error_patterns, known):
    """Strings declared both wrong and right. The two files disagree."""
    vocabulary = {str(entry).lower() for entry in known}
    clashes = []
    for pattern in error_patterns:
        core = _core(pattern)
        if core and core.lower() in vocabulary:
            clashes.append(pattern)
    return clashes


def _core_span(pattern):
    """The pattern with its outer delimiters removed. Inner punctuation stays."""
    found = CORE_rgx.search(pattern)
    if not found:
        return 0, 0
    last = None
    for match in CORE_rgx.finditer(pattern):
        last = match
    return found.start(), last.end()


def _core(pattern):
    start, end = _core_span(pattern)
    return pattern[start:end]


def _distance(left, right):
    """Levenshtein distance. Small strings, so the simple table is fine."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, source in enumerate(left, 1):
        current = [i]
        for j, target in enumerate(right, 1):
            substitute = previous[j - 1] + (source != target)
            current.append(min(previous[j] + 1, current[j - 1] + 1, substitute))
        previous = current
    return previous[-1]


def budget(core):
    return MAX_EDITS_SHORT if len(core) <= SHORT_LEN else MAX_EDITS_LONG


def resolve(core, known):
    """Return (replacement, reason). A tie or a distant match yields no replacement."""
    allowed = budget(core)
    scored = []
    for entry in known:
        if abs(len(entry) - len(core)) > allowed:
            continue
        scored.append((_distance(core.lower(), entry.lower()), entry))
    if not scored:
        return None, NO_CANDIDATE
    scored.sort()
    best = scored[0][0]
    if best > allowed:
        return None, TOO_FAR % (best, allowed)
    ties = [entry for distance, entry in scored if distance == best]
    if len(ties) > 1:
        return None, AMBIGUOUS % ", ".join(sorted(ties))
    return ties[0], ""


def _digits(text):
    return DIGIT_rgx.findall(text)


def _is_bare_token(pattern):
    """No outer delimiters, so the pattern must not sit inside a longer token."""
    return bool(pattern) and _core(pattern) == pattern


def _splice(text, spots, pattern, replacement):
    """Replace only where the pattern was actually found, right to left.

    ``str.replace`` knows nothing of token boundaries, so it would happily rewrite
    the `DAIAN` inside `DAIANX` that ``_occurrences`` was careful to reject.
    """
    for start in sorted(spots, reverse=True):
        text = text[:start] + replacement + text[start + len(pattern):]
    return text


def _occurrences(line, pattern):
    """Where the pattern sits. A bare token matches only on token boundaries."""
    bare = _is_bare_token(pattern)
    spots = []
    start = line.find(pattern)
    while start != -1:
        end = start + len(pattern)
        if not bare:
            spots.append(start)
        else:
            before = line[start - 1] if start > 0 else ""
            after = line[end] if end < len(line) else ""
            if not before.isalnum() and not after.isalnum():
                spots.append(start)
        start = line.find(pattern, start + 1)
    return spots


def apply_patterns(line, error_patterns, known):
    """Return (new_line, events). Events record every encounter, fixed or not."""
    events = []
    text = line
    for pattern in error_patterns:
        spots = _occurrences(text, pattern)
        if not spots:
            continue
        core = _core(pattern)
        if not core:
            events.append((KIND_ERROR, pattern, text, NO_CANDIDATE))
            continue
        replacement, reason = resolve(core, known)
        if replacement is None:
            events.append((KIND_ERROR, pattern, text, reason))
            continue
        start, end = _core_span(pattern)
        rebuilt = pattern[:start] + replacement + pattern[end:]
        if _digits(pattern) != _digits(rebuilt):
            events.append((KIND_ERROR, pattern, text, DIGITS_WOULD_CHANGE))
            continue
        text = _splice(text, spots, pattern, rebuilt)
        events.append((KIND_ERRORFIX, pattern, line, rebuilt))
    return text, events


# End of file #
