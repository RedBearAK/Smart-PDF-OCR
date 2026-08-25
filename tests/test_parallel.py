"""Tests for parallel recognition: pool sizing and the spawned pool itself.

tests/test_parallel.py

The sizing policy takes the smallest of three bounds -- cores paired with capped
engine threads, reclaimable memory over measured per-worker cost, and the page
count -- and states which bound won. These tests drive it with injected readings,
so the policy is pinned without depending on the machine running the suite. The
memory parsers get canned /proc/meminfo, memory_pressure, and vm_stat text for the same reason.

The last two tests spawn a real pool: two workers over three tiny images must
produce exactly what the sequential path produces, in page order, and a page
that cannot be read must abort the run naming that page. Both are skipped when
the recognizer or Pillow is absent, matching the suite's convention.
"""

import os
import tempfile

from smart_pdf_ocr.recognize.parallel import (
    size_pool,
    per_worker_mb,
    available_memory_mb,
    recognize_parallel,
    _linux_available_mb,
    _darwin_pressure_mb,
    _darwin_vm_stat_mb,
    MAX_WORKERS,
    UNKNOWN_MEMORY_CAP,
)


MEMINFO_SAMPLE = """MemTotal:       16384000 kB
MemFree:         2048000 kB
MemAvailable:   10240000 kB
Buffers:          123456 kB
"""

MEMORY_PRESSURE_SAMPLE = """The system has 17179869184 (1048576 pages with a page size of 16384).

Stats:
Pages free: 226092
Pages purgeable: 12288
Pages purged: 10608

Swap I/O:
Swapins: 0
Swapouts: 0

System-wide memory free percentage: 63%
"""

VM_STAT_SAMPLE = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              100000.
Pages active:                            300000.
Pages inactive:                          200000.
Pages speculative:                        50000.
Pages purgeable:                          30000.
Pages wired down:                        150000.
File-backed pages:                       220000.
Anonymous pages:                         330000.
"""


def _deps():
    import importlib.util
    return (importlib.util.find_spec("rapidocr") is not None
            and importlib.util.find_spec("PIL") is not None)


def test_meminfo_parse_reads_available():
    """MemAvailable is the number that matters, converted to MB."""
    mb = _linux_available_mb(MEMINFO_SAMPLE)
    print(f"  parsed {mb} MB")
    return mb == 10000


def test_memory_pressure_percentage_is_primary():
    """Apple's own free percentage of total memory: 63% of 16 GB."""
    mb = _darwin_pressure_mb(MEMORY_PRESSURE_SAMPLE, 16384)
    print(f"  parsed {mb} MB")
    return mb == 16384 * 63 // 100


def test_memory_pressure_without_the_line_yields_zero():
    """No percentage line means no answer, so the fallback gets its turn."""
    mb = _darwin_pressure_mb("Stats:\nPages free: 5\n", 16384)
    print(f"  parsed {mb} MB")
    return mb == 0


def test_vm_stat_fallback_counts_the_reclaimable_pool():
    """free + purgeable + file-backed, times the stated page size.

    Inactive anonymous pages are deliberately NOT counted: they may be dirty
    and swap-bound. Field report: counting inactive+speculative read 3.7 GB
    on a machine with ~10 GB genuinely available and starved the pool.
    """
    mb = _darwin_vm_stat_mb(VM_STAT_SAMPLE)
    expected = ((100000 + 30000 + 220000) * 16384) // (1024 * 1024)
    print(f"  parsed {mb} MB (expected {expected})")
    return mb == expected


def test_core_bound_pairs_workers_with_capped_threads():
    """Eight cores at two engine threads each carry four workers."""
    workers, reason = size_pool(82, 300, cores=8, memory_mb=20000)
    print(f"  {workers} ({reason})")
    return workers == 4 and "8 cores" in reason


def test_memory_bound_wins_on_a_small_machine():
    """Plenty of cores, little memory: the memory bound decides."""
    workers, reason = size_pool(82, 300, cores=16, memory_mb=3500)
    # headroom max(2048, 875) = 2048; (3500 - 2048) // 700 = 2
    print(f"  {workers} ({reason})")
    return workers == 2


def test_high_dpi_raises_the_per_worker_cost():
    """Above 300 dpi a worker is budgeted at the larger measured cost."""
    low, high = per_worker_mb(300), per_worker_mb(600)
    workers_low, _ = size_pool(82, 300, cores=16, memory_mb=6000)
    workers_high, _ = size_pool(82, 600, cores=16, memory_mb=6000)
    print(f"  {low}MB@300 -> {workers_low} workers, {high}MB@600 -> {workers_high}")
    return low < high and workers_high < workers_low


