# smart-pdf-ocr (beta 0.1.0b1)

A cross-page OCR **correction layer** behind a pluggable recognition front-end.
It does not try to be an OCR engine; it takes whatever a strong recognizer
returns and fixes the residue using the fact that these documents are extremely
repetitive — the correct spelling of almost anything is already present,
correctly, on other pages.

## Why this exists

The target corpus is scanned freight invoices delivered as image-only page
bundles (a ZIP of JPEGs plus a manifest, sometimes carrying a `.pdf`
extension). There is no text layer, so every character comes from OCR, and
classic engines (Tesseract / Acrobat) make the errors that break downstream
pattern matching — e.g. `UL6334564` read as `UL6s34564`, an ABA number
shredded into `E2508 4Ce`, a date `7/20/2026` collapsed to `712012026`.

## What the measurements said (84-page sample)

- **Recognizer:** PP-OCRv6 (via the `rapidocr` package) is the primary. On the
  sample it scored ~100% on structured fields with spacing preserved; mean line
  confidence 0.992. It replaces Tesseract/Acrobat outright rather than voting
  against them.
- **Failure set:** only 24 of 5,077 lines fell below 0.90 confidence, every one
  an invariant-boilerplate or recurring-proper-noun case (footer, `Fax#`,
  `SHIPPING WORLDWIDE`, `GalaxSea Freight Forwarding`).
- **Recovery:** every failure had abundant clean sibling reads elsewhere. The
  correction layer fixed 24/24 genuine errors with **zero false positives** and
  **zero numeric fields altered** (the two lines it left alone, `C/O` and
  `84513`, were already correct).

## How it works

```
document (pdf / image-bundle / image)
  -> ingest.container   sniff magic bytes, unpack, enumerate page images
  -> recognize          PP-OCRv6 primary backend (pluggable, runtime-optional)
  -> correct.pipeline   confidence-gated correction:
        cluster.py      group pages by layout family (A / B)
        consensus.py    position-slot + prefix-anchor boilerplate voting
        lexicon.py      self-built corpus lexicon + fuzzy token repair
  -> clean text dump (+ change report)
```

### Invariant figures

Not every figure varies. `(include an additional $35 wire fee...)` carries the
same `$35` on every page; refusing to repair it merely leaves garbage in the
output. `Forwarding Fee $90.00` carries an amount that differs by page, and
repairing it would restate money.

The strings decide which is which. An invariant figure lies inside the stretch a
garbled line and its canonical already agree on; a variable one lies beyond it,
in the part a rewrite would supply. So a figure-bearing line is rewritten only
when it agrees with its canonical for at least 20 characters **and** the
canonical's remaining tail holds no digits at all.

Only lines below the confidence gate are ever touched. Boilerplate voting runs
first (position for footers/headers, clean-prefix for body lines); the corpus
lexicon is the fallback for corrupted tokens in variable lines. Any line
carrying a numeric field is never rewritten, so amounts and dates are safe. An
edit that changes only whitespace is discarded -- it is not a correction.

## The support rule

A garbled line is rewritten only when at least **3 other pages of the same
format** carry a read of that line, or of that page position, at **0.95
confidence or better**. This is a count of clean siblings, not of pages:

- a 4-page document corrects fine when 3 pages are clean;
- a 50-page document corrects nothing if the line is garbled on all but two.

Because a garbled page needs 3 others to outvote it, a format cluster smaller
than 4 pages cannot correct anything. The tool prints its support summary on
every run and lists each low-confidence line it left alone, with the reason,
rather than reporting corrections it did not make.

## Resolution: why the default is `auto`

DPI is not a quality dial. A scanned page holds exactly as much detail as the image
embedded inside it, and `scale = dpi / 72` decides only how that detail is resampled
on the way out. There are three regimes, and only one of them is safe:

