"""Cross-page boilerplate recovery via position and prefix anchoring.

smart_pdf_ocr/correct/consensus.py

Invariant chrome recurs across a format cluster and is recovered two ways.
Position-anchored slot voting handles chrome at stable page positions (a footer
is the last line, a logo tagline sits near the top): the modal high-confidence
string at a slot is canonical. Prefix-anchored line voting handles chrome that
floats in the page body (a standing sentence whose leading words survive
corruption): a low-confidence line sharing a long clean prefix with a frequent
line is pulled back to it. Both only fire when the two strings are
content-compatible, so genuinely variable lines are never overwritten.
"""

from difflib import SequenceMatcher

from smart_pdf_ocr.correct.lexical import choose
from smart_pdf_ocr.patterns.field_rgx import CORE_rgx, DIGIT_rgx, ANCHOR_TOKEN_rgx, NUMERIC_FIELD_rgx


# The support rule, stated once. A garbled line is only rewritten when at least
# MIN_CLEAN_SIBLINGS other pages of the same format carry a read of that slot (or
# of that whole line) at CLEAN_CONF or better. This is a clean-sibling count, not
# a page count: a four-page document corrects fine when three pages are clean,
# and a fifty-page document corrects nothing if the line is garbled on all but
# two of them.
CLEAN_CONF = 0.95
MIN_CLEAN_SIBLINGS = 3

# How much of a figure-bearing line must already agree with its canonical before
# the rest may be rewritten.
FIGURE_PREFIX = 20

# A spelling that recurs three times still is not canonical if a near-identical
# sibling recurs forty. These mirror the disagreement detector's thresholds; the
# constants are restated here rather than imported, because that module depends on
# this one.
NEAR_MISS_RATIO = 0.90
NEAR_MISS_FRACTION = 0.25
NEAR_MISS_LENGTH_GAP = 3


def carries_numeric_field(text):
    """True for lines holding a figure: money, a decimal, an invoice number."""
    return bool(NUMERIC_FIELD_rgx.search(text))


def figure_safe(old, new):
    """May a figure-bearing line be rewritten to this canonical?

    Not every figure varies. `(include an additional $35 wire fee...)` carries the
    same $35 on all thirty-nine pages, and refusing to repair it leaves garbage in
    the output. `Forwarding Fee $90.00` carries a figure that differs by page, and
    repairing it would restate the amount.

    The strings themselves say which is which. An invariant figure lies inside the
    stretch the garbled line and its canonical already agree on; a variable one
    lies beyond it, in the part the rewrite would supply. So: agree on at least
    FIGURE_PREFIX characters, and the canonical's remaining tail must hold no
    digits at all.
    """
    if not carries_numeric_field(old) and not carries_numeric_field(new):
        return True
    left = old.strip()
    right = new.strip()
    shared = _common_prefix(left, right)
    if shared < FIGURE_PREFIX:
        return False
    return not DIGIT_rgx.search(right[shared:])


def build_slot_canon(pages, depth=4, min_conf=CLEAN_CONF, plausible=None):
    top = {}
    bottom = {}
    for page in pages:
        lines = page.lines
        count = len(lines)
        for k in range(min(depth, count)):
            head = lines[k]
            if head.conf >= min_conf and not carries_numeric_field(head.text):
                top.setdefault(k, {}).setdefault(head.text, 0)
                top[k][head.text] += 1
            tail = lines[count - 1 - k]
            if tail.conf >= min_conf and not carries_numeric_field(tail.text):
                bottom.setdefault(k, {}).setdefault(tail.text, 0)
                bottom[k][tail.text] += 1
    return _reduce(top, plausible), _reduce(bottom, plausible)


