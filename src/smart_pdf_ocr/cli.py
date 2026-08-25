"""Command-line entry point for smart_pdf_ocr.

smart_pdf_ocr/cli.py

Runs the whole pipeline: ingest a document (real PDF, image bundle, or bare
image), recognize every page with the primary backend, apply the cross-page
correction layer, and write a clean text dump. ``--cached`` skips recognition and
feeds a saved OCR dump straight into the correction layer, which is how the
correction stage is exercised without re-running the engine.

The support summary always goes to stderr, so a document that cannot support
cross-page consensus says so rather than quietly correcting nothing.

Every path the run depends on is checked before any page is recognized. Four of
them -- the corrected text, the review file, the learned profile, and the review
file being applied -- are otherwise not touched until the recognizer has finished,
so a mistyped directory would be discovered only after a quarter of an hour of
work had been thrown away. A missing file is a usage error, not a traceback.
"""

import os
import sys
import time
import shutil
import argparse

from smart_pdf_ocr.review.report import write_review
from smart_pdf_ocr.recognize import rapidocr_backend
from smart_pdf_ocr.review.overlay import load_rules, apply_rules
from smart_pdf_ocr.correct.disagree import DISAGREE_RATIO
from smart_pdf_ocr.correct.pipeline import correct_document, assemble_text
from smart_pdf_ocr.correct.normalize import MODE_EVIDENCE, MODE_FORCE, MODE_OFF
from smart_pdf_ocr.recognize.backend import load_cached_ocr
from smart_pdf_ocr.recognize.parallel import size_pool, recognize_parallel, recognize_streaming
from smart_pdf_ocr.ingest.container import (
    sniff,
    page_count,
    resolve_dpi,
    make_workdir,
    enumerate_pages,
    available_renderer,
    iter_rendered_pages,
)
from smart_pdf_ocr.review.profile import counts_of, load_profile, save_profile, learn_profile
from smart_pdf_ocr.correct.errors import contradictions, malformed, load_error_patterns
from smart_pdf_ocr.correct.lexical import known_entries, known_tokens, load_patterns, make_plausibility


def _readable(path):
    if not os.path.exists(path):
        return "does not exist"
    if os.path.isdir(path):
        return "is a directory, not a file"
    if not os.access(path, os.R_OK):
        return "is not readable"
    return ""


def _writable(path):
    if os.path.isdir(path):
        return "is a directory, not a file"
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        return "directory " + parent + " does not exist"
    if not os.access(parent, os.W_OK):
        return "directory " + parent + " is not writable"
    if os.path.exists(path) and not os.access(path, os.W_OK):
        return "is not writable"
    return ""


def _same_file(a, b):
    """True when two paths point at the same file, allowing for ./ and symlinks."""
    if not a or not b:
        return False
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.abspath(a) == os.path.abspath(b)


def _path_problems(args):
    """Every unusable path, found before a single page is recognized."""
    problems = []
    for label, path in (("input document", args.input),
                        ("--cached", args.cached),
                        ("--known-patterns", args.known_patterns),
                        ("--error-patterns", args.error_patterns),
                        ("--review-in", args.review_in),
                        ("--profile", args.profile)):
        if not path:
            continue
        why = _readable(path)
        if why:
            problems.append("{0}: {1} {2}".format(label, path, why))

    outputs = (("--output", args.output),
               ("--review-out", args.review_out),
               ("--learn-profile", args.learn_profile),
               ("--pdf-out", args.pdf_out))
    source = args.input or args.cached
    seen = {}
    for label, path in outputs:
        if not path:
            continue
        why = _writable(path)
        if why:
            problems.append("{0}: {1} {2}".format(label, path, why))
            continue
        # Never write over the file being read. This is irreversible and has no
        # legitimate use, so it is refused outright, with no --force.
        if _same_file(path, source):
            problems.append("{0}: {1} is the input file; refusing to overwrite it"
                            .format(label, path))
            continue
        # Two outputs aimed at one path would have the last silently win.
        if path in seen:
            problems.append("{0} and {1} both write to {2}"
                            .format(seen[path], label, path))
            continue
        seen[path] = label
        # An existing output is overwritten only with --force -- except the
        # declared profile round-trip: a --learn-profile that is also the
        # loaded --profile is being updated on purpose, like a review file
        # flowing back in through --review-in.
        if os.path.exists(path) and not args.force:
            if (label == "--learn-profile" and args.profile
                    and _same_file(path, args.profile)):
                continue
            problems.append("{0}: {1} already exists; pass --force to overwrite"
                            .format(label, path))
    return problems


