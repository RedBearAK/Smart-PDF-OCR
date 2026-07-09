"""Tests for quieting the recognizer's log chatter.

tests/test_ocr_logging.py

rapidocr prints a burst of INFO lines when its engine is built and repeats some on
every page. The backend installs a logging filter that drops those while letting
warnings and errors through. These tests exercise the filter directly, so they run
whether or not rapidocr is installed, and confirm the opt-out is honoured.
"""

import os
import logging

import smart_pdf_ocr.recognize.rapidocr_backend as backend


def test_filter_drops_info_keeps_warning():
    """The filter's whole job: INFO out, WARNING and ERROR through."""
    log_filter = backend._BelowWarning()

    def record(level):
        return logging.LogRecord("RapidOCR", level, __file__, 0, "msg", (), None)

    info = log_filter.filter(record(logging.INFO))
    warn = log_filter.filter(record(logging.WARNING))
    error = log_filter.filter(record(logging.ERROR))
    print(f"  info={info} warning={warn} error={error}")
    return info is False and warn is True and error is True


def test_quiet_installs_one_filter():
    """Quieting is idempotent -- calling it twice does not stack filters."""
    logger = logging.getLogger(backend._OCR_LOGGER)
    before = list(logger.filters)
    backend._FILTER_INSTALLED = False
    logger.filters = [f for f in logger.filters if not isinstance(f, backend._BelowWarning)]
    os.environ.pop("SMART_PDF_OCR_OCR_LOG", None)
    backend._quiet_engine_logger()
    backend._quiet_engine_logger()
    added = [f for f in logger.filters if isinstance(f, backend._BelowWarning)]
    logger.filters = before
    backend._FILTER_INSTALLED = False
    print(f"  filters added: {len(added)}")
    return len(added) == 1


def test_opt_out_env_var_keeps_the_logs():
    """With the escape hatch set, no filter is installed."""
    logger = logging.getLogger(backend._OCR_LOGGER)
    before = list(logger.filters)
    backend._FILTER_INSTALLED = False
    logger.filters = [f for f in logger.filters if not isinstance(f, backend._BelowWarning)]
    os.environ["SMART_PDF_OCR_OCR_LOG"] = "1"
    try:
        backend._quiet_engine_logger()
        added = [f for f in logger.filters if isinstance(f, backend._BelowWarning)]
    finally:
        os.environ.pop("SMART_PDF_OCR_OCR_LOG", None)
        logger.filters = before
        backend._FILTER_INSTALLED = False
    print(f"  filters added with opt-out: {len(added)}")
    return len(added) == 0


def main():
    tests = [
        test_filter_drops_info_keeps_warning,
        test_quiet_installs_one_filter,
        test_opt_out_env_var_keeps_the_logs,
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