def drop_near_misses(counts, min_support=MIN_CLEAN_SIBLINGS):
    """Discard rare spellings that a far more frequent sibling contradicts.

    Recurrence is what makes a line canonical, and a recognizer that misreads the
    same word the same way on three pages manufactures a counterfeit canonical.
    The disagreement detector already recognizes that shape -- a rare string one or
    two characters from a frequent one -- so nothing may become canonical that the
    tool would have flagged as an error had it read it on a page.
    """
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    kept = {}
    for text, count in ordered:
        contradicted = False
        for other, other_count in ordered:
            if other_count < min_support or other_count <= count:
                continue
            if abs(len(other) - len(text)) > NEAR_MISS_LENGTH_GAP:
                continue
            if count > max(1, int(other_count * NEAR_MISS_FRACTION)):
                continue
            if SequenceMatcher(None, text, other).ratio() >= NEAR_MISS_RATIO:
                contradicted = True
                break
        if not contradicted:
            kept[text] = count
    return kept


def build_line_index(pages, min_conf=CLEAN_CONF, min_count=MIN_CLEAN_SIBLINGS, min_length=18):
    tally = {}
    for page in pages:
        for line in page.lines:
            if line.conf < min_conf or len(line.text) < min_length:
                continue
            tally.setdefault(line.text, 0)
            tally[line.text] += 1
    tally = drop_near_misses(tally, min_count)
    return [(text, count) for text, count in tally.items() if count >= min_count]


def _reduce(slots, plausible=None):
    out = {}
    for k, tally in slots.items():
        out[k] = choose(tally, plausible)
    return out


def _common_prefix(left, right):
    limit = min(len(left), len(right))
    i = 0
    while i < limit and left[i] == right[i]:
        i += 1
    return i


def _compatible(garbled, canonical):
    left = garbled.strip()
    right = canonical.strip()
    if not left or not right:
        return False
    if SequenceMatcher(None, left, right).ratio() >= 0.25:
        return True
    left_tokens = ANCHOR_TOKEN_rgx.findall(left)
    right_tokens = ANCHOR_TOKEN_rgx.findall(right)
    for gt in left_tokens:
        for ct in right_tokens:
            if gt in ct or ct in gt:
                return True
    return False


def _slot_candidate(line, top_entry, bottom_entry, min_support):
    candidates = []
    for entry in (top_entry, bottom_entry):
        if entry is None:
            continue
        text, support = entry
        if support < min_support or text == line.text:
            continue
        if _compatible(line.text, text):
            candidates.append((support, text))
    if candidates:
        candidates.sort()
        return candidates[-1][1]
    return None


def _prefix_candidate(line, line_index, min_prefix):
    best = None
    best_prefix = min_prefix - 1
    for text, _count in line_index:
        if text == line.text:
            continue
        shared = _common_prefix(line.text.strip(), text.strip())
        if shared > best_prefix and shared >= min_prefix:
            best_prefix = shared
            best = text
    return best


def _all_vouched(text, known):
    """True when every alphanumeric token on the line is a known pattern."""
    if not known:
        return False
    tokens = CORE_rgx.findall(text)
    if not tokens:
        return False
    return all(token.lower() in known for token in tokens)


def recover_page(page, top_canon, bottom_canon, line_index=(), conf_gate=0.90,
                 min_support=MIN_CLEAN_SIBLINGS, min_prefix=12, known=frozenset(),
                 events=None):
    """Return a list of (line_index, old_text, new_text) boilerplate fixes.

    Voting outranks a bad read, but never a term the operator vouched for. A line
    made entirely of known patterns is left exactly as it stands, however loudly the
    slot's canonical disagrees -- SEKU and SEGU are both real, and the corpus has no
    way to tell a rare one from a misread of the common one.

    A candidate refused by that rule, or by ``figure_safe``, was a real rewrite the
    guard stopped, so it is recorded when the caller asks for events.
    """
    count = len(page.lines)
    fixes = []
    for i, line in enumerate(page.lines):
        if line.conf >= conf_gate:
            continue
        new_text = _slot_candidate(line, top_canon.get(i), bottom_canon.get(count - 1 - i), min_support)
        if new_text is None:
            new_text = _prefix_candidate(line, line_index, min_prefix)
        if new_text is None or new_text == line.text:
            continue
        if _all_vouched(line.text, known):
            if events is not None:
                events.append(("known", line.text, new_text))
            continue
        if not figure_safe(line.text, new_text):
            if events is not None:
                events.append(("figure", line.text, new_text))
            continue
        fixes.append((i, line.text, new_text))
    return fixes


# End of file #
