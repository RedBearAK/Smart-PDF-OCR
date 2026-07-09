"""Compiled regex globals for smart_pdf_ocr.

smart_pdf_ocr/patterns/field_rgx.py

Every regex used anywhere in the package lives here as a module-level compiled
global with a ``_rgx`` suffix. Patterns are never written inline inside larger
modules, where an unterminated string literal could corrupt the file during an
edit. Import these names; do not re-declare patterns elsewhere.
"""

import re


# Format-cluster signatures: which layout family a page belongs to.
FORMAT_A_rgx = re.compile(r"SHIPMENT DETAILS|BREAKDOWN OF CHARGES")
FORMAT_B_rgx = re.compile(r"Our Ref No|On Board Date|Bill To")

# Tokenisation: whole tokens, and "anchor" tokens that survive corruption.
TOKEN_rgx = re.compile(r"\S+")
ANCHOR_TOKEN_rgx = re.compile(r"[A-Za-z0-9]{3,}")

# Alphabetic tokens worth asking a dictionary about. Digits and short fragments
# carry no lexical signal.
WORD_TOKEN_rgx = re.compile(r"[A-Za-z]{3,}")

# Whitespace, for deciding whether a proposed edit changes anything real.
WHITESPACE_rgx = re.compile(r"\s+")

# Any character outside plain ASCII: a diacritic or stray symbol the scanner
# invented, or -- occasionally -- a character the document really contains.
NON_ASCII_rgx = re.compile(r"[^\x00-\x7F]")

# Variable numeric fields that must never be auto-corrected (amounts, counts,
# dates, invoice numbers). Presence of any of these vetoes a lexical repair.
NUMERIC_FIELD_rgx = re.compile(r"\$\s?\d|\d[\d,]*\.\d{2}\b|\bNo[:.]?\s*\d")

# Any digit at all, for asking whether a fragment carries a figure.
DIGIT_rgx = re.compile(r"\d")

# The alphanumeric core of a fragment, with its delimiters stripped away.
CORE_rgx = re.compile(r"[A-Za-z0-9]+")

# An identifier, in any document: a run of five or more digits. Such a line is
# data, not prose, and word-level repair has no business rewriting it. This says
# nothing about what the identifier means.
#
# A token merely mixing letters and digits is NOT enough. `Va1dez` is a misread of
# `Valdez`, not an identifier, and a guard that swallowed it would protect the very
# errors the lexicon exists to repair.
IDENTIFIER_rgx = re.compile(r"\d{5,}")

# Dates legitimately differ between pages, so they are never flagged as errors.
DATE_rgx = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")


# End of file #
