"""Tests for the self-built corpus lexicon and fuzzy token repair.

tests/test_lexicon.py

Confirms a rare near-neighbour is pulled back to a frequent token, that a line
carrying a numeric field is never rewritten, and that an already-frequent token
is left alone.
"""

from collections import Counter

from smart_pdf_ocr.correct.lexicon import repair_line


def test_rare_variant_pulled_to_frequent():
    """A digit-for-letter corruption is corrected to the frequent token."""
    counts = Counter({"Valdez": 40, "Seattle": 30, "Forwarding": 25})
    out = repair_line("Va1dez", counts)
    print(f"  'Va1dez' -> {out!r}")
    return out == "Valdez"


def test_numeric_line_never_rewritten():
    """A line with a dollar amount is returned untouched."""
    counts = Counter({"Total": 40, "Forwarding": 25})
    out = repair_line("Forwardng Fee $246.00", counts)
    print(f"  amount line -> {out!r}")
    return out == "Forwardng Fee $246.00"


def test_container_id_line_is_never_repaired():
    """SEKU and SEGU are both real owner prefixes; structure protects the line."""
    counts = Counter({"SEGU": 9, "Booking": 40})
    line = "SEKU 1234567 / UL7792815"
    out = repair_line(line, counts)
    print(f"  {line!r} -> {out!r}")
    return out == line


def test_known_pattern_token_is_never_repaired():
    """A term the operator vouched for survives even as a bare rare token."""
    counts = Counter({"SEGU": 9})
    loose = repair_line("SEKU", counts)
    guarded = repair_line("SEKU", counts, frozenset({"seku"}))
    print(f"  unguarded -> {loose!r}   guarded -> {guarded!r}")
    return loose == "SEGU" and guarded == "SEKU"


def test_frequent_token_left_alone():
    """A token already in the frequent set is not altered."""
    counts = Counter({"Seattle": 40})
    out = repair_line("Seattle", counts)
    print(f"  'Seattle' -> {out!r}")
    return out == "Seattle"


def main():
    tests = [
        test_rare_variant_pulled_to_frequent,
        test_numeric_line_never_rewritten,
        test_container_id_line_is_never_repaired,
        test_known_pattern_token_is_never_repaired,
        test_frequent_token_left_alone,
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
