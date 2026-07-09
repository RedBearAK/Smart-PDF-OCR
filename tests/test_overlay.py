"""Tests for the human-correction overlay.

tests/test_overlay.py

Forced corrections must match on the raw OCR text, override the automatic
result, honour page scope by default, and report rules that matched nothing.
"""

import os
import csv
import tempfile

from smart_pdf_ocr.review.schema import rule_id
from smart_pdf_ocr.review.overlay import load_rules, apply_rules
from smart_pdf_ocr.recognize.backend import PageLine, PageResult


HEADER = ["rule_id", "file", "page", "conf", "count", "status",
          "original_text", "tool_text", "corrected_text", "scope", "notes"]


def _pages():
    return [
        PageResult(1, [PageLine("GalaxSea Freight Forwarding", 0.99), PageLine("C/O", 0.88)]),
        PageResult(2, [PageLine("GalaxSea Freight Forwarding", 0.99), PageLine("C/O", 0.88)]),
    ]


def _corrected(pages):
    return {page.page_number: list(page.texts) for page in pages}


def _write(rows):
    handle = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False,
                                         encoding="utf-8", newline="")
    writer = csv.DictWriter(handle, fieldnames=HEADER, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    handle.close()
    return handle.name


def _row(page, original, corrected, scope=""):
    return {"rule_id": rule_id(page, original), "file": "d.pdf", "page": str(page),
            "conf": "0.88", "count": "2", "status": "uncorrected",
            "original_text": original, "tool_text": original,
            "corrected_text": corrected, "scope": scope, "notes": ""}


def test_blank_correction_is_not_a_rule():
    """A row left blank means 'accept the tool output' and creates no rule."""
    path = _write([_row(1, "C/O", "")])
    rules, warnings = load_rules(path)
    os.unlink(path)
    print(f"  rules={len(rules)} warnings={warnings}")
    return len(rules) == 0


def test_page_scope_is_the_default():
    """A correction with blank scope touches only its own page."""
    path = _write([_row(1, "C/O", "C/O Fee")])
    rules, _w = load_rules(path)
    os.unlink(path)
    pages = _pages()
    corrected = _corrected(pages)
    forced, stale = apply_rules(pages, corrected, rules)
    print(f"  page1={corrected[1][1]!r} page2={corrected[2][1]!r} forced={len(forced)}")
    return corrected[1][1] == "C/O Fee" and corrected[2][1] == "C/O" and not stale


def test_scope_all_touches_every_instance():
    """scope=all rewrites the same original text on every page."""
    path = _write([_row(1, "C/O", "C/O Fee", scope="all")])
    rules, _w = load_rules(path)
    os.unlink(path)
    pages = _pages()
    corrected = _corrected(pages)
    apply_rules(pages, corrected, rules)
    print(f"  page1={corrected[1][1]!r} page2={corrected[2][1]!r}")
    return corrected[1][1] == "C/O Fee" and corrected[2][1] == "C/O Fee"


def test_stale_rule_is_reported_not_applied():
    """A rule whose original text is absent matches nothing and is reported."""
    path = _write([_row(1, "TEXT THAT IS NOT THERE", "anything")])
    rules, _w = load_rules(path)
    os.unlink(path)
    pages = _pages()
    corrected = _corrected(pages)
    forced, stale = apply_rules(pages, corrected, rules)
    print(f"  forced={len(forced)} stale={len(stale)}")
    return len(forced) == 0 and len(stale) == 1


def test_overlay_is_idempotent():
    """Applying the same rules twice yields the same text."""
    path = _write([_row(1, "C/O", "C/O Fee")])
    rules, _w = load_rules(path)
    os.unlink(path)
    pages = _pages()
    corrected = _corrected(pages)
    apply_rules(pages, corrected, rules)
    first = list(corrected[1])
    apply_rules(pages, corrected, rules)
    print(f"  first={first} second={corrected[1]}")
    return first == corrected[1]


def main():
    tests = [
        test_blank_correction_is_not_a_rule,
        test_page_scope_is_the_default,
        test_scope_all_touches_every_instance,
        test_stale_rule_is_reported_not_applied,
        test_overlay_is_idempotent,
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
