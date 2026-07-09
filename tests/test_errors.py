"""Tests for error patterns and the protection record.

tests/test_errors.py

An error pattern declares a string wrong. It never says what is right -- that is
drawn from the known-patterns vocabulary, and only when the nearest member is
unambiguous. Where the tool cannot decide, it records the encounter and changes
nothing, so a rule that fires always leaves a trace.

A guard that refuses a rewrite records itself too, but only when it actually
refused something. A guard that skipped a line nothing would have touched has
protected nothing, and saying so would bury the cases that matter.
"""

import os
import tempfile

from smart_pdf_ocr.correct.pipeline import correct_document
from smart_pdf_ocr.recognize.backend import PageLine, PageResult
from smart_pdf_ocr.correct.lexical import known_entries, known_tokens
from smart_pdf_ocr.review.report import build_rows
from smart_pdf_ocr.correct.errors import (
    apply_patterns,
    contradictions,
    load_error_patterns,
    malformed,
    resolve,
)


KNOWN = ["KOD", "DUT", "SIT", "TAO", "SEA", "SEKU", "SEGU"]
VOCAB = known_entries(KNOWN)


def _routes():
    routes = ["SEA/KOD/TAO", "SEA/DUT/TAO", "SEA/KOD/QIN", "SEA/SIT/TAO", "SEA/DUT/QIN"]
    pages = [PageResult(i + 1, [PageLine("SHIPMENT DETAILS", 0.99),
                                PageLine("Routing: " + route, 0.99)])
             for i, route in enumerate(routes)]
    pages.append(PageResult(99, [PageLine("SHIPMENT DETAILS", 0.99),
                                 PageLine("Routing: SEA/KOO/TAO", 0.96)]))
    return pages


def test_correction_comes_from_the_known_vocabulary():
    """The error pattern says 'wrong'; the known list says what is right."""
    corrected, report, diag = correct_document(_routes(), error_patterns=["KOO"],
                                               vocabulary=VOCAB)
    kinds = [k for _p, k, _o, _n in report]
    print(f"  p99 -> {corrected[99][1]!r}   report kinds={kinds}")
    return corrected[99][1] == "Routing: SEA/KOD/TAO" and "errorfix" in kinds


def test_ambiguity_is_recorded_not_guessed():
    """Two known patterns equally close means the tool does not know."""
    vocab = known_entries(KNOWN + ["KOB"])
    corrected, report, diag = correct_document(_routes(), error_patterns=["KOO"],
                                               vocabulary=vocab)
    detail = diag["error_events"][0][4] if diag["error_events"] else ""
    print(f"  p99 -> {corrected[99][1]!r}   detail={detail!r}")
    return not report and detail.startswith("ambiguous")


def test_encounter_is_recorded_even_when_nothing_changes():
    """A rule that fires leaves a trace whether or not it could correct."""
    _c, report, diag = correct_document(_routes(), error_patterns=["KOO"],
                                        vocabulary=known_entries(["WORLDWIDE"]))
    print(f"  corrections={len(report)}  error_events={len(diag['error_events'])}")
    return len(report) == 0 and len(diag["error_events"]) == 1


def test_bare_token_matches_only_on_boundaries():
    """'KOO' must not fire inside 'KOOK'."""
    pages = [PageResult(1, [PageLine("Port KOOK Terminal", 0.99)])]
    corrected, _r, diag = correct_document(pages, error_patterns=["KOO"], vocabulary=VOCAB)
    print(f"  {corrected[1][0]!r}  events={len(diag['error_events'])}")
    return corrected[1][0] == "Port KOOK Terminal" and not diag["error_events"]


def test_replacement_respects_token_boundaries():
    """str.replace knows nothing of boundaries; the splice must."""
    line, _events = apply_patterns("Port DAIANX then DAIAN, CHINA", ["DAIAN"], ("DALIAN",))
    print(f"  {line!r}")
    return line == "Port DAIANX then DALIAN, CHINA"


def test_multi_word_pattern_is_refused():
    """A rule that would correct only the first of two words is worse than none."""
    broken = malformed(["Wire information:", "DAIAN", "/KOJ/", "WORL.WIDE"])
    print(f"  malformed -> {broken}")
    return broken == ["Wire information:"]


def test_internal_punctuation_is_part_of_the_term():
    """A full stop put where a letter belongs is the error, not a word boundary."""
    pick, why = resolve("WORL.WIDE", known_entries(["WORLDWIDE", "KOD"]))
    line, _events = apply_patterns("SHIPPING WORL.WIDE", ["WORL.WIDE"],
                                   known_entries(["WORLDWIDE"]))
    print(f"  resolve('WORL.WIDE') -> {pick!r} ({why or 'unique'})   line -> {line!r}")
    return pick == "WORLDWIDE" and line == "SHIPPING WORLDWIDE"


