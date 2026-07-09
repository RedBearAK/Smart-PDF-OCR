"""Durable vendor profile: canonicals that outlive a single document.

smart_pdf_ocr/review/profile.py

Cross-page consensus needs a corpus. A profile is that corpus, frozen: the
canonical text of each template slot and each standing line, learned from a
document that had enough pages to vote, and reused on documents that do not.

This is the part that compounds. These invoices arrive monthly on a stable
template, so a canonical confirmed once -- by consensus, or by a human reviewer
overriding it -- makes every later document need less correction, including
short ones where no consensus is possible at all.

Which is exactly why a profile must never learn an error. A recognizer that
misreads the same word the same way on three pages manufactures a counterfeit
canonical, and freezing it would carry the mistake into every future document. A
rare spelling contradicted by a far more frequent sibling is discarded before
anything is written down.
"""

import json

from smart_pdf_ocr.correct.lexical import choose
from smart_pdf_ocr.correct.cluster import page_format
from smart_pdf_ocr.correct.consensus import (
    CLEAN_CONF,
    MIN_CLEAN_SIBLINGS,
    carries_numeric_field,
    drop_near_misses,
)


# A profile canonical is asserted, not voted on, so it outranks any page count.
PROFILE_SUPPORT = 10 ** 6

SLOT_DEPTH = 4
MIN_LINE_LENGTH = 18


def _trusted(line, final_text):
    """Trustworthy if it read cleanly, or correction rewrote it. Never a figure.

    A durable canonical outlives the document it was learned from, so anything
    carrying a variable figure is excluded outright -- next month's invoice has
    different amounts, and an asserted amount would overwrite them.
    """
    if carries_numeric_field(final_text):
        return False
    if line.conf >= CLEAN_CONF:
        return True
    return final_text != line.text


def _tally(store, key, text):
    store.setdefault(key, {}).setdefault(text, 0)
    store[key][text] += 1


def _modal(tally, minimum, plausible=None):
    out = {}
    for key, counts in tally.items():
        text, count = choose(counts, plausible)
        if count >= minimum:
            out[key] = text
    return out


def learn_profile(page_results, corrected, plausible=None):
    """Build a profile from a corrected document. Returns a plain dict."""
    slots = {}
    lines = {}
    for page in page_results:
        final = corrected.get(page.page_number)
        if final is None:
            continue
        key = page_format(page)
        count = len(page.lines)
        for k in range(min(SLOT_DEPTH, count)):
            head = page.lines[k]
            if _trusted(head, final[k]):
                _tally(slots, (key, "top:%d" % k), final[k])
            tail_index = count - 1 - k
            tail = page.lines[tail_index]
            if _trusted(tail, final[tail_index]):
                _tally(slots, (key, "bottom:%d" % k), final[tail_index])
        for index, line in enumerate(page.lines):
            text = final[index]
            if len(text) >= MIN_LINE_LENGTH and _trusted(line, text):
                _tally(lines, key, text)

    slot_canon = _modal(slots, MIN_CLEAN_SIBLINGS, plausible)
    profile = {"version": 1, "clusters": {}}
    for (key, slot), text in slot_canon.items():
        profile["clusters"].setdefault(key, {"slots": {}, "lines": []})
        profile["clusters"][key]["slots"][slot] = text
    for key, counts in lines.items():
        profile["clusters"].setdefault(key, {"slots": {}, "lines": []})
        survivors = drop_near_misses(counts, MIN_CLEAN_SIBLINGS)
        kept = [t for t, n in survivors.items() if n >= MIN_CLEAN_SIBLINGS]
        profile["clusters"][key]["lines"] = sorted(kept)
    return profile


def save_profile(path, profile):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_profile(path):
    with open(path, encoding="utf-8-sig") as handle:
        try:
            raw = json.load(handle)
        except ValueError as exc:
            raise ValueError("profile " + str(path) + " is not valid JSON: " + str(exc))
    if not isinstance(raw, dict):
        raise ValueError("profile " + str(path) + " must be a JSON object")
    clusters = raw.get("clusters")
    if not isinstance(clusters, dict):
        raise ValueError("profile " + str(path) + " has no clusters object")
    return raw


def cluster_entry(profile, key):
    """Return (slots, lines) for a format cluster; empty when absent."""
    if not profile:
        return {}, []
    clusters = profile.get("clusters")
    if not isinstance(clusters, dict):
        return {}, []
    entry = clusters.get(key)
    if not isinstance(entry, dict):
        return {}, []
    slots = entry.get("slots")
    lines = entry.get("lines")
    slots = slots if isinstance(slots, dict) else {}
    lines = lines if isinstance(lines, list) else []
    return slots, lines


def counts_of(profile):
    slots = 0
    lines = 0
    for key in (profile.get("clusters") or {}):
        entry_slots, entry_lines = cluster_entry(profile, key)
        slots += len(entry_slots)
        lines += len(entry_lines)
    return slots, lines


# End of file #