def _progress(done, total, quiet, label="recognizing page"):
    """A multi-minute run should not look like a hang.

    The label is the whole phrase before the counter. Sequential phases read
    naturally as "recognizing page 12/82" -- the run is genuinely on that page.
    The pool finishes pages in whatever order they complete, so its label is
    "pages recognized": an hourglass filling, not a cursor moving.
    """
    if quiet:
        return
    if sys.stderr.isatty():
        sys.stderr.write("\r  %s %d/%d" % (label, done, total))
        if done == total:
            sys.stderr.write("\n")
        sys.stderr.flush()
        return
    if done == total or done % 10 == 0:
        print("  %s %d/%d" % (label, done, total), file=sys.stderr)


def _recognize_all(image_paths, quiet=False):
    if not rapidocr_backend.is_available():
        raise RuntimeError("rapidocr backend unavailable; install the 'rapidocr' package")
    if not quiet:
        print("loading recognizer (onnxruntime + models)...", file=sys.stderr)
    total = len(image_paths)
    results = []
    for index, image_path in enumerate(image_paths):
        results.append(rapidocr_backend.recognize_page(image_path, index + 1))
        _progress(index + 1, total, quiet)
    return results


def _recognize_pool(image_paths, workers, quiet=False):
    if not rapidocr_backend.is_available():
        raise RuntimeError("rapidocr backend unavailable; install the 'rapidocr' package")
    if not quiet:
        print("loading recognizer in %d workers (onnxruntime + models)..." % workers,
              file=sys.stderr)

    def tick(done, total):
        _progress(done, total, quiet, label="pages recognized")

    return recognize_parallel(image_paths, workers, progress=tick)


def _load_pages_streaming(args, timing, total, workers):
    """Pooled recognition fed by render-as-needed rasterization.

    Rendering runs in a parent-side thread while the pool recognizes, so the
    whole rasterization phase hides behind recognition except the first page.
    Only the pooled pdfium path streams; every other path pre-renders as
    before, and --workers 1 keeps the untouched sequential reference.
    """
    if not rapidocr_backend.is_available():
        raise RuntimeError("rapidocr backend unavailable; install the 'rapidocr' package")
    if not args.quiet:
        print("loading recognizer in %d workers (onnxruntime + models)..." % workers,
              file=sys.stderr)

    def tick(done, count):
        _progress(done, count, args.quiet, label="pages recognized")

    workdir = make_workdir()
    try:
        mark = time.monotonic()
        tasks = iter_rendered_pages(args.input, workdir, args.dpi)
        pages, image_paths, render_seconds = recognize_streaming(
            tasks, total, workers, progress=tick)
        timing["rasterize"] = render_seconds
        timing["overlapped"] = True
        timing["recognize"] = time.monotonic() - mark
        return pages, workdir, tuple(image_paths)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def _load_pages(args, timing):
    if args.cached:
        return load_cached_ocr(args.cached), None, ()

    streaming = sniff(args.input) == "pdf" and available_renderer() == "pypdfium2"
    workers = None
    if streaming:
        total = page_count(args.input)
        dpi_hint = args.dpi if isinstance(args.dpi, int) else None
        workers, reason = size_pool(total, dpi_hint, pinned=args.workers)
        print("workers: {0} ({1})".format(workers, reason), file=sys.stderr)
        if workers > 1:
            return _load_pages_streaming(args, timing, total, workers)

    def raster_tick(done, count):
        _progress(done, count, args.quiet, label="rasterizing page")

    mark = time.monotonic()
    image_paths, workdir = enumerate_pages(args.input, dpi=args.dpi, progress=raster_tick)
    timing["rasterize"] = time.monotonic() - mark
    try:
        if workers is None:
            dpi_hint = args.dpi if isinstance(args.dpi, int) else None
            workers, reason = size_pool(len(image_paths), dpi_hint, pinned=args.workers)
            print("workers: {0} ({1})".format(workers, reason), file=sys.stderr)
        mark = time.monotonic()
        if workers > 1:
            pages = _recognize_pool(image_paths, workers, quiet=args.quiet)
        else:
            pages = _recognize_all(image_paths, quiet=args.quiet)
        timing["recognize"] = time.monotonic() - mark
        return pages, workdir, tuple(image_paths)
    except Exception:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
        raise


