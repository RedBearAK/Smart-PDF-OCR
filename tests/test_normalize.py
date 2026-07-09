"""Tests for ASCII normalization.

tests/test_normalize.py

An invented accent should vanish; a real one should survive. The corpus is the
arbiter: a folded spelling is adopted only when other pages already attest to it.
``force`` mode discards that safeguard on purpose, and these tests pin down the
difference so nobody enables it by accident.
"""

from smart_pdf_ocr.correct.pipeline import correct_document
from smart_pdf_ocr.recognize.backend import PageLine, PageResult
from smart_pdf_ocr.correct.normalize import (
    MODE_FORCE,
    MODE_OFF,
    candidates,
    fold_marks,
    normalize_line,
)


PLAIN = "Port of Loading: Zurich, Switzerland"
ACCENTED = "Port of Loading: Zürich, Switzerland"


def _pages(text, count, conf=0.99):
    return [PageResult(i + 1, [PageLine(text, conf)]) for i in range(count)]


def test_fold_marks_strips_diacritics():
    """NFKD folding recovers the base letters."""
    print(f"  fold_marks('Chelän') -> {fold_marks('Chelän')!r}")
    print(f"  fold_marks('Prépaid') -> {fold_marks('Prépaid')!r}")
    return fold_marks("Chelän") == "Chelan" and fold_marks("Prépaid") == "Prepaid"


def test_candidates_also_drop_undecomposable_symbols():
    """A middle dot has no decomposition, so a second candidate removes it."""
    options = candidates("Account Number:\u00b73592668683")
    print(f"  candidates -> {options}")
    return "Account Number:3592668683" in options


def test_evidence_required_before_folding():
    """With no corpus support the accented spelling is kept."""
    kept = normalize_line(ACCENTED, {}, min_support=3)
    adopted = normalize_line(ACCENTED, {PLAIN: 40}, min_support=3)
    print(f"  no evidence -> {kept!r}")
    print(f"  40 attestations -> {adopted!r}")
    return kept == ACCENTED and adopted == PLAIN


def test_hallucinated_accent_is_folded():
    """One accented line among many plain ones is corrected."""
    pages = _pages(PLAIN, 40)
    pages.append(PageResult(99, [PageLine(ACCENTED, 0.97)]))
    _c, report, _d = correct_document(pages)
    fixes = [(o, n) for _p, k, o, n in report if k == "ascii"]
    print(f"  fixes={fixes}")
    return len(fixes) == 1 and fixes[0][1] == PLAIN


def test_genuine_accent_survives_but_force_destroys_it():
    """A corpus that really is accented is protected -- unless force is asked for."""
    _c, report, diag = correct_document(_pages(ACCENTED, 40))
    _c2, forced, _d2 = correct_document(_pages(ACCENTED, 40), ascii_mode=MODE_FORCE)
    print(f"  default: {len(report)} fixes, {len(diag['unresolved_non_ascii'])} review rows")
    print(f"  force  : {len(forced)} fixes")
    return len(report) == 0 and len(forced) == 40


def test_off_mode_changes_nothing():
    """--keep-diacritics disables the step entirely."""
    pages = _pages(PLAIN, 40)
    pages.append(PageResult(99, [PageLine(ACCENTED, 0.97)]))
    _c, report, _d = correct_document(pages, ascii_mode=MODE_OFF)
    fixes = [1 for _p, k, _o, _n in report if k == "ascii"]
    print(f"  ascii fixes with mode=off: {len(fixes)}")
    return len(fixes) == 0


def main():
    tests = [
        test_fold_marks_strips_diacritics,
        test_candidates_also_drop_undecomposable_symbols,
        test_evidence_required_before_folding,
        test_hallucinated_accent_is_folded,
        test_genuine_accent_survives_but_force_destroys_it,
        test_off_mode_changes_nothing,
    ]
    score = 0
    for test in tests:
        print("[test] " + test.__name__)
        passed = bool(test())
        print("  -> " + ("PASS" if passed else "FAIL"))
        score += 1 if passed else 0
    print("\nSCORE: {0}/{1}".format(score, len(tests)))
    return score == len(tests)


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)


# End of file #
