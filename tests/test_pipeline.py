"""Tests for the correction pipeline's honesty guarantees.

tests/test_pipeline.py

A whitespace-only rewrite is not a correction and must be discarded. A format
cluster too small to outvote a garbled page must say so and change nothing,
rather than appearing to have worked.
"""

from smart_pdf_ocr.recognize.backend import PageLine, PageResult
from smart_pdf_ocr.correct.pipeline import correct_document, MIN_CLUSTER_PAGES


FOOTER = "411 S 1st Street - Chelan, WA 98816 - Fx# 509-888-0058"
GARBLED = "41-- #--- # - - 058"


def _page(number, footer, conf):
    return PageResult(number, [
        PageLine("GalaxSea Freight Forwarding", 0.99),
        PageLine("SHIPMENT DETAILS", 0.99),
        PageLine("body value {0}".format(number), 0.99),
        PageLine(footer, conf),
    ])


def _document(clean_pages):
    pages = [_page(i + 1, FOOTER, 0.99) for i in range(clean_pages)]
    pages.append(_page(99, GARBLED, 0.60))
    return pages


def test_small_cluster_declares_no_consensus():
    """Too few pages to outvote a bad page: no corrections, and it says why."""
    pages = _document(MIN_CLUSTER_PAGES - 2)
    _corrected, report, diagnostics = correct_document(pages)
    can_vote = diagnostics["clusters"][0][2]
    print(f"  pages={len(pages)} can_vote={can_vote} corrections={len(report)}")
    print(f"  uncorrected={[(p, r) for p, _c, _t, r in diagnostics['uncorrected']]}")
    return (not can_vote) and len(report) == 0 and len(diagnostics["uncorrected"]) == 1


def test_sufficient_cluster_corrects_and_votes():
    """With enough clean siblings the garbled footer is restored."""
    pages = _document(MIN_CLUSTER_PAGES)
    _corrected, report, diagnostics = correct_document(pages)
    can_vote = diagnostics["clusters"][0][2]
    restored = any(new == FOOTER for _p, _k, _o, new in report)
    print(f"  pages={len(pages)} can_vote={can_vote} corrections={len(report)}")
    return can_vote and restored


def test_cosmetic_edit_rejected():
    """An edit that only changes whitespace is never reported as a correction."""
    pages = _document(MIN_CLUSTER_PAGES)
    spaced = _page(98, "41-- #- -- #- -  058", 0.62)
    pages.append(spaced)
    _corrected, report, _diagnostics = correct_document(pages)
    cosmetic = [(o, n) for _p, _k, o, n in report if o.replace(" ", "") == n.replace(" ", "")]
    print(f"  corrections={len(report)} cosmetic={cosmetic}")
    return len(cosmetic) == 0


def test_numeric_line_survives_correction():
    """A confident numeric line is never rewritten by the pipeline."""
    pages = _document(MIN_CLUSTER_PAGES)
    corrected, _report, _diagnostics = correct_document(pages)
    kept = corrected[1][2]
    print(f"  page 1 body line -> {kept!r}")
    return kept == "body value 1"


def main():
    tests = [
        test_small_cluster_declares_no_consensus,
        test_sufficient_cluster_corrects_and_votes,
        test_cosmetic_edit_rejected,
        test_numeric_line_survives_correction,
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