- **below native** -- pixels the scan contains are destroyed, permanently
- **at native** -- lossless, fastest, deterministic
- **above native** -- adds nothing; only the interpolation and compression artefacts
  change, and the recognizer's response to those is a lottery, not a gradient

That last point is not a figure of speech. On one page, rendering at 300 dpi read a
shredded footer perfectly -- but only when saved as JPEG q95. The same pixels saved
losslessly failed. The 300 dpi "win" was JPEG's blur, not resolution: an explicit
Gaussian blur of radius 0.4-0.8 reproduces it exactly, radius 1.2 destroys it, and
the same blur that rescued that footer broke a logo tagline two pages later.

So the default is `auto`: read the scan's own resolution and render at it.

```
smart-pdf-ocr doc.pdf              # auto: render at the scan's native resolution
smart-pdf-ocr doc.pdf --dpi 300    # allowed, and warned about in both directions
```

The tool prints what it found and warns when a requested DPI is below native (detail
discarded) or above it (no detail gained). Cost is not the reason to stay low:
recognition time is nearly flat in page pixels, because detection downsizes
internally and recognition scales with the number of text lines, not megapixels --
9.6 s at 3.7 Mpx against 9.8 s at 14.9 Mpx.

## Parallel recognition: sized to the machine, and it says so

Pages are independent, and the correction layer needs the whole document at once
either way, so recognition parallelizes cleanly. By default the tool sizes a
worker pool from the machine it is on and announces the arithmetic:

```
workers: 4 (8 cores -> 4, 9.6GB free -> 10 @ 700MB/worker, 82 pages)
loading recognizer in 4 workers (onnxruntime + models)...
```

Three bounds, smallest wins, and the line says which one did. The core bound
pairs each worker with two engine threads -- the engine's own threading already
spreads one page across cores, and workers multiply that. The memory bound
divides reclaimable RAM (on macOS: memory_pressure's own free percentage, with
the vm_stat free + purgeable + file-backed pool as fallback -- unified-memory
machines keep their real headroom in caches) by the measured cost of a worker,
about 0.7 GB at ordinary resolutions, 0.9 GB above 300 dpi, after a headroom
floor is set aside. The page count caps the rest. When the memory probe fails,
the pool retreats to two workers and says so rather than guessing.

```
smart-pdf-ocr doc.pdf                # auto-sized pool (the default)
smart-pdf-ocr doc.pdf --workers 6    # pinned; bounded only by the page count
smart-pdf-ocr doc.pdf --workers 1    # the sequential path, exactly as before
```

Workers are spawned processes, each with its own engine: a crash is isolated and
aborts the run naming its page, and results always reassemble in page order
before correction sees them, so nothing downstream changes at all. When the
pool runs, rasterization streams: pages render in a parent-side thread just
ahead of the workers consuming them, so the whole render phase hides behind
recognition (the timing line marks it "overlapped") and the saving grows with
the page count. A render failure mid-stream aborts naming how far it got. `--workers 1`
takes the original sequential code path untouched. Either way the multi-second
model load now announces itself instead of looking like a hang, and every run
ends with a timing line -- rasterize, recognize (with a per-page rate), correct,
total -- so machines and worker counts compare on numbers instead of feel.

## Page markers in the text output

Each page in the corrected text dump is headed by a marker line:

```
=== page 1 ===
GalaxSea Freight Forwarding
...


=== page 2 ===
...
```

The first page's marker leads the file; every later page is preceded by two blank
lines. This lets a reader -- or a downstream tool -- tell which page any line came
from. The markers contain a space and the word "page", so they do not collide with
the place-of-receipt (`City, ST`) or POL/POD (`XXX/YYY`) extraction patterns.

## Searchable PDF output

By default the tool emits corrected text. `--pdf-out` additionally writes a
*searchable* PDF: the original scanned page, untouched, under an invisible layer of
the corrected text, each line placed at the box the recognizer found it in.

```
smart-pdf-ocr invoices.pdf -o corrected.txt --pdf-out searchable.pdf
```