def _requested_dpi(parser, value):
    if value == "auto":
        return None
    if not value.isdigit() or int(value) <= 0:
        parser.error("--dpi must be a positive integer or 'auto'")
    return int(value)


def _requested_workers(parser, value):
    if value == "auto":
        return None
    if not value.isdigit() or int(value) <= 0:
        parser.error("--workers must be a positive integer or 'auto'")
    return int(value)


def _write_pdf(args, pages, corrected, image_paths):
    from PIL import Image
    from smart_pdf_ocr.ingest.container import sniff
    from smart_pdf_ocr.review.pdf_out import is_available, pages_from, write_searchable_pdf
    if not is_available():
        print("--pdf-out needs pikepdf (pip install 'smart-pdf-ocr[searchable-pdf]')",
              file=sys.stderr)
        return
    if not args.input or sniff(args.input) != "pdf":
        print("--pdf-out appends a text layer to a source PDF; the input must be a PDF",
              file=sys.stderr)
        return
    have_box = any(line.box for page in pages for line in page.lines)
    if not have_box:
        print("--pdf-out: recognizer returned no boxes; skipping", file=sys.stderr)
        return
    sizes = []
    for image_path in image_paths:
        with Image.open(image_path) as image:
            sizes.append(image.size)
    sheet = pages_from(pages, corrected)
    write_searchable_pdf(args.pdf_out, args.input, sheet, sizes)
    print("searchable PDF: {0} ({1} pages)".format(args.pdf_out, len(sheet)), file=sys.stderr)


def _print_timing(timing, page_count, started):
    """Where the minutes went, so machines and worker counts can be compared.

    The per-page recognition rate is the portable number: it holds its meaning
    across documents of different lengths, which the totals do not.
    """
    parts = []
    for phase in ("rasterize", "recognize", "correct"):
        seconds = timing.get(phase)
        if seconds is None:
            continue
        note = ""
        if phase == "rasterize" and timing.get("overlapped"):
            note = " overlapped"
        if phase == "recognize" and page_count:
            note = " (%.1fs/page)" % (seconds / page_count)
        parts.append("%s %.1fs%s" % (phase, seconds, note))
    parts.append("total %.1fs" % (time.monotonic() - started))
    print("timing: " + ", ".join(parts), file=sys.stderr)


def _print_support(diagnostics):
    needed = diagnostics["min_clean_siblings"]
    conf = diagnostics["clean_conf"]
    pages_needed = diagnostics["min_cluster_pages"]
    print("consensus rule: a line is only rewritten with {0} clean sibling reads "
          "at >= {1:.2f} confidence".format(needed, conf), file=sys.stderr)
    for key, pages, can_vote in diagnostics["clusters"]:
        if can_vote:
            state = "consensus active"
        else:
            state = "NO CONSENSUS - needs {0} pages, nothing will be corrected".format(pages_needed)
        label = "page" if pages == 1 else "pages"
        print("  format {0}: {1} {2} -> {3}".format(key, pages, label, state), file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="smart-pdf-ocr",
        description="Cross-page OCR correction behind a pluggable recognizer.",
    )
    parser.add_argument("input_pos", nargs="?", metavar="input",
                        help="document to process (pdf / bundle / image)")
    parser.add_argument("-i", "--input", dest="input_flag",
                        help="the same document, as a flag, so it may appear in any order")
    parser.add_argument("-o", "--output",
                        help="write corrected text here; '-' dumps to stdout "
                             "(default: <stem>_corrected.txt in the run folder)")
    parser.add_argument("--support-dir",
                        help="folder of standing support files to auto-load "
                             "(default: _smartocr_support beside the input)")
    parser.add_argument("--output-dir",
                        help="folder for defaulted outputs (default: a new "
                             "<stem>_smartocr_files_<HHMMSS> folder beside the input)")
    parser.add_argument("--cached", help="use a saved OCR JSON dump instead of recognizing")
    parser.add_argument("--dpi", default="auto",
                        help="rasterization DPI for real PDFs, or 'auto' for the scan's "
                             "native resolution (the default)")
    parser.add_argument("--gate", type=float, default=0.90, help="confidence gate for correction")
    parser.add_argument("--report", action="store_true", help="print the change report to stderr")
    parser.add_argument("--review-out", help="write a review file (.tsv/.csv) of every flagged line")
    parser.add_argument("--review-in", help="apply human corrections from a completed review file")
    parser.add_argument("--profile", help="load a vendor profile of canonicals (JSON)")
    parser.add_argument("--learn-profile", help="write a vendor profile learned from this run")
    parser.add_argument("--workers", default="auto",
                        help="parallel recognition workers: a count, 'auto' to size "
                             "from cores and free memory (the default), or 1 for the "
                             "sequential path")
    parser.add_argument("--quiet", action="store_true", help="suppress the per-page progress line")
    parser.add_argument("--keep-diacritics", action="store_true",
                        help="never fold non-ASCII characters to ASCII")
    parser.add_argument("--force-ascii", action="store_true",
                        help="fold non-ASCII even without corpus evidence")
    parser.add_argument("--keep-typography", action="store_true",
                        help="flag spacing, punctuation and case slips instead of fixing them")
    parser.add_argument("--pdf-out",
                        help="also write a searchable PDF: the scan under a corrected text layer")
    parser.add_argument("--known-patterns",
                        help="file of known words, names and places, one per line")
    parser.add_argument("--error-patterns",
                        help="file of strings that can never be correct, one per line")
    parser.add_argument("--disagree-ratio", type=float, default=DISAGREE_RATIO,
                        help="similarity floor for flagging confident disagreements")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing output files (never the input)")
    return parser


