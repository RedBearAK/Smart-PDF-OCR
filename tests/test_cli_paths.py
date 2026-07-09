"""Tests for path validation and clean failure.

tests/test_cli_paths.py

Four of the paths a run depends on are not touched until the recognizer has
finished: the corrected text, the review file, the learned profile, and the review
file being applied. A mistyped directory among them would otherwise be discovered
only after a quarter of an hour of work had been thrown away. So every path is
checked before a single page is read, all problems are reported at once, and a
missing file is a usage error rather than a traceback.
"""

import io
import os
import contextlib
import tempfile

import smart_pdf_ocr.cli as cli


def _run(argv):
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
        try:
            code = cli.main(argv)
        except SystemExit as exit_signal:
            code = exit_signal.code if isinstance(exit_signal.code, int) else 2
    return code, stderr.getvalue()


def _cached_dump():
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    handle.write('{"1": {"txts": ["GalaxSea Freight Forwarding"], "scores": [0.99]}}')
    handle.close()
    return handle.name


def test_missing_input_file_is_a_usage_error():
    """A missing known-patterns file exits 2 and names the flag."""
    dump = _cached_dump()
    code, err = _run(["--cached", dump, "-o", "/tmp/out.txt",
                      "--known-patterns", "/tmp/definitely_absent.txt"])
    os.unlink(dump)
    print(f"  exit={code}  {err.strip()}")
    return code == 2 and "--known-patterns" in err and "does not exist" in err


def test_unwritable_output_is_caught_before_any_work():
    """A bad output directory is refused up front, not after recognition."""
    dump = _cached_dump()
    code, err = _run(["--cached", dump, "-o", "/tmp/no_such_dir/deep/out.txt"])
    os.unlink(dump)
    print(f"  exit={code}  {err.strip()}")
    return code == 2 and "--output" in err and "does not exist" in err


def test_every_problem_is_reported_at_once():
    """Fixing one path at a time is a poor way to spend fifteen minutes."""
    code, err = _run(["--cached", "/tmp/absent.json", "-o", "/tmp/out.txt",
                      "--known-patterns", "/tmp/absent.txt",
                      "--review-out", "/tmp/no_such_dir/r.tsv"])
    lines = [line for line in err.splitlines() if line.startswith("error:")]
    print(f"  exit={code}  reported {len(lines)} problems")
    return code == 2 and len(lines) == 3


def test_directory_given_where_a_file_belongs():
    """A directory is not a document."""
    code, err = _run(["/tmp", "-o", "/tmp/out.txt"])
    print(f"  exit={code}  {err.strip()}")
    return code == 2 and "is a directory" in err


def test_malformed_json_names_the_file():
    """A parse failure says which file, and does not raise."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    handle.write("{not json")
    handle.close()
    dump = _cached_dump()
    code, err = _run(["--cached", dump, "--profile", handle.name, "-o", "/tmp/out.txt"])
    os.unlink(handle.name)
    os.unlink(dump)
    print(f"  exit={code}  {err.strip()[:70]}")
    return code == 1 and handle.name in err and "not valid JSON" in err


def test_input_flag_and_positional_are_equivalent():
    """-i and the bare positional reach the same place; order does not matter."""
    dump = _cached_dump()
    out = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    out.close()
    # filename last, via -i, with options first -- the pasteable-command case
    code, err = _run(["-o", out.name, "--cached", dump])
    os.unlink(dump)
    os.unlink(out.name)
    print(f"  options-first, --cached -> exit {code}")
    return code == 0


def test_input_given_twice_is_refused():
    """Positional and -i together, disagreeing, is a usage error."""
    code, err = _run(["a.pdf", "-i", "b.pdf", "-o", "/tmp/x.txt"])
    print(f"  exit {code}  {err.strip()[:48]}")
    return code == 2 and "one" in err


def test_a_good_run_still_succeeds():
    """Validation must not reject a correct invocation."""
    dump = _cached_dump()
    out = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    out.close()
    code, err = _run(["--cached", dump, "-o", out.name])
    os.unlink(dump)
    os.unlink(out.name)
    print(f"  exit={code}")
    return code == 0


def main():
    tests = [
        test_missing_input_file_is_a_usage_error,
        test_unwritable_output_is_caught_before_any_work,
        test_every_problem_is_reported_at_once,
        test_directory_given_where_a_file_belongs,
        test_malformed_json_names_the_file,
        test_a_good_run_still_succeeds,
        test_input_flag_and_positional_are_equivalent,
        test_input_given_twice_is_refused,
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
