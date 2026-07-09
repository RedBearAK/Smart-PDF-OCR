"""Review-file writer: every line the tool changed or declined to change.

smart_pdf_ocr/review/report.py

The emitted file is both a report and a rule source. A reviewer leaves
``corrected_text`` blank to accept what the tool did, or fills it to force a
different result on the next run. Rows can also be added by hand for lines the
recognizer was confident about but got wrong.
"""

import csv

from smart_pdf_ocr.review.schema import COLUMNS, rule_id, delimiter_for


def _index_pages(page_results):
    confidence = {}
    occurrences = {}
    for page in page_results:
        for line in page.lines:
            confidence[(page.page_number, line.text)] = line.conf
            occurrences[line.text] = occurrences.get(line.text, 0) + 1
    return confidence, occurrences


def build_rows(source_name, page_results, report, diagnostics):
    confidence, occurrences = _index_pages(page_results)
    rows = []
    for page_number, kind, old, new in report:
        note = ""
        if kind == "errorfix":
            note = "error pattern corrected this line"
        elif kind == "typography":
            note = "spacing, punctuation or case adopted from the canonical"
        rows.append({
            "rule_id": rule_id(page_number, old),
            "file": source_name,
            "page": str(page_number),
            "conf": "%.2f" % confidence.get((page_number, old), 0.0),
            "count": str(occurrences.get(old, 1)),
            "status": kind,
            "original_text": old,
            "tool_text": new,
            "corrected_text": "",
            "scope": "",
            "notes": note,
        })
    for page_number, conf, text, canonical, kind in diagnostics.get("disagreements", []):
        rows.append({
            "rule_id": rule_id(page_number, text),
            "file": source_name,
            "page": str(page_number),
            "conf": "%.2f" % conf,
            "count": str(occurrences.get(text, 1)),
            "status": kind,
            "original_text": text,
            "tool_text": text,
            "corrected_text": "",
            "scope": "",
            "notes": "confident, but %d pages read %r" % (occurrences.get(canonical, 0), canonical),
        })
    for page_number, conf, text, pattern, detail in diagnostics.get("error_events", []):
        rows.append({
            "rule_id": rule_id(page_number, text),
            "file": source_name,
            "page": str(page_number),
            "conf": "%.2f" % conf,
            "count": str(occurrences.get(text, 1)),
            "status": "error",
            "original_text": text,
            "tool_text": text,
            "corrected_text": "",
            "scope": "",
            "notes": "error pattern %r matched but could not be corrected: %s"
                     % (pattern, detail),
        })
    for page_number, guard, before, after in diagnostics.get("protected", []):
        rows.append({
            "rule_id": rule_id(page_number, before),
            "file": source_name,
            "page": str(page_number),
            "conf": "",
            "count": str(occurrences.get(before, 1)),
            "status": "protected",
            "original_text": before,
            "tool_text": before,
            "corrected_text": "",
            "scope": "",
            "notes": "%s guard prevented %r" % (guard, after),
        })
    for page_number, conf, text in diagnostics.get("unresolved_non_ascii", []):
        rows.append({
            "rule_id": rule_id(page_number, text),
            "file": source_name,
            "page": str(page_number),
            "conf": "%.2f" % conf,
            "count": str(occurrences.get(text, 1)),
            "status": "nonascii",
            "original_text": text,
            "tool_text": text,
            "corrected_text": "",
            "scope": "",
            "notes": "non-ASCII character with no corpus evidence for folding",
        })
    for page_number, conf, text, reason in diagnostics["uncorrected"]:
        rows.append({
            "rule_id": rule_id(page_number, text),
            "file": source_name,
            "page": str(page_number),
            "conf": "%.2f" % conf,
            "count": str(occurrences.get(text, 1)),
            "status": "uncorrected",
            "original_text": text,
            "tool_text": text,
            "corrected_text": "",
            "scope": "",
            "notes": reason,
        })
    rows.sort(key=lambda row: (int(row["page"]), row["rule_id"]))
    return rows


def write_review(path, source_name, page_results, report, diagnostics):
    rows = build_rows(source_name, page_results, report, diagnostics)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS),
                                delimiter=delimiter_for(path), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


# End of file #