def test_outer_delimiters_are_stripped_inner_kept():
    """'/KOJ/' resolves KOJ; the slashes are context."""
    line, _events = apply_patterns("Routing: SEA/KOJ/TAO", ["/KOJ/"], known_entries(["KOD"]))
    print(f"  {line!r}")
    return line == "Routing: SEA/KOD/TAO"


def test_digits_are_never_altered():
    """A correction that would change a digit is refused."""
    line, events = apply_patterns("code AB1", ["AB1"], ("AB2",))
    print(f"  {line!r}  events={[(e[0], e[3]) for e in events]}")
    return line == "code AB1" and events[0][0] == "error"


def test_resolve_refuses_a_distant_match():
    """Three edits away is not a correction, it is a coincidence."""
    pick, reason = resolve("XQZ", VOCAB)
    print(f"  resolve('XQZ') -> {pick!r} ({reason})")
    return pick is None


def test_contradiction_between_the_two_files():
    """A string declared both wrong and right stops the run."""
    print(f"  contradictions(['SEKU']) -> {contradictions(['SEKU'], VOCAB)}")
    return contradictions(["SEKU"], VOCAB) == ["SEKU"]


def test_known_pattern_outranks_slot_voting_and_records_the_save():
    """Voting beats a bad read, never a term the operator vouched for."""
    pages = [PageResult(i + 1, [PageLine("SHIPMENT DETAILS", 0.99),
                                PageLine("Carrier prefix SEGU", 0.99)]) for i in range(6)]
    pages.append(PageResult(99, [PageLine("SHIPMENT DETAILS", 0.99), PageLine("SEKU", 0.62)]))
    corrected, _r, diag = correct_document(pages, known=known_tokens(["SEKU"]))
    guards = [g for _p, g, _b, _a in diag["protected"]]
    print(f"  p99 -> {corrected[99][1]!r}   protection guards={guards}")
    return corrected[99][1] == "SEKU" and "known" in guards


def test_load_error_patterns_skips_comments():
    """The error file is a plain list, tolerant of comments."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    handle.write("# ports\nKOO\n\n  /KOJ/  \n")
    handle.close()
    patterns = load_error_patterns(handle.name)
    os.unlink(handle.name)
    print(f"  loaded {patterns}")
    return patterns == ["KOO", "/KOJ/"]


def test_inline_comments_are_stripped_but_hashes_survive():
    """A trailing '# note' is a comment; the '#' in 'Fax#' is part of the term."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    handle.write("DAIAN   # -> DALIAN\nFax#\n")
    handle.close()
    patterns = load_error_patterns(handle.name)
    os.unlink(handle.name)
    print(f"  loaded {patterns}")
    return patterns == ["DAIAN", "Fax#"]


def test_corrected_error_makes_one_clear_row():
    """A corrected error pattern yields a single errorfix row showing the fix."""
    corrected, report, diag = correct_document(_routes(), error_patterns=["KOO"],
                                               vocabulary=VOCAB)
    rows = [r for r in build_rows("d.pdf", _routes(), report, diag)
            if r["status"] in ("error", "errorfix")]
    errorfix = [r for r in rows if r["status"] == "errorfix"]
    print(f"  rows={[(r['status'], r['original_text'], r['tool_text']) for r in rows]}")
    return (len(errorfix) == 1
            and errorfix[0]["original_text"] != errorfix[0]["tool_text"]
            and "SEA/KOD/TAO" in errorfix[0]["tool_text"])


def test_uncorrectable_error_says_so():
    """An ambiguous match is a single error row whose tool_text is unchanged."""
    vocab = known_entries(KNOWN + ["KOB"])
    corrected, report, diag = correct_document(_routes(), error_patterns=["KOO"],
                                               vocabulary=vocab)
    rows = [r for r in build_rows("d.pdf", _routes(), report, diag)
            if r["status"] in ("error", "errorfix")]
    print(f"  rows={[(r['status'], r['notes']) for r in rows]}")
    return (len(rows) == 1 and rows[0]["status"] == "error"
            and rows[0]["original_text"] == rows[0]["tool_text"]
            and "could not be corrected" in rows[0]["notes"])


def main():
    tests = [
        test_correction_comes_from_the_known_vocabulary,
        test_ambiguity_is_recorded_not_guessed,
        test_encounter_is_recorded_even_when_nothing_changes,
        test_bare_token_matches_only_on_boundaries,
        test_replacement_respects_token_boundaries,
        test_multi_word_pattern_is_refused,
        test_internal_punctuation_is_part_of_the_term,
        test_outer_delimiters_are_stripped_inner_kept,
        test_digits_are_never_altered,
        test_resolve_refuses_a_distant_match,
        test_contradiction_between_the_two_files,
        test_known_pattern_outranks_slot_voting_and_records_the_save,
        test_load_error_patterns_skips_comments,
        test_inline_comments_are_stripped_but_hashes_survive,
        test_corrected_error_makes_one_clear_row,
        test_uncorrectable_error_says_so,
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
