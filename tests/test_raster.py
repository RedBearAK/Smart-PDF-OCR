"""Tests for PDF renderer detection and its failure message.

tests/test_raster.py

Rasterizing a real PDF is the one place the tool can depend on something pip
cannot install. Detection must prefer the wheel (pypdfium2), accept poppler as a
fallback, and, when neither exists, fail with instructions rather than a bare
FileNotFoundError from a missing binary.
"""

import smart_pdf_ocr.ingest.container as container

from smart_pdf_ocr.ingest.container import FALLBACK_DPI, available_renderer, resolve_dpi, MISSING_RENDERER_MESSAGE


def test_renderer_detected():
    """Some renderer is found in an environment that has one installed."""
    found = available_renderer()
    print(f"  available_renderer() -> {found!r}")
    return found in ("pypdfium2", "pdftoppm")


def test_missing_renderer_raises_runtime_error():
    """With no renderer, a RuntimeError carries install instructions."""
    original = container.available_renderer
    container.available_renderer = lambda: ""
    try:
        container._rasterize_pdf("/nonexistent.pdf", "/tmp", 200)
        raised = None
    except RuntimeError as exc:
        raised = exc
    except Exception as exc:
        raised = exc
    finally:
        container.available_renderer = original
    print(f"  raised {type(raised).__name__}")
    return isinstance(raised, RuntimeError)


def test_message_names_both_install_routes():
    """The message tells a macOS and a Linux user what to run."""
    text = MISSING_RENDERER_MESSAGE
    has_pip = "pip install pypdfium2" in text
    has_mac = "brew install poppler" in text
    has_apt = "apt install poppler-utils" in text
    print(f"  pip={has_pip} brew={has_mac} apt={has_apt}")
    return has_pip and has_mac and has_apt


def test_auto_dpi_uses_the_scans_native_resolution():
    """A page with no discoverable scan falls back; one with a scan uses it."""
    original = container.native_dpi
    container.native_dpi = lambda path, sample_pages=4: 400
    try:
        dpi, native, warning = resolve_dpi("whatever.pdf", None)
    finally:
        container.native_dpi = original
    print(f"  auto -> dpi={dpi} native={native} warning={warning!r}")
    return dpi == 400 and native == 400 and not warning


def test_downsampling_is_warned_about():
    """Rendering below native destroys detail and must say so."""
    original = container.native_dpi
    container.native_dpi = lambda path, sample_pages=4: 400
    try:
        _dpi, _native, warning = resolve_dpi("whatever.pdf", 200)
    finally:
        container.native_dpi = original
    print(f"  warning={warning!r}")
    return "below" in warning and "discarded" in warning


def test_unknown_native_resolution_falls_back():
    """A vector PDF has no scan to match, so a stated default is used."""
    original = container.native_dpi
    container.native_dpi = lambda path, sample_pages=4: 0
    try:
        dpi, native, warning = resolve_dpi("vector.pdf", None)
    finally:
        container.native_dpi = original
    print(f"  dpi={dpi} native={native} warning={warning!r}")
    return dpi == FALLBACK_DPI and native == 0 and "unknown" in warning


def main():
    tests = [
        test_renderer_detected,
        test_auto_dpi_uses_the_scans_native_resolution,
        test_downsampling_is_warned_about,
        test_unknown_native_resolution_falls_back,
        test_missing_renderer_raises_runtime_error,
        test_message_names_both_install_routes,
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