def main(argv=None):
    """Entry point. Usage problems exit 2; runtime failures exit 1."""
    try:
        return _run(argv)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1


def _resolve_default_outputs(args):
    """Fill unset output paths with stem-prefixed names in a per-run folder.

    The old default -- every artifact named generically, landing wherever the
    shell happened to be -- made runs collide with each other. Now a bare run
    creates <stem>_smartocr_files_<HHMMSS> BESIDE the input (the scan names
    already carry the date, so the folder carries only the time) holding
    <stem>_corrected.txt, <stem>_review.tsv and <stem>_smartocr.pdf. An
    explicit path for any artifact wins for that artifact and is never moved
    into the folder; --output-dir redirects the folder itself (created if
    needed, reusable); -o - keeps the stdout text dump. The searchable PDF is
    defaulted only when there is a real PDF input to sandwich and pikepdf is
    present -- otherwise it is skipped with a note, never an error.

    Returns (folder_created_or_None, problems, notes). The folder is created
    here so the preflight can validate the files inside it; if the preflight
    then fails, the caller removes the empty folder again.
    """
    from smart_pdf_ocr.review.pdf_out import is_available as pdf_possible

    problems = []
    notes = []
    source = args.input or args.cached
    if not source or not os.path.isfile(source):
        # A bad input path is the preflight's problem to name; defaulting
        # outputs beside it would only bury that message under this one.
        return None, problems, notes
    stdout_dump = args.output == "-"
    if stdout_dump:
        args.output = None
    want_text = args.output is None and not stdout_dump
    want_review = args.review_out is None
    sandwich_ok = (args.pdf_out is None and args.input and not args.cached
                   and sniff(args.input) == "pdf")
    want_pdf = sandwich_ok and pdf_possible()
    if sandwich_ok and not pdf_possible():
        notes.append("searchable PDF skipped: pikepdf not installed")

    if not (want_text or want_review or want_pdf):
        return None, problems, notes

    stem = os.path.splitext(os.path.basename(source))[0]
    created = None
    if args.output_dir:
        folder = args.output_dir
        os.makedirs(folder, exist_ok=True)
    else:
        beside = os.path.dirname(os.path.abspath(source))
        folder = os.path.join(
            beside, "{0}_smartocr_files_{1}".format(stem, time.strftime("%H%M%S")))
        if os.path.exists(folder):
            problems.append("run folder already exists: {0}".format(folder))
            return None, problems, notes
        os.makedirs(folder)
        created = folder
    if want_text:
        args.output = os.path.join(folder, stem + "_corrected.txt")
    if want_review:
        args.review_out = os.path.join(folder, stem + "_review.tsv")
    if want_pdf:
        args.pdf_out = os.path.join(folder, stem + "_smartocr.pdf")
    notes.append("outputs: " + folder)
    return created, problems, notes