Select a line in a viewer and the selection lands on the matching pixels; search
for a word and it is found -- including where the correction layer turned a misread
`WORLOWIDE` into the `WORLDWIDE` the page plainly shows. What a human sees is the
untouched scan; what a machine reads is the best text the whole pipeline produced.
The two occasionally disagree, on exactly the lines consensus repaired, and that is
the point of building the layer from corrected text rather than a raw second OCR
pass.

The layer is *appended to the original PDF* -- the scanned images are copied
untouched, never re-encoded -- so the file barely grows: a 2.7 MB scan gains a few
kilobytes of text and stays 2.7 MB. Needs `pikepdf`
(`pip install 'smart-pdf-ocr[searchable-pdf]'`) and a PDF input, since there must be
an original PDF to append to; it does not apply to image bundles or `--cached` runs.
Lines the recognizer gave no box are left out rather than piled at the origin.

## Invoking it

The document may be given positionally or with `-i/--input`:

```
smart-pdf-ocr invoices.pdf -o corrected.txt
smart-pdf-ocr -o corrected.txt --known-patterns known.txt -i invoices.pdf
```

The second form keeps every option ahead of the filename, so a command can be saved
with its flags and the input edited or appended per run.

## Recognizer log output

The OCR engine prints a burst of INFO lines when it loads -- which runtime and model
files it found -- and repeats some per page. Useful once, on a fresh install; noise
after, where it interleaves with this tool's own output. It is filtered out by
default, leaving genuine warnings and errors. To see all of it (for troubleshooting
a model or runtime problem), set `SMART_PDF_OCR_OCR_LOG=1`.

## Failing early

Four of the paths a run depends on are not touched until the recognizer has
finished: `-o`, `--review-out`, `--learn-profile`, and `--review-in`. On an
84-page scan that is a quarter of an hour of work, and a mistyped directory would
throw all of it away at the last moment.

So every path is checked before a single page is read, every problem is reported at
once, and a missing file exits 2 with a message rather than a traceback:

```
$ smart-pdf-ocr doc.pdf -o out.txt --known-patterns known_patterns.txt
error: --known-patterns: known_patterns.txt does not exist
```

Usage problems exit 2. Runtime failures exit 1 and name the file they came from.
An interrupt exits 130.

## Install

```
pip install -e .            # pulls rapidocr (PP-OCRv6 models) + pypdfium2 + pillow
```

No system packages are required. Real PDFs are rasterized with `pypdfium2`, a
pip wheel. If poppler's `pdftoppm` happens to be installed it is accepted as a
fallback, but nothing needs it. With neither present the tool reports what to
install for macOS and Linux instead of raising `FileNotFoundError`.

Measured on this corpus, `pypdfium2` matched poppler line-for-line and scored
marginally higher mean OCR confidence (0.9883 vs 0.9877) at the same speed.

## Use

```
# full pipeline on a document
smart-pdf-ocr invoices.pdf -o corrected.txt --report

# run the correction layer on a saved OCR dump (skips recognition)
smart-pdf-ocr --cached ocr_dump.json -o corrected.txt --report
```

The saved-OCR dump is `{ "1": {"txts": [...], "scores": [...]}, ... }`.

## Human review round trip

No correction layer reaches 100%. The tool emits every line it changed or
declined to change, as a spreadsheet you can edit and hand straight back:

```
smart-pdf-ocr invoices.pdf --review-out review.tsv -o corrected.txt
# open review.tsv, fill in the corrected_text column where the tool got it wrong
smart-pdf-ocr invoices.pdf --review-in review.tsv -o corrected.txt --report
```

Blank `corrected_text` accepts what the tool did. Filled, it forces your text.
`scope` is `page` by default; set it to `all` to fix every identical original.
Rules are keyed by page plus a hash of the raw OCR text, never by line index --
re-rasterizing at a different DPI reorders lines, so an index-keyed rule would
rewrite the wrong one. When the recognizer's output moves, the key stops
matching and the rule is reported **stale** rather than misapplied. A human edit
outranks every internal veto, is logged as `human` in the report, and re-running
with the same file changes nothing further.

