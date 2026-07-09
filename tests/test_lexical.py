"""Tests for lexical plausibility as a vote tiebreaker.

tests/test_lexical.py

Repetition is the primary signal and must never be overturned by a dictionary.
Lexical evidence only speaks when the counts are close -- which is exactly the
situation a short document produces, and exactly where a garbled label can
otherwise be elected canonical and frozen into a profile.

These tests supply their own names list, so they behave identically whether or
not the optional ``wordfreq`` backend is installed.
"""

import os
import tempfile

from smart_pdf_ocr.correct.lexical import choose, known_tokens, load_patterns, make_plausibility


NAMES = ["fax", "amount", "GalaxSea"]


def test_near_tie_is_broken_by_plausibility():
    """Two clean pages against two garbled ones: the real word wins."""
    tally = {"Fax#": 2, "#ax#": 2}
    plain = choose(tally, None)
    lexical = choose(tally, make_plausibility(NAMES))
    print(f"  no tiebreaker -> {plain}   with tiebreaker -> {lexical}")
    return plain[0] == "#ax#" and lexical[0] == "Fax#"


def test_garbage_ahead_within_tie_factor_is_overturned():
    """A 3:2 lead is close enough for lexical evidence to matter."""
    tally = {"#ax#": 3, "Fax#": 2}
    picked = choose(tally, make_plausibility(NAMES))
    print(f"  3:2 against the real word -> {picked}")
    return picked[0] == "Fax#"


def test_lopsided_vote_is_never_overturned():
    """Forty pages beat a dictionary, however implausible they look."""
    tally = {"aount": 40, "amount": 1}
    picked = choose(tally, make_plausibility(NAMES))
    print(f"  40:1 for a non-word -> {picked}")
    return picked[0] == "aount"


def test_choice_without_a_backend_is_deterministic():
    """With no lexical source, ties fall back to a stable text ordering."""
    first = choose({"Fax#": 2, "#ax#": 2}, None)
    second = choose({"#ax#": 2, "Fax#": 2}, None)
    print(f"  {first} == {second}")
    return first == second


def test_names_cover_vendor_coinage():
    """wordfreq cannot know 'GalaxSea'; the names list supplies it."""
    plausible = make_plausibility(NAMES)
    score = plausible("GalaxSea")
    print(f"  plausibility('GalaxSea') with names = {score:.2f}")
    return score == 1.0


def test_known_patterns_outrank_wordfreq():
    """A term the operator vouched for scores higher than a merely-known word."""
    vouched = make_plausibility(["Guempel"])
    print(f"  plausibility('Guempel') with the list = {vouched('Guempel'):.2f}")
    return vouched("Guempel") == 1.0


def test_phrases_contribute_each_word():
    """A multi-word entry vouches for its words individually."""
    tokens = known_tokens(["HO CHI MINH", "GalaxSea Freight"])
    print(f"  tokens = {sorted(tokens)}")
    return {"chi", "minh", "galaxsea", "freight"} <= tokens


def test_load_patterns_skips_blanks_and_comments():
    """A known-patterns file is a plain list, tolerant of comments."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    handle.write("# vendors\nGalaxSea\n\n  Chelan  \n")
    handle.close()
    names = load_patterns(handle.name)
    os.unlink(handle.name)
    print(f"  loaded {names}")
    return names == ["GalaxSea", "Chelan"]


def main():
    tests = [
        test_near_tie_is_broken_by_plausibility,
        test_garbage_ahead_within_tie_factor_is_overturned,
        test_lopsided_vote_is_never_overturned,
        test_choice_without_a_backend_is_deterministic,
        test_names_cover_vendor_coinage,
        test_known_patterns_outrank_wordfreq,
        test_phrases_contribute_each_word,
        test_load_patterns_skips_blanks_and_comments,
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
