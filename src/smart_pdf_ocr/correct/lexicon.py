"""Self-built corpus lexicon: token frequencies and fuzzy token repair.

smart_pdf_ocr/correct/lexicon.py

The corpus is its own dictionary. A token that recurs across many high-
confidence reads is treated as ground truth; a rare near-neighbour of a
frequent token is pulled back to it. This is the fallback for corrupted tokens
inside variable lines that boilerplate voting does not cover.

Two things are never touched. A line carrying a variable numeric field is left
alone, so amounts survive. So is a line carrying an identifier -- a run of five or
more digits. Such a line is data, not prose, and the neighbouring words on it are
labels rather than candidates for repair. This test knows nothing about what the
identifier means; it recognizes the shape. It deliberately does not treat a token
that merely mixes letters and digits as an identifier: `Va1dez` is a misread of
`Valdez`, and swallowing it would protect the very errors repair exists to fix.

That shape guard is coarse by design. Codes whose alphabetic part stands alone --
a four-letter carrier prefix, a three-letter port code -- look exactly like words,
and no structural test can tell `SEKU` from a typo for `SEGU`. Those belong in the
operator's known-patterns list, whose tokens are never rewritten.
"""

from difflib import SequenceMatcher
from collections import Counter

from smart_pdf_ocr.patterns.field_rgx import TOKEN_rgx, IDENTIFIER_rgx, NUMERIC_FIELD_rgx


def build_lexicon(page_results, min_conf=0.95):
    counts = Counter()
    for page in page_results:
        for line in page.lines:
            if line.conf < min_conf:
                continue
            for token in TOKEN_rgx.findall(line.text):
                if len(token) >= 3 and not token.isdigit():
                    counts[token] += 1
    return counts


def _closest(token, counts, min_ratio):
    best = None
    best_ratio = 0.0
    for candidate, _freq in counts.items():
        if abs(len(candidate) - len(token)) > 2:
            continue
        ratio = SequenceMatcher(None, token, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = candidate
    if best is not None and best_ratio >= min_ratio:
        return best
    return token


def _unguarded(text, counts, frequent, min_ratio):
    """What repair would have done with no guards at all."""
    out = []
    for token in TOKEN_rgx.findall(text):
        if len(token) >= 3 and not token.isdigit() and token not in frequent:
            out.append(_closest(token, counts, min_ratio))
        else:
            out.append(token)
    return " ".join(out)


def repair_line(text, counts, known=frozenset(), floor_freq=5, min_ratio=0.72, events=None):
    """Repair a line. A guard that actually prevented a change records itself.

    Only a counterfactual save is worth recording. A guard that skipped a line
    nothing would have touched has protected nothing, and saying so would bury the
    cases that matter.
    """
    frequent = {token for token, freq in counts.items() if freq >= floor_freq}
    numeric = bool(NUMERIC_FIELD_rgx.search(text))
    identifier = bool(IDENTIFIER_rgx.search(text))
    if numeric or identifier:
        if events is not None:
            naive = _unguarded(text, counts, frequent, min_ratio)
            if naive != text:
                guard = "numeric" if numeric else "identifier"
                events.append((guard, text, naive))
        return text
    out = []
    for token in TOKEN_rgx.findall(text):
        vouched = token.lower() in known
        repairable = (len(token) >= 3
                      and not token.isdigit()
                      and token not in frequent
                      and not vouched)
        if repairable:
            out.append(_closest(token, counts, min_ratio))
            continue
        if vouched and events is not None:
            candidate = _closest(token, counts, min_ratio)
            if candidate != token:
                events.append(("known", token, candidate))
        out.append(token)
    return " ".join(out)


# End of file #