You can also add rows by hand for lines the recognizer was confident about but
still got wrong: give the page, the `original_text`, and your correction.

## Confident disagreements: the failures that lie

The confidence gate only catches failures that admit to being failures. A
recognizer can read `SHIPPING WOALDWIDE` at 0.96, or hallucinate an umlaut into
`Chelan`, and the gate waves it through. Measured on a real 84-page document,
eleven such character errors and eight spacing slips reached the output with no
low-confidence signal at all -- including `ABA#'325081403` and `Sitka. AK 99835`.

What betrays them is rarity, not confidence: the line sits where a canonical
sits, is one or two characters from it, and forty sibling pages disagree. Those
lines are surfaced in the review file as `status=disagrees` (character error) or
`status=spacing`. The similarity floor defaults to 0.90 (`--disagree-ratio`);
lowering it to 0.86 adds only arguable flags like `QINGDAO, CN` against
`QINGDAO, CHINA`. They are **never rewritten** -- high confidence deserves that
much -- only shown to a reviewer. Dates and figures legitimately differ between
pages and are never flagged.

## ASCII normalization

A scan of an English freight invoice does not contain `Chelän` or `Prépaid`. The
accent is a recognizer artefact. Folding it away is nearly always right -- but a
document that genuinely reads `Zürich` on forty pages would be silently ruined by
a blind fold, and no flag would ever tell you.

So the corpus decides. A folded spelling replaces the original only when other
pages already attest to the plain spelling. Two candidates are tried: drop the
combining marks (`Chelän` -> `Chelan`), then drop any non-ASCII that survives,
which handles stray symbols with no decomposition (`·Net 15` -> `Net 15`).
Non-ASCII that the corpus cannot adjudicate is surfaced as a `status=nonascii`
review row instead of being changed.

Measured on a real 84-page document: four artefacts folded, nothing else touched.
On a synthetic corpus that really says `Zürich` on every page: zero changes --
while `--force-ascii` rewrites all forty.

```
smart-pdf-ocr doc.pdf                     # evidence-gated folding (default)
smart-pdf-ocr doc.pdf --keep-diacritics   # never fold
smart-pdf-ocr doc.pdf --force-ascii       # fold without evidence (dangerous)
```

## Lexical tiebreaker (optional)

Repetition decides which spelling is canonical, and a lopsided vote is never
overturned -- forty pages reading `aount` would beat any dictionary. But a short
document can split three to two, and the majority can be wrong: in a real corpus
the `Fax#` label was shredded on 19 of 43 pages, so a five-page slice elects
`#ax#` as canonical and freezes it into a profile.

Where the counts are within a factor of 1.5, the tool asks whether the string is
made of real words. `Fax` is; `#ax#` is not. Nothing else changes: on the real
84-page document, enabling the tiebreaker produced identical output and an
identical learned profile.

```
smart-pdf-ocr doc.pdf --known-patterns known_patterns.txt
```

`wordfreq` is a core dependency -- accuracy is the point of the tool, and the
tiebreaker is part of being accurate -- and scores ordinary vocabulary and common
place names alike (`chelan`, `qingdao`, `sitka`). Coined terms -- `GalaxSea`,
`FFWDR`, container prefixes -- are unknown to it, and that is what the
known-patterns file is for. It is always consulted, but only ever to break a close
vote; a lopsided count is never overturned.

## The known-patterns file

One term per line; blanks and `#` comments ignored; case-insensitive. Phrases are
allowed and vouch for each of their words. It does two things:

1. **Outranks wordfreq when a close vote needs breaking.** A term you vouched for
   scores 1.0; a term wordfreq merely recognizes scores 0.6; anything else, 0.0.
