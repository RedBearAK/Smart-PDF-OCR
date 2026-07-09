"""Lexical plausibility: a tiebreaker, never a primary signal.

smart_pdf_ocr/correct/lexical.py

Repetition decides which spelling is canonical. Forty pages reading `amount` and
one reading `aount` need no dictionary, and a lopsided vote is never overturned
however implausible its winner. But a five-page document can split three to two,
and the majority can be wrong: in one real corpus the `Fax#` label was shredded on
nineteen of forty-three pages, so a short enough slice would elect garbage as
canonical and then freeze it into a profile.

Where the counts are close, ask whether the string is made of real words. `Fax`
is; `#ax#` is not. `wordfreq` answers that, and it knows ordinary vocabulary and
common place names alike (`chelan`, `qingdao`, `sitka` all score). It is a required
dependency -- accuracy is the whole point of the tool, and the tiebreaker is part of
being accurate -- so it is always consulted. A caller's known-patterns list adds the
coined terms `wordfreq` cannot know, and outweighs it when the two would disagree.
"""

from wordfreq import zipf_frequency

from smart_pdf_ocr.patterns.field_rgx import WORD_TOKEN_rgx


# A vote is "close" when the runner-up is within this factor of the winner.
TIE_FACTOR = 1.5

# Zipf frequency below which wordfreq considers a token unattested.
MIN_ZIPF = 1.5

# A term the operator vouched for outranks one the general corpus merely knows.
KNOWN_WEIGHT = 1.0
WORDFREQ_WEIGHT = 0.6

def known_tokens(patterns=()):
    """Every alphabetic token named in the known-patterns file, lowercased.

    A phrase contributes each of its words, so `HO CHI MINH` vouches for `ho`,
    `chi` and `minh` individually.
    """
    tokens = set()
    for entry in patterns:
        for token in WORD_TOKEN_rgx.findall(str(entry)):
            tokens.add(token.lower())
    return frozenset(tokens)


def known_entries(patterns=()):
    """Every alphanumeric term named in the known-patterns file, original case.

    Unlike ``known_tokens`` this keeps the spelling, because a correction drawn
    from this vocabulary has to be written back into the text as the operator
    wrote it, and it keeps short codes like `KOD` that carry no letters to spare.
    """
    from smart_pdf_ocr.patterns.field_rgx import CORE_rgx
    entries = []
    seen = set()
    for entry in patterns:
        for token in CORE_rgx.findall(str(entry)):
            lowered = token.lower()
            if lowered not in seen:
                seen.add(lowered)
                entries.append(token)
    return tuple(entries)


def make_plausibility(patterns=()):
    """Return a callable text -> 0.0..1.0 scoring how word-like a string is."""
    known = known_tokens(patterns)

    def plausibility(text):
        tokens = WORD_TOKEN_rgx.findall(text)
        if not tokens:
            return 0.0
        weight = 0.0
        for token in tokens:
            lowered = token.lower()
            if lowered in known:
                weight += KNOWN_WEIGHT
            elif zipf_frequency(lowered, "en") >= MIN_ZIPF:
                weight += WORDFREQ_WEIGHT
        return weight / len(tokens)

    return plausibility


def choose(tally, plausible=None):
    """Pick the canonical spelling from {text: count}. Returns (text, count)."""
    ranked = sorted(tally.items(), key=lambda pair: (-pair[1], pair[0]))
    best_text, best_count = ranked[0]
    if plausible is None or len(ranked) == 1:
        return best_text, best_count

    contenders = [pair for pair in ranked if pair[1] * TIE_FACTOR >= best_count]
    if len(contenders) < 2:
        return best_text, best_count

    scored = sorted(contenders, key=lambda pair: (plausible(pair[0]), pair[1], pair[0]),
                    reverse=True)
    return scored[0]


def _strip_comment(line):
    """Drop a trailing comment, but never a '#' that belongs to the term itself.

    `Fax#` and `ABA#` are real terms. A comment is a '#' preceded by whitespace.
    """
    for marker in (" #", "\t#"):
        cut = line.find(marker)
        if cut != -1:
            line = line[:cut]
    return line.strip()


def load_patterns(path):
    """Read a newline-delimited list of known words, names and places."""
    names = []
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            if line.lstrip().startswith("#"):
                continue
            token = _strip_comment(line.rstrip("\n").rstrip("\r"))
            if token:
                names.append(token)
    return names


# End of file #
