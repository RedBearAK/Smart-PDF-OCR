"""Tests for the invariant-figure rule.

tests/test_figures.py

Not every figure varies. The `$35` wire fee is identical on every page, so a line
carrying it is ordinary boilerplate and refusing to repair it just leaves garbage
in the output. `Forwarding Fee $90.00` carries a figure that differs by page, and
repairing it would restate the amount.

The strings decide. An invariant figure sits inside the stretch the garbled line
and its canonical already agree on; a variable one sits beyond it, in the part a
rewrite would supply.
"""

from smart_pdf_ocr.correct.pipeline import correct_document
from smart_pdf_ocr.correct.consensus import figure_safe
from smart_pdf_ocr.recognize.backend import PageLine, PageResult


CANON = "(include an additional $35 wire fee if paying by wire"
GARBLE = "(include an additional $35 wireLfed if pa85byU@84191"


def _page(number, wire, amount, wire_conf=0.99, amount_conf=0.99):
    return PageResult(number, [
        PageLine("GalaxSea Freight Forwarding", 0.99),
        PageLine("SHIPMENT DETAILS", 0.99),
        PageLine("Forwarding Fee and Handling  ${0}".format(amount), amount_conf),
        PageLine(wire, wire_conf),
    ])


def _document(count=6, amount="90.00"):
    return [_page(i + 1, CANON, amount) for i in range(count)]


def test_invariant_figure_sits_inside_the_agreed_prefix():
    """The $35 is common to both strings, so the tail may be rewritten."""
    ok = figure_safe(GARBLE, CANON)
    print(f"  figure_safe(garbled wire line, canonical) = {ok}")
    return ok is True


def test_variable_amount_is_refused_by_short_prefix():
    """'Forwarding Fee $' is only 16 characters of agreement."""
    ok = figure_safe("Forwarding Fee $105.00", "Forwarding Fee $90.00")
    print(f"  figure_safe('...$105.00', '...$90.00') = {ok}")
    return ok is False


def test_variable_amount_is_refused_even_with_a_long_prefix():
    """A long prefix does not help when the canonical's tail still holds digits."""
    ok = figure_safe("Forwarding Fee and Handling  $105.00",
                     "Forwarding Fee and Handling  $90.00")
    print(f"  long-prefix amount pair -> {ok}")
    return ok is False


def test_low_confidence_invariant_line_is_repaired():
    """The garbled wire-fee line is restored from its clean siblings."""
    pages = _document()
    victim = _page(99, GARBLE, "90.00", wire_conf=0.62)
    pages.append(victim)
    _corrected, report, _diag = correct_document(pages)
    restored = [n for _p, _k, _o, n in report if n == CANON]
    print(f"  corrections={[(o[:24], n[:24]) for _p, _k, o, n in report]}")
    return len(restored) == 1


def test_low_confidence_variable_amount_is_never_repaired():
    """A genuinely different amount survives, even reading badly."""
    pages = _document()
    victim = _page(99, CANON, "90.00")
    victim.lines[2] = PageLine("Forwarding Fee and Handling  $105.00", 0.62)
    pages.append(victim)
    corrected, report, _diag = correct_document(pages)
    touched = [1 for _p, _k, old, _n in report if "$" in old]
    print(f"  page 99 amount line -> {corrected[99][2]!r}")
    return not touched and corrected[99][2] == "Forwarding Fee and Handling  $105.00"


def test_confident_garbled_invariant_line_is_flagged():
    """Above the gate it is not repaired, but prefix agreement still exposes it."""
    pages = _document()
    pages.append(_page(99, GARBLE, "90.00", wire_conf=0.97))
    _corrected, _report, diag = correct_document(pages)
    hits = [e for e in diag["disagreements"] if e[2] == GARBLE]
    print(f"  disagreements={[(e[0], e[2][:26], e[4]) for e in diag['disagreements']]}")
    return len(hits) == 1 and hits[0][3] == CANON


def main():
    tests = [
        test_invariant_figure_sits_inside_the_agreed_prefix,
        test_variable_amount_is_refused_by_short_prefix,
        test_variable_amount_is_refused_even_with_a_long_prefix,
        test_low_confidence_invariant_line_is_repaired,
        test_low_confidence_variable_amount_is_never_repaired,
        test_confident_garbled_invariant_line_is_flagged,
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