def test_page_count_bounds_the_pool():
    """Two pages never justify more than two workers."""
    workers, _reason = size_pool(2, 300, cores=16, memory_mb=32000)
    print(f"  {workers}")
    return workers == 2


def test_hard_cap_and_floor():
    """A monster machine stops at the cap; a starved one still gets one."""
    capped, _ = size_pool(500, 300, cores=64, memory_mb=200000)
    starved, _ = size_pool(500, 300, cores=2, memory_mb=2100)
    print(f"  capped={capped} starved={starved}")
    return capped == MAX_WORKERS and starved == 1


def test_unknown_memory_is_a_stated_retreat():
    """A failed probe caps the pool and says so, rather than guessing."""
    workers, reason = size_pool(82, 300, cores=16, memory_mb=0)
    print(f"  {workers} ({reason})")
    return workers == UNKNOWN_MEMORY_CAP and "probe failed" in reason


def test_pinned_workers_win_but_never_outnumber_pages():
    """--workers is respected outright, bounded only by the work."""
    pinned, reason_a = size_pool(82, 300, pinned=6, cores=2, memory_mb=2100)
    bounded, reason_b = size_pool(3, 300, pinned=6, cores=16, memory_mb=32000)
    print(f"  pinned={pinned} ({reason_a}); bounded={bounded} ({reason_b})")
    return pinned == 6 and bounded == 3 and "pinned" in reason_a


def test_real_probe_reports_something_on_this_machine():
    """The live probe returns a positive reading here (Linux or macOS)."""
    mb = available_memory_mb()
    print(f"  available: {mb} MB")
    return mb > 0


def _tiny_pages(workdir, count):
    """Small legible page images the recognizer can read quickly."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=40)
    paths = []
    words = ["GalaxSea Freight", "SHIPPING WORLDWIDE", "Forwarding Fee"]
    for index in range(count):
        image = Image.new("RGB", (420, 120), "white")
        ImageDraw.Draw(image).text((16, 36), words[index % len(words)],
                                   fill="black", font=font)
        path = os.path.join(workdir, "page-%02d.jpg" % (index + 1))
        image.save(path, quality=95)
        paths.append(path)
    return paths


def test_pool_matches_the_sequential_path():
    """Two workers over three pages read exactly what one process reads."""
    if not _deps():
        print("  skipped: rapidocr or pillow absent")
        return True
    from smart_pdf_ocr.recognize import rapidocr_backend
    work = tempfile.mkdtemp()
    paths = _tiny_pages(work, 3)
    sequential = [rapidocr_backend.recognize_page(p, i + 1)
                  for i, p in enumerate(paths)]
    ticks = []
    parallel = recognize_parallel(paths, 2, progress=lambda d, t: ticks.append((d, t)))
    seq_texts = [page.texts for page in sequential]
    par_texts = [page.texts for page in parallel]
    ordered = [page.page_number for page in parallel] == [1, 2, 3]
    print(f"  sequential={seq_texts}")
    print(f"  parallel  ={par_texts}  ordered={ordered}  ticks={ticks}")
    return seq_texts == par_texts and ordered and ticks[-1] == (3, 3)


def test_failed_page_aborts_naming_the_page():
    """A worker that cannot read its page fails the run loudly, by number."""
    if not _deps():
        print("  skipped")
        return True
    work = tempfile.mkdtemp()
    paths = _tiny_pages(work, 1) + [os.path.join(work, "absent.jpg")]
    try:
        recognize_parallel(paths, 1)
    except RuntimeError as exc:
        print(f"  {exc}")
        return "page 2" in str(exc)
    print("  no error raised")
    return False


def main():
    tests = [
        test_meminfo_parse_reads_available,
        test_memory_pressure_percentage_is_primary,
        test_memory_pressure_without_the_line_yields_zero,
        test_vm_stat_fallback_counts_the_reclaimable_pool,
        test_core_bound_pairs_workers_with_capped_threads,
        test_memory_bound_wins_on_a_small_machine,
        test_high_dpi_raises_the_per_worker_cost,
        test_page_count_bounds_the_pool,
        test_hard_cap_and_floor,
        test_unknown_memory_is_a_stated_retreat,
        test_pinned_workers_win_but_never_outnumber_pages,
        test_real_probe_reports_something_on_this_machine,
        test_pool_matches_the_sequential_path,
        test_failed_page_aborts_naming_the_page,
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
