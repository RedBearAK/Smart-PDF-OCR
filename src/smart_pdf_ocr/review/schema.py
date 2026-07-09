"""Review-file schema: columns, scopes, and the content-derived rule key.

smart_pdf_ocr/review/schema.py

A rule is keyed by page plus a hash of the raw OCR text, never by line index.
Line indices are detector order, not geometry: re-rasterizing the same page at a
different DPI reorders them, so an index-keyed rule would silently rewrite the
wrong line. Hashing the original text makes a rule self-invalidating instead --
when the recognizer's output changes, the key no longer matches and the rule is
reported stale rather than misapplied.
"""

import hashlib


COLUMNS = (
    "rule_id",
    "file",
    "page",
    "conf",
    "count",
    "status",
    "original_text",
    "tool_text",
    "corrected_text",
    "scope",
    "notes",
)

SCOPE_PAGE = "page"
SCOPE_ALL = "all"

STATUS_UNCORRECTED = "uncorrected"
KIND_HUMAN = "human"


def rule_id(page_number, original_text):
    """Stable, spreadsheet-safe key: page number plus a hash of the raw text."""
    digest = hashlib.sha1(original_text.encode("utf-8")).hexdigest()
    return "p%04d-%s" % (int(page_number), digest[:8])


def delimiter_for(path):
    """Tab-separated unless the caller explicitly asked for .csv."""
    if str(path).lower().endswith(".csv"):
        return ","
    return "\t"


# End of file #
