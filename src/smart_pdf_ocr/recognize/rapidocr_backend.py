"""PP-OCRv6 recognition backend (primary), via the rapidocr package.

smart_pdf_ocr/recognize/rapidocr_backend.py

The heavy engine is imported lazily so this module (and the whole package) can
be imported on a machine where rapidocr is not installed. ``is_available``
reports installability; ``recognize_page`` runs a single page.

The engine logs a burst of INFO lines when it is constructed -- which onnxruntime
it chose, which model files it found -- and repeats a subset on every page. That is
useful exactly once, when confirming a fresh install, and noise on every run after,
where it interleaves with this tool's own progress and report on stderr.

The lines are dropped with a logging filter rather than a level. rapidocr resets
its logger's level whenever an engine is constructed, so a raised level does not
survive; a filter attached to the logger does, and it must be installed before the
engine is built because the three sub-engines log as they are constructed. The
filter passes warnings and errors untouched. Set ``SMART_PDF_OCR_OCR_LOG`` to keep
all of it.
"""

import os
import logging
import importlib.util

from smart_pdf_ocr.recognize.backend import PageLine, PageResult


_ENGINE = None
_OCR_LOGGER = "RapidOCR"
_FILTER_INSTALLED = False


class _BelowWarning(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.WARNING


def is_available():
    return importlib.util.find_spec("rapidocr") is not None


def _quiet_engine_logger():
    """Filter the recognizer's INFO chatter, unless the user opted out.

    A filter, not a level: the library resets the level on construction, and it
    must be in place before the engine is built, since the sub-engines log as they
    load. Warnings and errors are never filtered.
    """
    global _FILTER_INSTALLED
    if _FILTER_INSTALLED or os.environ.get("SMART_PDF_OCR_OCR_LOG"):
        return
    logger = logging.getLogger(_OCR_LOGGER)
    log_filter = _BelowWarning()
    logger.addFilter(log_filter)
    for handler in logger.handlers:
        handler.addFilter(log_filter)
    _FILTER_INSTALLED = True


def _engine():
    global _ENGINE
    if _ENGINE is None:
        _quiet_engine_logger()
        from rapidocr import RapidOCR
        _ENGINE = RapidOCR()
        _quiet_engine_logger()
    return _ENGINE


def _rectangle(box):
    """Reduce a four-corner OCR box to (x0, y0, x1, y1). Empty when absent."""
    if box is None:
        return ()
    xs, ys = [], []
    for point in box:
        xs.append(float(point[0]))
        ys.append(float(point[1]))
    if not xs:
        return ()
    return (min(xs), min(ys), max(xs), max(ys))


def recognize_page(image_path, page_number):
    result = _engine()(image_path)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    boxes = getattr(result, "boxes", None)
    lines = []
    if texts is not None and scores is not None:
        count = len(texts)
        box_list = list(boxes) if boxes is not None else [None] * count
        for index in range(count):
            box = box_list[index] if index < len(box_list) else None
            lines.append(PageLine(str(texts[index]), float(scores[index]), _rectangle(box)))
    return PageResult(int(page_number), lines)


# End of file #
