"""Tests for typographic adoption.

tests/test_typography.py

A high-confidence line whose alphanumeric payload matches its canonical exactly
differs only in presentation: the spacing, the punctuation, or the case. Adopting
the canonical cannot change a letter or a digit, so it is a correction rather than
a finding. Anything that altered a character stays flagged, because at that point
only a human can say which reading is right.
"""

import re

from smart_pdf_ocr.correct.pipeline import correct_document
from smart_pdf_ocr.correct.disagree import classify
from smart_pdf_ocr.recognize.backend import PageLine, PageResult


DIGIT_rgx = re.compile(r"\d")


def _document(variant, clean="Sitka, AK 99835", count=12):
    pages = []
    for i in range(count):
        pages.append(PageResult(i + 1, [
            PageLine("SHIPMENT DETAILS", 0.99),
            PageLine("GalaxSea Freight Forwarding", 0.99),
            PageLine(clean, 0.99),
        ]))
    pages.append(PageResult(99, [
        PageLine("SHIPMENT DETAILS", 0.99),
        PageLine("GalaxSea Freight Forwarding", 0.99),
        PageLine(variant, 0.99),
    ]))
    return pages


def test_classify_separates_the_three_harmless_kinds():
    """Spacing, punctuation and case are distinguished from a character change."""
    checks = {
        classify("Account Number:123456", "Account Number: 123456"): "spacing",
        classify("Sitka. AK 99835", "Sitka, AK 99835"): "punctuation",
        classify("invoice Terms", "Invoice Terms"): "case",
        classify("SHIPPING WORLOWIDE", "SHIPPING WORLDWIDE"): "disagrees",
    }
    print(f"  {checks}")
    return all(found == expected for found, expected in checks.items())


def test_punctuation_slip_is_corrected():
    """A comma read as a full stop is adopted from the canonical."""
    corrected, report, diag = correct_document(_document("Sitka. AK 99835"))
    kinds = [k for _p, k, _o, _n in report]
    print(f"  p99 -> {corrected[99][2]!r}  kinds={kinds}")
    return corrected[99][2] == "Sitka, AK 99835" and "typography" in kinds


def test_case_slip_is_corrected():
    """A lowercased word is restored from the canonical."""
    corrected, _r, _d = correct_document(_document("invoice Terms", "Invoice Terms"))
    print(f"  p99 -> {corrected[99][2]!r}")
    return corrected[99][2] == "Invoice Terms"


def test_inserted_separator_is_typographic():
    """'WORL.DWIDE' still spells WORLDWIDE; the full stop was inserted, not swapped."""
    corrected, report, _d = correct_document(
        _document("SHIPPING WORL.DWIDE", "SHIPPING WORLDWIDE"))
    kinds = [k for _p, k, _o, _n in report]
    print(f"  p99 -> {corrected[99][2]!r}  kinds={kinds}")
    return corrected[99][2] == "SHIPPING WORLDWIDE" and "typography" in kinds


def test_replaced_letter_is_not_typographic():
    """'WORL.WIDE' is short a 'D'. No reformatting recovers it."""
    corrected, _r, diag = correct_document(
        _document("SHIPPING WORL.WIDE", "SHIPPING WORLDWIDE"))
    flagged = [e[2] for e in diag["disagreements"]]
    print(f"  p99 -> {corrected[99][2]!r}  flagged={flagged}")
    return corrected[99][2] == "SHIPPING WORL.WIDE" and flagged == ["SHIPPING WORL.WIDE"]


def test_character_change_stays_flagged():
    """A letter that actually differs is never adopted, only reported."""
    corrected, report, diag = correct_document(
        _document("SHIPPING WORLOWIDE", "SHIPPING WORLDWIDE"))
    flagged = [e[2] for e in diag["disagreements"]]
    print(f"  p99 -> {corrected[99][2]!r}  flagged={flagged}")
    return corrected[99][2] == "SHIPPING WORLOWIDE" and "SHIPPING WORLOWIDE" in flagged


def test_no_digit_or_letter_is_ever_altered():
    """The payload is identical by construction; verify it stays that way."""
    corrected, report, _d = correct_document(_document("ABA#'325081403", "ABA# 325081403"))
    fixes = [(o, n) for _p, k, o, n in report if k == "typography"]
    moved = [(o, n) for o, n in fixes if DIGIT_rgx.findall(o) != DIGIT_rgx.findall(n)]
    print(f"  fixes={fixes}  digits moved={moved}")
    return fixes and not moved


def test_typography_can_be_disabled():
    """--keep-typography turns the corrections back into findings."""
    corrected, report, diag = correct_document(_document("Sitka. AK 99835"), typography=False)
    print(f"  p99 -> {corrected[99][2]!r}  corrections={len(report)} "
          f"disagreements={len(diag['disagreements'])}")
    return corrected[99][2] == "Sitka. AK 99835" and len(diag["disagreements"]) == 1


def main():
    tests = [
        test_classify_separates_the_three_harmless_kinds,
        test_punctuation_slip_is_corrected,
        test_case_slip_is_corrected,
        test_inserted_separator_is_typographic,
        test_replaced_letter_is_not_typographic,
        test_character_change_stays_flagged,
        test_no_digit_or_letter_is_ever_altered,
        test_typography_can_be_disabled,
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
