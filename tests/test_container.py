"""Tests for container magic-byte sniffing.

tests/test_container.py

Runnable under pytest, but written to run standalone: each test prints what it
checked, returns True/False, and main() accumulates a score. The point is that
the extension is never trusted -- a ``.pdf`` that is really a ZIP bundle must be
detected as a bundle from its bytes.
"""

import tempfile

from smart_pdf_ocr.ingest.container import sniff, page_count, iter_rendered_pages


def _deps():
    import importlib.util
    return (importlib.util.find_spec("PIL") is not None
            and importlib.util.find_spec("pypdfium2") is not None)


def _small_pdf(pages=3):
    from PIL import Image
    frames = [Image.new("RGB", (200, 100), "white") for _ in range(pages)]
    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    handle.close()
    frames[0].save(handle.name, "PDF", resolution=72.0, save_all=True,
                   append_images=frames[1:])
    return handle.name


def _sniff_bytes(head):
    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    handle.write(head + b"\x00" * 8)
    handle.close()
    return sniff(handle.name)


def test_sniff_real_pdf():
    """A genuine PDF header is reported as 'pdf'."""
    kind = _sniff_bytes(b"%PDF-1.7")
    print(f"  %PDF header -> {kind!r}")
    return kind == "pdf"


def test_sniff_zip_bundle():
    """A ZIP header (image bundle disguised as .pdf) is reported as 'bundle'."""
    kind = _sniff_bytes(b"PK\x03\x04")
    print(f"  PK.. header -> {kind!r}")
    return kind == "bundle"


def test_sniff_bare_jpeg():
    """A JPEG header is reported as 'image'."""
    kind = _sniff_bytes(b"\xff\xd8\xff\xe0")
    print(f"  JFIF header -> {kind!r}")
    return kind == "image"


def test_sniff_unknown():
    """Unrecognized bytes are reported as 'unknown', not guessed."""
    kind = _sniff_bytes(b"garbage!")
    print(f"  garbage header -> {kind!r}")
    return kind == "unknown"


def test_page_count_without_rendering():
    """The page total is knowable before any page is rendered."""
    if not _deps():
        print("  skipped: pillow or pypdfium2 absent")
        return True
    pdf = _small_pdf(3)
    count = page_count(pdf)
    print(f"  page_count -> {count}")
    return count == 3


def test_rendered_pages_stream_in_order():
    """The streaming renderer yields numbered pages, each on disk when yielded."""
    if not _deps():
        print("  skipped")
        return True
    import os
    pdf = _small_pdf(3)
    workdir = tempfile.mkdtemp()
    seen = []
    for number, path in iter_rendered_pages(pdf, workdir, 72):
        seen.append((number, os.path.exists(path)))
    print(f"  {seen}")
    return seen == [(1, True), (2, True), (3, True)]


def main():
    tests = [
        test_sniff_real_pdf,
        test_sniff_zip_bundle,
        test_sniff_bare_jpeg,
        test_sniff_unknown,
        test_page_count_without_rendering,
        test_rendered_pages_stream_in_order,
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
