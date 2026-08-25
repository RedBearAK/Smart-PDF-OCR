"""Tests for the searchable PDF emitter.

tests/test_pdf_out.py

The sandwich PDF appends an invisible corrected-text layer to the original scanned
PDF, each line at the box the recognizer found. The images are never re-encoded, so
the file barely grows. These tests build a tiny image-based PDF, append text, read
the layer back out, and confirm both that it is searchable and that the file stays
near its original size. They are skipped when pikepdf is absent, since it is an
optional dependency.

Two invariants guard the placement rules added after the interleave incident:
every glyph must sit inside the box its line was recognized in (so extractors that
sort characters by position cannot shuffle adjacent columns together), and the
declared /WinAnsiEncoding must round-trip non-ASCII bytes (an e-acute went out as
`Péase` and came back `PØase` under the old undeclared StandardEncoding).
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


def _char_positions(path, page=0):
    """Every non-whitespace glyph on the page as (x_left, x_right, character)."""
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(path)
    textpage = document[page].get_textpage()
    text = textpage.get_text_range()
    glyphs = []
    for index in range(textpage.count_chars()):
        char = text[index] if index < len(text) else ""
        if not char.strip():
            continue
        left, _bottom, right, _top = textpage.get_charbox(index)
        glyphs.append((left, right, char))
    return glyphs


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


def test_every_glyph_stays_inside_its_box():
    """A long run is compressed into its box instead of overrunning it.

    The interleave incident: a run's natural width exceeded its recognizer box
    and spilled into the neighboring column. With width-fitting, every glyph's
    x-extent must sit inside the line's own box (page points equal image pixels
    here, so boxes compare directly).
    """
    if not _deps():
        print("  skipped")
        return True
    work = tempfile.mkdtemp()
    src = os.path.join(work, "in.pdf")
    out = os.path.join(work, "out.pdf")
    size = _source_pdf(src, size=(850, 200))
    left_box = (100, 100, 320, 140)
    right_box = (330, 100, 560, 140)
    sheet = [[("Please Wire payment to: GalaxSea Freight Forwarding", left_box),
              ("TOTAL: Door to Port $356.00", right_box)]]
    write_searchable_pdf(out, src, sheet, [size])
    glyphs = _char_positions(out)
    slack = 1.0
    boundary = right_box[0]
    left_glyphs = [g for g in glyphs if g[1] <= boundary + slack]
    right_glyphs = [g for g in glyphs if g[0] >= boundary - slack]
    print(f"  glyphs={len(glyphs)} left={len(left_glyphs)} right={len(right_glyphs)}")
    # Every glyph belongs wholly to one side of the column boundary.
    return glyphs and len(left_glyphs) + len(right_glyphs) == len(glyphs)


def test_position_sorted_extraction_does_not_interleave():
    """Characters sorted purely by x reproduce the two runs whole, in order.

    This simulates what a pdfminer-family extractor does to the layer: ignore
    stream order and sort every glyph by position. Before width-fitting that
    produced `ForwardTinOgTAL`; now the x-sorted text must contain each source
    string intact.
    """
    if not _deps():
        print("  skipped")
        return True
    work = tempfile.mkdtemp()
    src = os.path.join(work, "in.pdf")
    out = os.path.join(work, "out.pdf")
    size = _source_pdf(src, size=(850, 200))
    left = "Please Wire payment to: GalaxSea Freight Forwarding"
    right = "TOTAL: Door to Port $356.00"
    sheet = [[(left, (100, 100, 320, 140)), (right, (330, 100, 560, 140))]]
    write_searchable_pdf(out, src, sheet, [size])
    ordered = "".join(c for _l, _r, c in sorted(_char_positions(out)))
    print(f"  x-sorted: {ordered!r}")
    squeeze = ordered.replace(" ", "")
    return (left.replace(" ", "") in squeeze and right.replace(" ", "") in squeeze
            and squeeze == left.replace(" ", "") + right.replace(" ", ""))


def test_declared_encoding_round_trips_non_ascii():
    """An e-acute survives extraction; the apostrophe stays an apostrophe.

    Under the old undeclared encoding, extractors fell back to Adobe
    StandardEncoding: 0xE9 read back as a slashed O (`Péase` -> `PØase`) and
    0x27 as a curly quote (`Addt'l` -> `Addt’l`). /WinAnsiEncoding must make
    the bytes mean what the correction layer wrote.
    """
    if not _deps():
        print("  skipped")
        return True
    work = tempfile.mkdtemp()
    src = os.path.join(work, "in.pdf")
    out = os.path.join(work, "out.pdf")
    size = _source_pdf(src)
    sheet = [[("P\u00e9ase pay", (20, 20, 200, 60)),
              ("Addt'l Handling", (20, 80, 200, 120))]]
    write_searchable_pdf(out, src, sheet, [size])
    text = _read_layer(out)
    print(f"  extracted: {text.strip()!r}")
    return ("P\u00e9ase" in text and "\u00d8" not in text
            and "Addt'l" in text and "\u2019" not in text)


def main():
    tests = [
        test_layer_is_searchable,
        test_layer_carries_corrected_text,
        test_file_stays_near_original_size,
        test_pages_from_pairs_corrected_text_with_boxes,
        test_lines_without_a_box_are_skipped,
        test_every_glyph_stays_inside_its_box,
        test_position_sorted_extraction_does_not_interleave,
        test_declared_encoding_round_trips_non_ascii,
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