2. **Protects its tokens from lexicon repair, absolutely.** They are never
   rewritten, however rare they are and however close a frequent lookalike sits.

That second job matters more than the first. `SEKU` and `SEGU` are both real
container owner prefixes, one character apart. A `SEKU` that never once read
cleanly would be pulled into `SEGU` by frequency alone -- exactly the corruption
this tool exists to prevent.

Lines carrying an identifier -- a run of five or more digits, or a token mixing
letters and digits -- are protected structurally, with no file required. That test
is document-agnostic: it recognizes a shape, not a meaning. But a code whose
alphabetic part stands alone (`SEKU`, or a three-letter port code) looks exactly
like a word, and no structural test can save it. Those belong in the
known-patterns file. Stage every carrier prefix and port code you expect, and the
tool stays ignorant of what kind of document it is reading.

## Error patterns: what can never be right

Consensus can only judge what it can vote on. A three-letter port code inside a
routing line that differs on every page has no siblings to outvote it, and at three
characters plain edit distance carries no signal: `KOO` sits exactly as close to
`KOD` as to `KOB` or `KOZ`. Nothing the corpus knows can settle it.

So the operator declares it. An error pattern is a deterministic trigger -- no vote,
no confidence gate, no similarity threshold. It is **not** a replacement: the
correction is drawn from the known-patterns vocabulary, as the nearest member, and
only when the nearest member is unambiguous.

```
smart-pdf-ocr doc.pdf --known-patterns known.txt --error-patterns errors.txt
```

```
# errors.txt -- strings that can never be correct
DAIAN        # bare token: matches only on boundaries, never inside DAIANX
/KOJ/        # contains delimiters: matches literally
```

Each entry names exactly **one** term. Whitespace between letters is what makes two:
`Wire information:` is refused at load, because a rule that silently corrected only
the first word would be worse than no rule. Punctuation *inside* a term is not a
boundary but part of the error -- `WORL.WIDE` is one term, one edit from `WORLDWIDE`,
and correcting it is the whole point. Outer delimiters are stripped (`/KOJ/` resolves
`KOJ`); inner ones are kept. Trailing `# comments` are stripped, but a `#` that
belongs to the term (`Fax#`, `ABA#`) is kept.

Adding a term to `known-patterns` alone never corrects anything -- it protects the
term and supplies the vocabulary. The pair is what acts: `DAIAN` in the error file,
`DALIAN` in the known file. Case-only and punctuation-only errors (`invoice` for
`Invoice`, `Sitka.` for `Sitka`) belong in the review file instead, where a human
judges one instance at a time.

- Nearest known pattern within 1 edit (2 for terms over 6 characters) → corrected.
- Two known patterns equally close → **recorded, not guessed**.
- Nothing close enough → **recorded, unchanged**.
- A correction that would alter a digit → refused.
- A string in *both* files is a contradiction, and the run stops. No heuristics,
  no counting of pages.

Every encounter reaches the review file, corrected or not, so a rule that fires
always leaves a trace.

**The limitation, stated plainly.** If a real code is missing from your known
patterns and you declare a variant of it an error, it will be "corrected" to the
nearest listed member. An incomplete controlled vocabulary is a wrong one. The
blast radius is bounded to exactly what you declared wrong.

## Protection records

Known patterns and structural guards do their most important work by *refusing*.
Voting beats a bad read, but never a term you vouched for: a line made entirely of
known patterns survives however loudly the slot's canonical disagrees, because
`SEKU` and `SEGU` are both real and the corpus cannot tell a rare one from a misread
of the common one.

Those refusals are recorded as `status=protected` rows -- but only when the guard
actually prevented a change. A guard that skipped a line nothing would have touched
has protected nothing, and saying so would bury the cases that matter. On a clean
84-page document that yields zero rows; it yields rows exactly when something was
about to go wrong.

## Typography: the harmless third of the findings

A high-confidence line whose alphanumeric payload matches its canonical *exactly*
differs only in presentation -- the spacing, the punctuation, or the case:

