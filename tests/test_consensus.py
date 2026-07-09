"""Tests for cross-page boilerplate recovery.

tests/test_consensus.py

Covers the two recovery paths (position slot and prefix anchor) and, crucially,
the false-positive guard: a low-confidence line that is genuinely variable and
unrelated to any boilerplate must be left untouched.
"""

from smart_pdf_ocr.recognize.backend import PageLine, PageResult
from smart_pdf_ocr.correct.consensus import (
    build_slot_canon,
    build_line_index,
    drop_near_misses,
    recover_page,
)


def _clean_page(number):
    return PageResult(number, [
        PageLine("GalaxSea Freight Forwarding", 0.99),
        PageLine("Please Wire payment to: GalaxSea Freight Forwarding", 0.99),
        PageLine("body value {0}".format(number), 0.99),
        PageLine("411 S 1st Street - Chelan, WA 98816 - Fx# 509-888-0058", 0.99),
    ])


def _pages_with_garble(bad_line_index, bad_text, conf):
    pages = [_clean_page(n) for n in range(1, 7)]
    victim = _clean_page(7)
    victim.lines[bad_line_index] = PageLine(bad_text, conf)
    pages.append(victim)
    return pages, victim


def _recover(pages, victim):
    top, bottom = build_slot_canon(pages)
    index = build_line_index(pages)
    return recover_page(victim, top, bottom, index)


def test_footer_recovered_by_slot():
    """A shredded last line is restored from clean sibling footers."""
    pages, victim = _pages_with_garble(3, "41-- #--- # - - 058", 0.60)
    fixes = _recover(pages, victim)
    ok = any(new.endswith("509-888-0058") for _, _, new in fixes)
    print(f"  fixes: {fixes}")
    return ok


def test_wire_line_recovered_by_prefix():
    """A mid-page line with a clean prefix is restored by prefix anchoring."""
    pages, victim = _pages_with_garble(1, "Please Wire payment to: GaTxSea 4g Fbwd3", 0.82)
    fixes = _recover(pages, victim)
    ok = any(new.endswith("GalaxSea Freight Forwarding") for _, _, new in fixes)
    print(f"  fixes: {fixes}")
    return ok


def test_variable_line_untouched():
    """A low-confidence but genuinely variable line is NOT overwritten."""
    pages, victim = _pages_with_garble(2, "body value 999 (rare wording)", 0.70)
    fixes = _recover(pages, victim)
    print(f"  fixes: {fixes}")
    return len(fixes) == 0


def test_near_miss_of_a_frequent_line_is_not_canonical():
    """A rare spelling contradicted by a frequent sibling never becomes canonical."""
    counts = {"SHIPPING WORLDWIDE": 38, "SHIPPING WORLOWIDE": 3, "Terrina Guempel": 41}
    kept = drop_near_misses(counts)
    print(f"  kept: {sorted(kept)}")
    return "SHIPPING WORLOWIDE" not in kept and "SHIPPING WORLDWIDE" in kept


def test_distinct_lines_are_both_kept():
    """Two genuinely different lines that merely start alike both survive."""
    counts = {"Vessel & Voy: CMA CGM SHANGHAI 0TNUQN1M": 8,
              "Vessel & Voy: CMA CGM SWORDFISH 0GVMTW": 6}
    kept = drop_near_misses(counts)
    print(f"  kept {len(kept)} of 2")
    return len(kept) == 2


def test_high_conf_line_never_touched():
    """Lines at or above the gate are never candidates for change."""
    pages, victim = _pages_with_garble(3, "41-- #--- # - - 058", 0.99)
    fixes = _recover(pages, victim)
    print(f"  fixes: {fixes}")
    return len(fixes) == 0


def main():
    tests = [
        test_footer_recovered_by_slot,
        test_wire_line_recovered_by_prefix,
        test_variable_line_untouched,
        test_near_miss_of_a_frequent_line_is_not_canonical,
        test_distinct_lines_are_both_kept,
        test_high_conf_line_never_touched,
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
