"""ASCII normalization: unfold the diacritics the scanner invented.

smart_pdf_ocr/correct/normalize.py

A scan of an English freight invoice does not contain `Chelän` or `Prépaid`. The
accent is a recognizer artefact, and stripping it is nearly always right. Nearly.
A document that genuinely says `Zürich` on forty pages would be ruined by a blind
fold, and no flag would ever tell you.

So the corpus decides. A folded candidate replaces the original only when the
corpus already attests to the candidate -- the same line, spelled plainly, on
enough other pages. Where the evidence is absent the line is left alone and
surfaced for review instead. ``force`` mode folds unconditionally for callers who
know their corpus is ASCII; ``off`` disables the step entirely.

Two candidates are tried, in order of increasing violence: drop the combining
marks (`Chelän` -> `Chelan`), then drop whatever non-ASCII remains, which handles
the stray symbols that have no decomposition at all (`·Net 15` -> `Net 15`).
"""

import unicodedata

from smart_pdf_ocr.patterns.field_rgx import NON_ASCII_rgx


MODE_EVIDENCE = "evidence"
MODE_FORCE = "force"
MODE_OFF = "off"

KIND_ASCII = "ascii"


def has_non_ascii(text):
    return bool(NON_ASCII_rgx.search(text))


def fold_marks(text):
    """Decompose, then drop combining marks: 'Chelän' -> 'Chelan'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def drop_non_ascii(text):
    """Remove whatever survives folding: '·Net 15' -> 'Net 15'."""
    return NON_ASCII_rgx.sub("", text)


def candidates(text):
    """Progressively plainer spellings of a line, nearest first."""
    seen = []
    folded = fold_marks(text)
    if folded != text:
        seen.append(folded)
    stripped = drop_non_ascii(folded)
    if stripped != text and stripped not in seen:
        seen.append(stripped)
    return [c for c in seen if c.strip()]


def normalize_line(text, attested, mode=MODE_EVIDENCE, min_support=3):
    """Return the plainest spelling the corpus supports, or the text unchanged."""
    if mode == MODE_OFF or not has_non_ascii(text):
        return text
    if mode == MODE_FORCE:
        forced = drop_non_ascii(fold_marks(text))
        return forced if forced.strip() else text
    original_support = attested.get(text, 0)
    for candidate in candidates(text):
        support = attested.get(candidate, 0)
        if support >= min_support and support > original_support:
            return candidate
    return text


# End of file #