def _resolve_support(args):
    """Auto-load standing support files from a folder beside the input.

    The working folder should hold nothing but the documents: the always-there
    files -- known patterns, error patterns, the vendor profile -- live in a
    _smartocr_support subfolder (leading underscore so it sorts above the
    PDFs), found beside the input like every other default, or wherever
    --support-dir points. An explicit flag wins per file, exactly as on the
    output side. What was loaded is announced; nothing is ever loaded
    silently.

    A lone .json in the folder is the vendor profile and becomes BOTH ends of
    the round-trip: loaded before the run and updated by learning after it,
    so it compounds across runs. Two or more .json files are refused by name
    rather than guessed between. A missing auto folder is simply an
    unconfigured folder; a missing --support-dir is an error.
    """
    problems = []
    notes = []
    source = args.input or args.cached
    if not source or not os.path.isfile(source):
        return problems, notes
    if args.support_dir:
        folder = args.support_dir
        if not os.path.isdir(folder):
            problems.append("--support-dir: {0} is not a folder".format(folder))
            return problems, notes
    else:
        folder = os.path.join(os.path.dirname(os.path.abspath(source)),
                              "_smartocr_support")
        if not os.path.isdir(folder):
            return problems, notes

    loaded = []
    for attribute, filename in (("known_patterns", "known_patterns.txt"),
                                ("error_patterns", "error_patterns.txt")):
        path = os.path.join(folder, filename)
        if getattr(args, attribute) is None and os.path.isfile(path):
            setattr(args, attribute, path)
            loaded.append(filename)

    if args.profile is None or args.learn_profile is None:
        candidates = sorted(name for name in os.listdir(folder)
                            if name.lower().endswith(".json"))
        if len(candidates) > 1:
            problems.append("support folder holds {0} profile JSONs ({1}); "
                            "pass --profile / --learn-profile explicitly"
                            .format(len(candidates), ", ".join(candidates)))
        elif candidates:
            path = os.path.join(folder, candidates[0])
            duties = []
            if args.profile is None:
                args.profile = path
                duties.append("load")
            if args.learn_profile is None:
                args.learn_profile = path
                duties.append("update")
            loaded.append("profile {0} ({1})".format(candidates[0], "+".join(duties)))

    if loaded:
        notes.append("support: {0} ({1})".format(folder, ", ".join(loaded)))
    return problems, notes


