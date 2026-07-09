"""Confident disagreement: high-confidence lines that conflict with the corpus.

smart_pdf_ocr/correct/disagree.py

Every failure the confidence gate catches announces itself by scoring low. Some
failures lie. A recognizer can read SHIPPING WOALDWIDE at 0.96, or hallucinate an
umlaut into a street name, and the gate will wave it through because the model is
sure. Consensus never sees the line, and the error reaches the output silently.

What betrays such a line is not its confidence but its rarity: it sits where a
canonical sits, it is one or two characters away from that canonical, and forty
sibling pages disagree with it. This module finds those lines whose characters
actually differ -- high confidence deserves that much respect -- it surfaces those
for review. A line that differs only in spacing, punctuation or case carries the
same letters and digits as its canonical, so that one is simply corrected.

A figure-bearing line is judged by ``figure_safe``: an invariant amount that sits
inside the agreed prefix (the standing `$35` wire fee) may be flagged, while a
variable one that the canonical would restate (`$90.00` against `$105.00`) may not.

The similarity floor defaults to 0.90, the conservative end of a plateau fitted
against a real 84-page document: every unambiguous error is still caught at 0.92,
and the only flag gained by dropping to 0.86 is an arguable one (`QINGDAO, CN`
against `QINGDAO, CHINA`). Lower it when truncations matter more than quiet.
"""

from difflib import SequenceMatcher

from smart_pdf_ocr.patterns.field_rgx import CORE_rgx, DATE_rgx, WHITESPACE_rgx
from smart_pdf_ocr.correct.consensus import figure_safe


DISAGREE_RATIO = 0.90
MAX_VARIANT_FRACTION = 0.25
MIN_DISAGREE_LENGTH = 8
MAX_LENGTH_GAP = 3

# A line whose tail dissolves into noise scores badly on whole-string similarity
# even when its opening is a perfect match. Prefix agreement catches those.
PREFIX_MIN_CHARS = 20
PREFIX_MIN_FRACTION = 0.40

KIND_CHARACTER = "disagrees"
KIND_SPACING = "spacing"
KIND_PUNCTUATION = "punctuation"
KIND_CASE = "case"

# Differences that cannot alter a letter or a digit, only how they are presented.
TYPOGRAPHIC_KINDS = (KIND_SPACING, KIND_PUNCTUATION, KIND_CASE)


def is_variable(text):
    """A date legitimately differs between pages and is never worth flagging."""
    return bool(DATE_rgx.search(text))


def _shared_prefix(left, right):
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def payload(text):
    """Every letter and digit, in order, with all separators removed."""
    return "".join(CORE_rgx.findall(text))


def classify(text, canonical):
    """How the two differ, from harmless to substantive.

    The test is the payload: every letter and digit, in order, separators stripped.
    When two lines carry the same payload they hold the same information, and only
    its presentation differs -- a missing space, an intruding full stop, a lowercased
    initial. `SHIPPING WORL.DWIDE` still spells WORLDWIDE; the period was inserted
    between two letters, not put in place of one. Adopting the canonical cannot lose
    a character, so these are corrections rather than findings.

    `SHIPPING WORL.WIDE` is a different animal. There the `D` was replaced, the
    payload is short a letter, and no amount of reformatting recovers it. That one
    stays flagged, or is declared wrong outright in the error-patterns file.
    """
    if WHITESPACE_rgx.sub("", text) == WHITESPACE_rgx.sub("", canonical):
        return KIND_SPACING
    left = payload(text)
    right = payload(canonical)
    if left == right:
        return KIND_PUNCTUATION
    if left.lower() == right.lower():
        return KIND_CASE
    return KIND_CHARACTER


def _eligible(text, canonical, seen, support, min_support):
    if support < min_support:
        return False
    if is_variable(canonical):
        return False
    if seen > max(1, int(support * MAX_VARIANT_FRACTION)):
        return False
    return figure_safe(text, canonical)


def _best_canonical(text, seen, canon_counts, min_support, min_ratio):
    """Nearest canonical by whole-string similarity, for ordinary near-misses."""
    best = None
    best_ratio = min_ratio
    for canonical, support in canon_counts.items():
        if abs(len(canonical) - len(text)) > MAX_LENGTH_GAP:
            continue
        if not _eligible(text, canonical, seen, support, min_support):
            continue
        ratio = SequenceMatcher(None, text, canonical).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = canonical
    return best


def _best_by_prefix(text, seen, canon_counts, min_support):
    """Nearest canonical by exact opening agreement, for lines that dissolve."""
    best = None
    best_shared = 0
    stripped = text.strip()
    for canonical, support in canon_counts.items():
        if not _eligible(text, canonical, seen, support, min_support):
            continue
        target = canonical.strip()
        if target == stripped:
            continue
        shared = _shared_prefix(stripped, target)
        if shared < PREFIX_MIN_CHARS or shared < PREFIX_MIN_FRACTION * len(target):
            continue
        if shared > best_shared:
            best_shared = shared
            best = canonical
    return best


def find_disagreements(pages, canon_counts, conf_gate=0.90, min_support=3,
                       min_ratio=DISAGREE_RATIO):
    """Return [(page_number, conf, text, canonical, kind)] for confident conflicts."""
    variant_counts = {}
    for page in pages:
        for line in page.lines:
            variant_counts[line.text] = variant_counts.get(line.text, 0) + 1

    findings = []
    for page in pages:
        for line in page.lines:
            text = line.text
            if line.conf < conf_gate:
                continue
            if len(text) < MIN_DISAGREE_LENGTH or text in canon_counts:
                continue
            if is_variable(text):
                continue
            seen = variant_counts.get(text, 1)
            canonical = _best_canonical(text, seen, canon_counts, min_support, min_ratio)
            if canonical is None:
                canonical = _best_by_prefix(text, seen, canon_counts, min_support)
            if canonical is not None:
                findings.append((page.page_number, line.conf, text, canonical,
                                 classify(text, canonical)))
    return findings


# End of file #