```
'Sitka. AK 99835'            -> 'Sitka, AK 99835'
'Account Number:3592668683'  -> 'Account Number: 3592668683'
"ABA#'325081403"             -> 'ABA# 325081403'
'invoice Terms'              -> 'Invoice Terms'
```

The test is the **payload**: every letter and digit, in order, separators stripped.
Two lines with the same payload hold the same information and differ only in how it
is presented. That includes a full stop dropped *between* two letters:

```
'SHIPPING WORL.DWIDE'  payload SHIPPINGWORLDWIDE  ==  canonical  -> corrected
'SHIPPING WORL.WIDE'   payload SHIPPINGWORLWIDE   !=  canonical  -> flagged
```

The first still spells WORLDWIDE. The second is short a `D` -- the scanner put a
period *in place of* a letter, and no amount of reformatting recovers it. Adopting
the canonical cannot lose a character in the first case, so it is a correction,
reported as `typography`. The second stays flagged, or is declared wrong outright in
the error-patterns file.

On a real 400 dpi document this resolved 7 of 16 disagreements with zero letters
and zero digits altered. `--keep-typography` turns them back into findings.

## Vendor profiles: corrections that compound

Cross-page consensus needs a corpus, so a short document corrects nothing. A
profile is that corpus frozen -- the canonical text of each template slot and
standing line -- learned once and reused forever:

```
smart-pdf-ocr big_batch.pdf --learn-profile galaxsea.json    # learn from 84 pages
smart-pdf-ocr one_invoice.pdf --profile galaxsea.json        # correct a 1-page file
```

Measured: a 3-page extract corrected nothing on its own ("NO CONSENSUS"), and
recovered its shredded footer with the profile applied. Because these invoices
arrive monthly on a stable template, a canonical confirmed once keeps paying.

A profile must never learn an error. A recognizer that misreads the same word the
same way on three pages manufactures a counterfeit canonical, and freezing it would
carry the mistake into every future document. On a real 400 dpi run, `SHIPPING
WORLOWIDE` appeared three times at high confidence -- enough to clear the support
threshold -- while the disagreement detector was flagging it as wrong in the same
run. A rare spelling contradicted by a far more frequent sibling is now discarded
before anything is written down, in the profile and in the within-document line
index alike.

Profiles never carry a variable figure. A currency amount, a decimal, or an
invoice number is excluded from every canonical, and a line holding one is never
rewritten by boilerplate voting -- two pages may legitimately read
`Forwarding Fee $90.00` and `Forwarding Fee $105.00`, and a shared prefix long
enough to anchor one onto the other would silently restate the amount.

## Known gaps (beta)

- Only the `rapidocr` backend is wired live. Tesseract / docTR belong as
  independent-architecture *validators* (disagreement flags), not co-voters,
  and are not yet implemented.
- Short documents get no cross-page correction, by design. See the support rule
  above; the tool says so rather than pretending.
- A profile asserts invariant lines that carry digits (an ABA or account number
  is constant for a vendor, and restoring a mangled one is the point). If a
  vendor's account number ever changes, re-learn the profile: a stale profile
  would assert the old number onto a low-confidence line.
- Format clustering uses signature tokens for two known templates; a new
  template lands in the `?` cluster and receives no boilerplate canonicals
  until a signature is added.
- Lexicon repair is conservative on heavily-corrupted tokens (edit-distance
  floor), by design, to avoid false corrections.

## Conventions

Regex lives only in `patterns/field_rgx.py` as `_rgx` globals. No `typing`
module. Tests are runnable under `pytest` but self-scoring: each returns True or
False and `main()` accumulates a score.

`conftest.py` makes pytest honour that. Without it pytest calls a test, discards
the boolean, sees no exception and reports a pass -- so a broken test is green under
pytest and red only when run by hand. The hook inspects the returned value and
fails on a falsy one, with no assertion in any test module.
