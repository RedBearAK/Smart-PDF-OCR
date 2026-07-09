"""Tests for confident-disagreement detection.

tests/test_disagree.py

The confidence gate only catches failures that admit to being failures. These
tests cover the ones that lie: a high-confidence line one character away from
what forty sibling pages agree on. Such a line is surfaced, never rewritten, and
content that legitimately varies between pages is never flagged at all.
"""

from smart_pdf_ocr.recognize.backend import PageLine, PageResult
from smart_pdf_ocr.correct.pipeline import correct_document
from smart_pdf_ocr.correct.disagree import classify, find_disagreements


CANON = "SHIPPING WORLDWIDE"
WRONG = "SHIPPING WOALDWIDE"


def _page(number, tagline, conf=0.99, date="7/7/2026", amount="90.00"):
    return PageResult(number, [
        PageLine("GalaxSea Freight Forwarding", 0.99),
        PageLine("SHIPMENT DETAILS", 0.99),
        PageLine(tagline, conf),
        PageLine("Sailing Date: {0}".format(date), 0.99),
        PageLine("Forwarding Fee  ${0}".format(amount), 0.99),
    ])


def _document(bad_tagline=None, bad_page=99):
    pages = [_page(i + 1, CANON) for i in range(8)]
    if bad_tagline is not None:
        pages.append(_page(bad_page, bad_tagline))
    return pages


def test_confident_wrong_read_is_flagged():
    """A 0.96-confidence one-character error is surfaced despite the gate."""
    pages = _document(WRONG)
    pages[-1].lines[2] = PageLine(WRONG, 0.96)
    _c, _report, diag = correct_document(pages)
    hits = [e for e in diag["disagreements"] if e[2] == WRONG]
    print(f"  disagreements={[(e[0], e[2], e[4]) for e in diag['disagreements']]}")
    return len(hits) == 1 and hits[0][3] == CANON and hits[0][4] == "disagrees"


def test_disagreement_is_never_rewritten():
    """Flagging is not correcting: the output keeps the confident text."""
    pages = _document(WRONG)
    pages[-1].lines[2] = PageLine(WRONG, 0.96)
    corrected, report, _diag = correct_document(pages)
    kept = corrected[99][2]
    print(f"  output line={kept!r} corrections={len(report)}")
    return kept == WRONG and len(report) == 0


def test_spacing_slip_is_classified_separately():
    """A whitespace-only variant is reported as spacing, not a character error."""
    print(f"  classify('A B','AB') -> {classify('A B', 'AB')!r}")
    print(f"  classify('AXB','AB') -> {classify('AXB', 'AB')!r}")
    return classify("A B", "AB") == "spacing" and classify("AXB", "AB") == "disagrees"


def test_dates_and_amounts_are_never_flagged():
    """Content that legitimately differs per page must not be surfaced."""
    pages = [_page(i + 1, CANON, date="7/7/2026", amount="90.00") for i in range(8)]
    pages.append(_page(99, CANON, date="8/1/2026", amount="105.00"))
    _c, _report, diag = correct_document(pages)
    flagged = [e[2] for e in diag["disagreements"]]
    print(f"  flagged={flagged}")
    return not any("$" in t or "/" in t for t in flagged)


def test_rare_canonical_gives_no_authority():
    """A canonical with too little support cannot condemn a variant."""
    pages = [_page(1, CANON), _page(2, WRONG)]
    counts = {CANON: 2}
    found = find_disagreements(pages, counts, conf_gate=0.90, min_support=3)
    print(f"  support=2 -> {len(found)} flags")
    return len(found) == 0


def main():
    tests = [
        test_confident_wrong_read_is_flagged,
        test_disagreement_is_never_rewritten,
        test_spacing_slip_is_classified_separately,
        test_dates_and_amounts_are_never_flagged,
        test_rare_canonical_gives_no_authority,
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
