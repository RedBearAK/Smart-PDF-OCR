"""Correction pipeline: cluster, learn, confidence-gate, recover, assemble.

smart_pdf_ocr/correct/pipeline.py

Given recognizer output for every page, this stage clusters pages by format,
learns the boilerplate slot canonicals and the corpus lexicon, then touches only
confidence-flagged lines: boilerplate voting first, lexical repair as a fallback.

Two honesty rules govern the output. An edit that changes only whitespace is not
a correction and is discarded. And a low-confidence line that could not be
corrected is recorded, with the reason, rather than passed over silently -- a
document whose format cluster holds fewer than MIN_CLEAN_SIBLINGS clean reads
cannot vote at all, and should say so instead of appearing to have worked.
"""

from smart_pdf_ocr.correct.cluster import cluster_pages
from smart_pdf_ocr.correct.errors import KIND_ERRORFIX, apply_patterns
from smart_pdf_ocr.correct.disagree import (
    DISAGREE_RATIO,
    MIN_DISAGREE_LENGTH,
    TYPOGRAPHIC_KINDS,
    find_disagreements,
)
from smart_pdf_ocr.correct.normalize import (
    KIND_ASCII,
    MODE_EVIDENCE,
    MODE_OFF,
    has_non_ascii,
    normalize_line,
)
from smart_pdf_ocr.correct.lexicon import build_lexicon, repair_line
from smart_pdf_ocr.patterns.field_rgx import WHITESPACE_rgx
from smart_pdf_ocr.review.profile import PROFILE_SUPPORT, cluster_entry
from smart_pdf_ocr.correct.consensus import (
    CLEAN_CONF,
    MIN_CLEAN_SIBLINGS,
    build_slot_canon,
    build_line_index,
    recover_page,
)


# A garbled page needs MIN_CLEAN_SIBLINGS *other* pages to outvote it, so a
# format cluster cannot correct anything until it holds one more page than that.
MIN_CLUSTER_PAGES = MIN_CLEAN_SIBLINGS + 1

NO_SUPPORT = "format cluster too small for consensus (needs %d pages)" % MIN_CLUSTER_PAGES
NO_CANONICAL = "fewer than %d clean sibling reads for this line" % MIN_CLEAN_SIBLINGS


def _is_cosmetic(old, new):
    """True when a proposed edit changes nothing but whitespace."""
    return WHITESPACE_rgx.sub("", old) == WHITESPACE_rgx.sub("", new)


def _all_texts(pages):
    counts = {}
    for page in pages:
        for line in page.lines:
            counts[line.text] = counts.get(line.text, 0) + 1
    return counts


def _frequent_texts(pages, minimum):
    """Texts a cluster agrees on often enough to treat as its canonical wording."""
    counts = {}
    for page in pages:
        for line in page.lines:
            counts[line.text] = counts.get(line.text, 0) + 1
    return {t: c for t, c in counts.items() if len(t) >= MIN_DISAGREE_LENGTH and c >= minimum}


def _apply_profile(top_canon, bottom_canon, cluster_lines, slots, lines):
    """Overlay asserted canonicals onto the voted ones, at unbeatable support."""
    for slot, text in slots.items():
        if not isinstance(slot, str) or ":" not in slot:
            continue
        side, _sep, index = slot.partition(":")
        if not index.isdigit():
            continue
        target = top_canon if side == "top" else bottom_canon
        target[int(index)] = (text, PROFILE_SUPPORT)
    known = {text for text, _count in cluster_lines}
    for text in lines:
        if text not in known:
            cluster_lines.append((text, PROFILE_SUPPORT))
    return top_canon, bottom_canon, cluster_lines


