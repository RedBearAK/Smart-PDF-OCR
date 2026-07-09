"""Tests for the durable vendor profile and the figures it must never assert.

tests/test_profile.py

A profile lets a document too short for consensus correct anyway. It must never
carry a variable figure: next month's invoice has different amounts, and an
asserted amount would overwrite them.
"""

from smart_pdf_ocr.correct.pipeline import correct_document
from smart_pdf_ocr.recognize.backend import PageLine, PageResult
from smart_pdf_ocr.review.profile import learn_profile, cluster_entry


FOOTER = "411 S 1st Street - Chelan, WA 98816 - Fx# 509-888-0058"
GARBLED = "41-- #--- # - - 058"


def _page(number, footer, conf, amount="90.00"):
    return PageResult(number, [
        PageLine("GalaxSea Freight Forwarding", 0.99),
        PageLine("SHIPMENT DETAILS", 0.99),
        PageLine("Forwarding Fee and Handling  ${0}".format(amount), 0.99),
        PageLine(footer, conf),
    ])


def _big_document():
    return [_page(i + 1, FOOTER, 0.99) for i in range(5)]


def test_profile_learns_slot_canonicals():
    """A clean document yields the footer as a bottom-slot canonical."""
    pages = _big_document()
    corrected, _report, _diag = correct_document(pages)
    profile = learn_profile(pages, corrected)
    slots, _lines = cluster_entry(profile, "A")
    print(f"  slots={sorted(slots)}")
    return slots.get("bottom:0") == FOOTER


def test_profile_never_learns_a_repeated_misreading():
    """Three misreadings do not outvote forty clean ones, however confident."""
    pages = []
    for i in range(43):
        tagline = "SHIPPING WORLDWIDE" if i >= 3 else "SHIPPING WORLOWIDE"
        pages.append(PageResult(i + 1, [
            PageLine("GalaxSea Freight Forwarding", 0.99),
            PageLine("SHIPMENT DETAILS", 0.99),
            PageLine(tagline, 0.99),
            PageLine(FOOTER, 0.99),
        ]))
    corrected, _report, _diag = correct_document(pages)
    profile = learn_profile(pages, corrected)
    _slots, lines = cluster_entry(profile, "A")
    print(f"  learned lines: {sorted(lines)}")
    return "SHIPPING WORLDWIDE" in lines and "SHIPPING WORLOWIDE" not in lines


def test_profile_never_asserts_an_amount():
    """No learned canonical carries a currency figure."""
    pages = _big_document()
    corrected, _report, _diag = correct_document(pages)
    profile = learn_profile(pages, corrected)
    slots, lines = cluster_entry(profile, "A")
    everything = list(slots.values()) + list(lines)
    money = [t for t in everything if "$" in t]
    print(f"  canonicals={len(everything)} money-bearing={money}")
    return len(money) == 0


def test_profile_rescues_a_document_too_short_to_vote():
    """One page cannot vote, but an asserted canonical still corrects it."""
    pages = _big_document()
    corrected, _report, _diag = correct_document(pages)
    profile = learn_profile(pages, corrected)

    lonely = [_page(1, GARBLED, 0.60)]
    _c, report_without, diag_without = correct_document(lonely)
    _c2, report_with, diag_with = correct_document(lonely, profile=profile)
    print(f"  without profile: {len(report_without)} corrections, "
          f"can_vote={diag_without['clusters'][0][2]}")
    print(f"  with profile:    {len(report_with)} corrections, "
          f"can_vote={diag_with['clusters'][0][2]}")
    restored = any(new == FOOTER for _p, _k, _o, new in report_with)
    return len(report_without) == 0 and restored


def test_amount_line_is_never_rewritten():
    """A low-confidence amount is left alone, not restated from a sibling."""
    pages = _big_document()
    victim = _page(99, FOOTER, 0.99, amount="105.00")
    victim.lines[2] = PageLine("Forwarding Fee and Handling  $105.00", 0.62)
    pages.append(victim)
    _c, report, _diag = correct_document(pages)
    touched = [(o, n) for _p, _k, o, n in report if "$" in o or "$" in n]
    print(f"  edits touching an amount: {touched}")
    return len(touched) == 0


def main():
    tests = [
        test_profile_learns_slot_canonicals,
        test_profile_never_learns_a_repeated_misreading,
        test_profile_never_asserts_an_amount,
        test_profile_rescues_a_document_too_short_to_vote,
        test_amount_line_is_never_rewritten,
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