def _run(argv=None):
    started = time.monotonic()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.input = args.input_flag or args.input_pos
    if args.input_flag and args.input_pos and args.input_flag != args.input_pos:
        parser.error("input given both positionally and with -i; use one")
    if not args.input and not args.cached:
        parser.error("provide an input document or --cached OCR dump")

    support_problems, support_notes = _resolve_support(args)
    created_folder, problems, notes = _resolve_default_outputs(args)
    problems = support_problems + problems
    notes = support_notes + notes
    problems += _path_problems(args)
    if problems:
        for problem in problems:
            print("error: " + problem, file=sys.stderr)
        if created_folder:
            shutil.rmtree(created_folder, ignore_errors=True)
        return 2
    for note in notes:
        print(note, file=sys.stderr)

    requested = _requested_dpi(parser, str(args.dpi))
    args.workers = _requested_workers(parser, str(args.workers))
    if args.input and not args.cached:
        kind = sniff(args.input)
        print("container: " + kind, file=sys.stderr)
        if kind == "pdf":
            renderer = available_renderer()
            print("renderer: " + (renderer if renderer else "NONE"), file=sys.stderr)
            dpi, native, warning = resolve_dpi(args.input, requested)
            args.dpi = dpi
            origin = "native" if native and dpi == native else "requested"
            print("dpi: {0} ({1}){2}".format(
                dpi, origin, "; scan is {0} dpi".format(native) if native else ""),
                file=sys.stderr)
            if warning:
                print("warning: " + warning, file=sys.stderr)
        else:
            args.dpi = requested

    profile = None
    if args.profile:
        profile = load_profile(args.profile)
        slots, lines = counts_of(profile)
        print("profile: {0} slot canonicals, {1} standing lines".format(slots, lines),
              file=sys.stderr)

    patterns = load_patterns(args.known_patterns) if args.known_patterns else ()
    known = known_tokens(patterns)
    vocabulary = known_entries(patterns)
    plausible = make_plausibility(patterns)
    print("lexical tiebreaker: wordfreq, {0} known terms".format(len(known)),
          file=sys.stderr)

    error_patterns = load_error_patterns(args.error_patterns) if args.error_patterns else ()
    if error_patterns:
        broken = malformed(error_patterns)
        if broken:
            for pattern in broken:
                print("error pattern {0!r} must name exactly one alphanumeric "
                      "term".format(pattern), file=sys.stderr)
            return 2
        clashes = contradictions(error_patterns, vocabulary)
        if clashes:
            for clash in clashes:
                print("contradiction: {0!r} is in both the error and known "
                      "pattern files".format(clash), file=sys.stderr)
            return 2
        print("error patterns: {0} loaded".format(len(error_patterns)), file=sys.stderr)
    ascii_mode = MODE_EVIDENCE
    if args.keep_diacritics:
        ascii_mode = MODE_OFF
    elif args.force_ascii:
        ascii_mode = MODE_FORCE

    timing = {}
    pages, workdir, image_paths = _load_pages(args, timing)
    mark = time.monotonic()
    try:
        corrected, report, diagnostics = correct_document(pages, conf_gate=args.gate,
                                                          profile=profile,
                                                          ascii_mode=ascii_mode,
                                                          plausible=plausible,
                                                          disagree_ratio=args.disagree_ratio,
                                                          known=known,
                                                          error_patterns=error_patterns,
                                                          vocabulary=vocabulary,
                                                          typography=not args.keep_typography)
    except Exception:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
        raise
    timing["correct"] = time.monotonic() - mark

    _print_support(diagnostics)

    if args.review_in:
        rules, warnings = load_rules(args.review_in)
        for warning in warnings:
            print("review warning: " + warning, file=sys.stderr)
        forced, stale = apply_rules(pages, corrected, rules)
        report.extend(forced)
        print("forced corrections: {0} of {1} rules".format(len(forced), len(rules)),
              file=sys.stderr)
        for rule in stale:
            print("  stale rule (matched nothing): p{0} {1!r}".format(rule.page, rule.original),
                  file=sys.stderr)

    origin = os.path.basename(args.input or args.cached or "")
    text = assemble_text(corrected, source=origin)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print("wrote " + args.output, file=sys.stderr)
    else:
        sys.stdout.write(text + "\n")

    if args.review_out:
        source = os.path.basename(args.input or args.cached or "document")
        written = write_review(args.review_out, source, pages, report, diagnostics)
        print("review file: {0} ({1} rows)".format(args.review_out, written), file=sys.stderr)

    if args.learn_profile:
        learned = learn_profile(pages, corrected, plausible)
        save_profile(args.learn_profile, learned)
        slots, lines = counts_of(learned)
        print("learned profile: {0} slot canonicals, {1} standing lines -> {2}".format(
            slots, lines, args.learn_profile), file=sys.stderr)

    if args.pdf_out:
        _write_pdf(args, pages, corrected, image_paths)

    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)

    stray = diagnostics.get("unresolved_non_ascii", [])
    if stray:
        print("non-ASCII left in place (no corpus evidence): " + str(len(stray)), file=sys.stderr)

    saves = diagnostics.get("protected", [])
    if saves:
        print("guards prevented {0} rewrite(s); see the review file".format(len(saves)),
              file=sys.stderr)

    encounters = diagnostics.get("error_events", [])
    if encounters:
        fixed = sum(1 for entry in encounters if entry[4] == "corrected")
        print("error patterns matched {0} time(s): {1} corrected, {2} flagged".format(
            len(encounters), fixed, len(encounters) - fixed), file=sys.stderr)

    disagreements = diagnostics.get("disagreements", [])
    if disagreements:
        characters = sum(1 for entry in disagreements if entry[4] == "disagrees")
        print("confident disagreements: {0} ({1} character, {2} spacing) - not corrected".format(
            len(disagreements), characters, len(disagreements) - characters), file=sys.stderr)

    _print_timing(timing, len(pages), started)
    print("corrections: " + str(len(report)), file=sys.stderr)
    if args.report:
        for page_number, kind, old, new in report:
            print("  p{0} [{1}] {2!r} -> {3!r}".format(page_number, kind, old, new), file=sys.stderr)
        left = diagnostics["uncorrected"]
        print("uncorrected low-confidence lines: " + str(len(left)), file=sys.stderr)
        for page_number, conf, text_line, reason in left:
            print("  p{0} {1:.2f} {2!r}  ({3})".format(page_number, conf, text_line, reason),
                  file=sys.stderr)
        for page_number, conf, text_line, canonical, kind in disagreements:
            print("  p{0} {1:.2f} [{2}] {3!r}  vs canonical {4!r}".format(
                page_number, conf, kind, text_line, canonical), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# End of file #
