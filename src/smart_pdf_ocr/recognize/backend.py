"""Recognition front-end: page records and a cached-OCR loader.

smart_pdf_ocr/recognize/backend.py

Backends are pluggable and independently available at runtime. Each yields a
``PageResult`` holding ``PageLine`` records (text + confidence + optional box).
A recognizer that is not installed reports itself unavailable rather than
raising at import time, so the package imports cleanly without any engine.
"""

import os
import json

from dataclasses import dataclass, field


@dataclass
class PageLine:
    text: str
    conf: float
    box: tuple = ()


@dataclass
class PageResult:
    page_number: int
    lines: list = field(default_factory=list)

    @property
    def texts(self):
        return [line.text for line in self.lines]


def load_cached_ocr(path):
    """Load a ``{page: {txts, scores}}`` JSON dump into ordered PageResults."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as handle:
        try:
            raw = json.load(handle)
        except ValueError as exc:
            raise ValueError("cached OCR " + str(path) + " is not valid JSON: " + str(exc))
    if not isinstance(raw, dict):
        raise ValueError("cached OCR " + str(path) + " must be a page-keyed object")
    results = []
    for key in sorted(raw, key=int):
        entry = raw[key]
        if not isinstance(entry, dict):
            continue
        txts = entry.get("txts", [])
        scores = entry.get("scores", [])
        lines = [PageLine(str(t), float(s)) for t, s in zip(txts, scores)]
        results.append(PageResult(int(key), lines))
    return results


# End of file #
