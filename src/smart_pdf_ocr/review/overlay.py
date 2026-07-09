"""Human correction overlay: forced edits applied over the automatic result.

smart_pdf_ocr/review/overlay.py

Rules match against the raw OCR text, never against what the tool produced, so a
forced edit deterministically overrides automatic correction and re-running with
the same file changes nothing further. A human edit outranks every internal veto,
including the numeric-field guard -- the reviewer is authoritative -- but each
forced edit is reported so downstream consumers can see the text was touched.

A rule that matches nothing is reported as stale rather than dropped silently:
that is the signal that the recognizer's output moved underneath the rule.
"""

import csv

from dataclasses import dataclass

from smart_pdf_ocr.review.schema import SCOPE_ALL, SCOPE_PAGE, rule_id, delimiter_for


@dataclass
class Rule:
    page: int
    original: str
    corrected: str
    scope: str
    stated_id: str


def _clean(value):
    if value is None:
        return ""
    return value.replace("\r", "").strip()


def load_rules(path):
    """Return (rules, warnings). Only rows with a correction become rules."""
    rules = []
    warnings = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter_for(path))
        for number, row in enumerate(reader, start=2):
            if not isinstance(row, dict):
                continue
            corrected = _clean(row.get("corrected_text"))
            if not corrected:
                continue
            original = _clean(row.get("original_text"))
            if not original:
                warnings.append("row %d: correction given with no original_text" % number)
                continue
            page_text = _clean(row.get("page"))
            if not page_text.isdigit():
                warnings.append("row %d: page is not a number" % number)
                continue
            page = int(page_text)
            scope = _clean(row.get("scope")).lower() or SCOPE_PAGE
            if scope not in (SCOPE_PAGE, SCOPE_ALL):
                warnings.append("row %d: unknown scope %r, using %r" % (number, scope, SCOPE_PAGE))
                scope = SCOPE_PAGE
            stated = _clean(row.get("rule_id"))
            if stated and stated != rule_id(page, original):
                warnings.append(
                    "row %d: rule_id %r does not match original_text; "
                    "the spreadsheet may have altered the text" % (number, stated))
            rules.append(Rule(page, original, corrected, scope, stated))
    return rules, warnings


def _matches(rule, page_number, text):
    if rule.original != text:
        return False
    if rule.scope == SCOPE_ALL:
        return True
    return rule.page == page_number


def apply_rules(page_results, corrected, rules):
    """Force corrections in place. Return (forced_report, stale_rules)."""
    if not rules:
        return [], []
    fired = set()
    forced = []
    for page in page_results:
        lines_out = corrected.get(page.page_number)
        if lines_out is None:
            continue
        for index, line in enumerate(page.lines):
            for position, rule in enumerate(rules):
                if not _matches(rule, page.page_number, line.text):
                    continue
                fired.add(position)
                if lines_out[index] != rule.corrected:
                    previous = lines_out[index]
                    lines_out[index] = rule.corrected
                    forced.append((page.page_number, "human", previous, rule.corrected))
                break
    stale = [rule for position, rule in enumerate(rules) if position not in fired]
    return forced, stale


# End of file #
