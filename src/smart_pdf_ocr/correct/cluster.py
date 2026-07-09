"""Format clustering: assign each page to a layout family.

smart_pdf_ocr/correct/cluster.py

Boilerplate voting must compare like with like. A document here mixes two
invoice templates (A and B), so pages are grouped by a signature-token match
before any cross-page consensus runs. Unknown-format pages cluster on their
own and simply receive no boilerplate canonicals.
"""

from smart_pdf_ocr.patterns.field_rgx import FORMAT_A_rgx, FORMAT_B_rgx


def page_format(page_result):
    blob = " ".join(page_result.texts)
    if FORMAT_B_rgx.search(blob):
        return "B"
    if FORMAT_A_rgx.search(blob):
        return "A"
    return "?"


def cluster_pages(page_results):
    clusters = {}
    for page in page_results:
        key = page_format(page)
        clusters.setdefault(key, []).append(page)
    return clusters


# End of file #
