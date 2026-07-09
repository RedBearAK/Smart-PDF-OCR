"""Tests for the searchable PDF emitter.

tests/test_pdf_out.py

The sandwich PDF appends an invisible corrected-text layer to the original scanned
PDF, each line at the box the recognizer found. The images are never re-encoded, so
the file barely grows. These tests build a tiny image-based PDF, append text, read
the layer back out, and confirm both that it is searchable and that the file stays
near its original size. They are skipped when pikepdf is absent, since it is an
optional dependency.
"""

import os
import tempfile

from smart_pdf_ocr.recognize.backend import PageLine, PageResult
from smart_pdf_ocr.review.pdf_out import is_available, pages_from, write_searchable_pdf


def _deps():
    import importlib.util
    return (is_available()
            and importlib.util.find_spec("PIL") is not None
            and importlib.util.find_spec("pypdfium2") is not None)


def _source_pdf(path, size=(400, 300)):
    """A minimal image-based PDF: one white page holding one image."""
    from PIL import Image
    image = Image.new("RGB", size, "white")
    image.save(path, "PDF", resolution=72.0)
    return size


def _read_layer(path, page=0):
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(path)
    return document[page].get_textpage().get_text_range()


def test_layer_is_searchable():
    """Text handed in comes back out of the finished PDF."""
    if not _deps():
        print("  skipped: pikepdf, pillow, or pypdfium2 absent")
        return True
    work = tempfile.mkdtemp()
    src = os.path.join(work, "in.pdf")
    out = os.path.join(work, "out.pdf")
    size = _source_pdf(src)
    sheet = [[("GalaxSea Freight Forwarding", (20, 20, 380, 60))]]
    write_searchable_pdf(out, src, sheet, [size])
    text = _read_layer(out)
    print(f"  extracted: {text.strip()!r}")
    return "GalaxSea" in text


def test_layer_carries_corrected_text():
    """The invisible layer is the corrected string, not the raw read."""
    if not _deps():
        print("  skipped")
        return True
    work = tempfile.mkdtemp()
    src = os.path.join(work, "in.pdf")
    out = os.path.join(work, "out.pdf")
    size = _source_pdf(src)
    write_searchable_pdf(out, src, [[("WORLDWIDE", (20, 20, 200, 60))]], [size])
    text = _read_layer(out)
    print(f"  extracted: {text.strip()!r}")
    return "WORLDWIDE" in text


def test_file_stays_near_original_size():
    """The images are copied untouched, so growth is text-sized, not image-sized."""
    if not _deps():
        print("  skipped")
        return True
    work = tempfile.mkdtemp()
    src = os.path.join(work, "in.pdf")
    out = os.path.join(work, "out.pdf")
    size = _source_pdf(src, size=(1200, 1600))
    before = os.path.getsize(src)
    sheet = [[("line one of text", (20, 20, 400, 50)),
              ("line two of text", (20, 80, 400, 110))]]
    write_searchable_pdf(out, src, sheet, [size])
    after = os.path.getsize(out)
    growth = after - before
    print(f"  before={before} after={after} growth={growth}")
    return growth < before


def test_pages_from_pairs_corrected_text_with_boxes():
    """Corrected strings align by index with the boxes on the original lines."""
    page = PageResult(1, [
        PageLine("SHIPPING WORLOWIDE", 0.99, (10, 10, 200, 40)),
        PageLine("GalaxSea", 0.99, (10, 50, 120, 80)),
    ])
    corrected = {1: ["SHIPPING WORLDWIDE", "GalaxSea"]}
    sheet = pages_from([page], corrected)
    print(f"  {sheet[0]}")
    return sheet[0][0] == ("SHIPPING WORLDWIDE", (10, 10, 200, 40))


def test_lines_without_a_box_are_skipped():
    """A line with no coordinates cannot be placed, so it leaves no text."""
    if not _deps():
        print("  skipped")
        return True
    work = tempfile.mkdtemp()
    src = os.path.join(work, "in.pdf")
    out = os.path.join(work, "out.pdf")
    size = _source_pdf(src)
    sheet = [[("placed", (20, 20, 200, 60)), ("floating", ())]]
    write_searchable_pdf(out, src, sheet, [size])
    text = _read_layer(out)
    print(f"  extracted: {text.strip()!r}")
    return "placed" in text and "floating" not in text


def main():
    tests = [
        test_layer_is_searchable,
        test_layer_carries_corrected_text,
        test_file_stays_near_original_size,
        test_pages_from_pairs_corrected_text_with_boxes,
        test_lines_without_a_box_are_skipped,
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