def correct_document(page_results, conf_gate=0.90, profile=None, ascii_mode=MODE_EVIDENCE,
                     plausible=None, disagree_ratio=DISAGREE_RATIO, known=frozenset(),
                     error_patterns=(), vocabulary=(), typography=True):
    clusters = cluster_pages(page_results)
    lexicon = build_lexicon(page_results)

    corrected = {}
    report = []
    cluster_info = []
    uncorrected = []
    disagreements = []
    unresolved_non_ascii = []
    protected = []
    error_events = []
    typographic = {}

    for key, pages in clusters.items():
        slots, lines = cluster_entry(profile, key)
        has_profile = bool(slots) or bool(lines)
        can_vote = has_profile or len(pages) >= MIN_CLUSTER_PAGES
        cluster_info.append((key, len(pages), can_vote))

        top_canon, bottom_canon = build_slot_canon(pages, plausible=plausible)
        cluster_lines = build_line_index(pages)
        if has_profile:
            top_canon, bottom_canon, cluster_lines = _apply_profile(
                top_canon, bottom_canon, cluster_lines, slots, lines)

        attested = _all_texts(pages)
        disagree_support = max(MIN_CLEAN_SIBLINGS, len(pages) // 4)
        canon_counts = _frequent_texts(pages, disagree_support)
        for text in lines:
            canon_counts[text] = max(canon_counts.get(text, 0), disagree_support)
        found = find_disagreements(pages, canon_counts, conf_gate,
                                   disagree_support, disagree_ratio)
        disagreements.extend(found)
        if typography:
            for page_number, _conf, text, canonical, kind in found:
                if kind in TYPOGRAPHIC_KINDS:
                    typographic[(page_number, text)] = canonical

        for page in pages:
            lines_out = list(page.texts)
            fixed_index = set()

            guard_events = []
            if can_vote:
                for index, old, new in recover_page(page, top_canon, bottom_canon,
                                                    cluster_lines, conf_gate,
                                                    known=known, events=guard_events):
                    if _is_cosmetic(old, new):
                        continue
                    lines_out[index] = new
                    fixed_index.add(index)
                    report.append((page.page_number, "boilerplate", old, new))

            for index, line in enumerate(page.lines):
                if index in fixed_index or line.conf >= conf_gate:
                    continue
                repaired = repair_line(line.text, lexicon, known, events=guard_events)
                if repaired != line.text and not _is_cosmetic(line.text, repaired):
                    lines_out[index] = repaired
                    fixed_index.add(index)
                    report.append((page.page_number, "lexicon", line.text, repaired))

            for guard, before, after in guard_events:
                protected.append((page.page_number, guard, before, after))

            if ascii_mode != MODE_OFF:
                for index, current in enumerate(lines_out):
                    if not has_non_ascii(current):
                        continue
                    plain = normalize_line(current, attested, ascii_mode, MIN_CLEAN_SIBLINGS)
                    if plain != current:
                        lines_out[index] = plain
                        fixed_index.add(index)
                        report.append((page.page_number, KIND_ASCII,
                                       page.lines[index].text, plain))
                    elif attested.get(current, 0) < MIN_CLEAN_SIBLINGS:
                        unresolved_non_ascii.append(
                            (page.page_number, page.lines[index].conf, current))

            if error_patterns:
                for index, current in enumerate(lines_out):
                    replaced, events = apply_patterns(current, error_patterns, vocabulary)
                    for kind, pattern, _before, detail in events:
                        if kind == KIND_ERRORFIX:
                            continue
                        error_events.append((page.page_number, page.lines[index].conf,
                                             current, pattern, detail))
                    if replaced != current:
                        lines_out[index] = replaced
                        fixed_index.add(index)
                        report.append((page.page_number, KIND_ERRORFIX,
                                       page.lines[index].text, replaced))

            for index, line in enumerate(page.lines):
                if index in fixed_index:
                    continue
                canonical = typographic.get((page.page_number, line.text))
                if canonical is None or canonical == lines_out[index]:
                    continue
                lines_out[index] = canonical
                fixed_index.add(index)
                report.append((page.page_number, "typography", line.text, canonical))

            for index, line in enumerate(page.lines):
                if index in fixed_index or line.conf >= conf_gate:
                    continue
                reason = NO_CANONICAL if can_vote else NO_SUPPORT
                uncorrected.append((page.page_number, line.conf, line.text, reason))

            corrected[page.page_number] = lines_out

    corrected_olds = {(page_number, old) for page_number, _kind, old, _new in report}
    disagreements = [entry for entry in disagreements
                     if (entry[0], entry[2]) not in corrected_olds]

    diagnostics = {
        "clean_conf": CLEAN_CONF,
        "min_clean_siblings": MIN_CLEAN_SIBLINGS,
        "min_cluster_pages": MIN_CLUSTER_PAGES,
        "clusters": cluster_info,
        "uncorrected": uncorrected,
        "disagreements": disagreements,
        "unresolved_non_ascii": unresolved_non_ascii,
        "protected": protected,
        "error_events": error_events,
    }
    return corrected, report, diagnostics


def assemble_text(corrected):
    """Join corrected pages into one text dump, each headed by a page marker.

    Every page is introduced by a ``=== page N ===`` line so a reader -- human or a
    downstream tool -- can tell which page any line came from. Pages are separated by
    two blank lines before the marker; the first page has no leading blank lines.
    """
    chunks = []
    for page_number in sorted(corrected):
        marker = "=== page {0} ===".format(page_number)
        body = "\n".join(corrected[page_number])
        chunks.append(marker + "\n" + body)
    return "\n\n\n".join(chunks)


# End of file #
