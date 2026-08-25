"""Parallel page recognition: size a worker pool to the machine, then run it.

smart_pdf_ocr/recognize/parallel.py

Recognition is embarrassingly parallel -- every page is independent, and the
correction layer downstream needs the whole document at once either way -- so the
only real questions are how many workers this machine can carry, and how to fail
loudly when one dies.

Workers are processes, not threads. Each holds its own recognizer engine, so a
crash is isolated and named, and nothing rests on the thread-safety of the
engine's wrapper. The pool is spawned, never forked: onnxruntime does not survive
a fork, and spawn behaves the same everywhere, macOS included.

Sizing takes the smallest of three bounds and reports which one won. The core
bound pairs each worker with a capped engine thread count, because the engine's
own intra-op threading already spreads a single page across cores; workers
multiply that, and unconstrained workers times unconstrained threads is how a
machine thrashes. The memory bound divides what the OS reports as reclaimable by
the measured cost of one worker -- model weights are cheap, inference scratch is
not -- after setting a headroom floor aside. On macOS the reading is
memory_pressure's own free percentage, with the vm_stat reclaimable pool
(free + purgeable + file-backed) as the fallback: unified-memory machines keep
most of their real headroom in caches, and naive free-page counts starve the
pool. The page bound stops the pool outgrowing the work.

When the memory probe fails, the answer is a stated retreat, not a guess: the
pool is capped at two workers and the announcement says the probe failed. A
pinned ``--workers`` value is respected, bounded only by the page count.
"""

import os
import platform
import subprocess
import multiprocessing

from smart_pdf_ocr.recognize import rapidocr_backend
from smart_pdf_ocr.patterns.field_rgx import (
    VM_STAT_FREE_rgx,
    VM_STAT_PAGE_SIZE_rgx,
    VM_STAT_PURGEABLE_rgx,
    VM_STAT_FILE_BACKED_rgx,
    MEMINFO_AVAILABLE_rgx,
    MEMORY_PRESSURE_PCT_rgx,
)


# Engine threads per worker. Two is the pairing the core bound assumes: enough
# to keep a worker from serializing on one core, few enough that four workers
# on an eight-core machine do not oversubscribe it.
INTRA_OP_THREADS = 2

# Measured worker cost: interpreter + models + inference scratch, high-water.
# Above 300 dpi the source arrays and their preprocessing copies grow.
WORKER_MB = 700
WORKER_MB_HIGH_DPI = 900
HIGH_DPI = 300

# Memory kept out of the pool's reach: the larger of a flat floor and a fraction
# of what is currently reclaimable.
HEADROOM_MB = 2048
HEADROOM_FRACTION = 0.25

MAX_WORKERS = 8

# The stated retreat when the memory probe fails on an unfamiliar platform.
UNKNOWN_MEMORY_CAP = 2

# The default macOS page size (Apple Silicon) when vm_stat's header is missing.
DARWIN_PAGE_SIZE = 16384


def _linux_available_mb(meminfo_text):
    """MemAvailable from /proc/meminfo, in MB. Zero when the field is absent."""
    found = MEMINFO_AVAILABLE_rgx.search(meminfo_text)
    if not found:
        return 0
    return int(found.group(1)) // 1024


def _darwin_pressure_mb(pressure_text, total_mb):
    """Available memory from memory_pressure's free percentage, in MB.

    This is Apple's own pressure-based view of availability, and the number
    unified memory actually honors. Field-tested: on a 16 GB machine with
    ~10 GB genuinely reclaimable, the old free+inactive formula read 3.7 GB
    and starved the pool down to one worker.
    """
    found = MEMORY_PRESSURE_PCT_rgx.search(pressure_text)
    if not found or total_mb <= 0:
        return 0
    return (total_mb * int(found.group(1))) // 100


def _darwin_vm_stat_mb(vm_stat_text):
    """Fallback reclaimable memory from vm_stat, in MB.

    free + purgeable + file-backed: the pool behind Activity Monitor's
    "available" (free plus cached files). Inactive anonymous pages are NOT
    counted -- they may be dirty and swap-bound, and counting them is how the
    pool over-commits.
    """
    size = VM_STAT_PAGE_SIZE_rgx.search(vm_stat_text)
    page_size = int(size.group(1)) if size else DARWIN_PAGE_SIZE
    pages = 0
    for regex in (VM_STAT_FREE_rgx, VM_STAT_PURGEABLE_rgx, VM_STAT_FILE_BACKED_rgx):
        found = regex.search(vm_stat_text)
        if found:
            pages += int(found.group(1))
    return (pages * page_size) // (1024 * 1024)


def _run_probe(command):
    probe = subprocess.run(command, capture_output=True, text=True, check=True)
    return probe.stdout


def _darwin_available_mb():
    """memory_pressure first; the vm_stat pool as the fallback."""
    try:
        total_mb = int(_run_probe(["sysctl", "-n", "hw.memsize"]).strip()) // (1024 * 1024)
        from_pressure = _darwin_pressure_mb(_run_probe(["memory_pressure"]), total_mb)
        if from_pressure > 0:
            return from_pressure
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        return _darwin_vm_stat_mb(_run_probe(["vm_stat"]))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def available_memory_mb():
    """Reclaimable physical memory in MB, or 0 when it cannot be discovered."""
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/meminfo", encoding="ascii") as handle:
                return _linux_available_mb(handle.read())
        if system == "Darwin":
            return _darwin_available_mb()
    except (OSError, ValueError):
        return 0
    return 0


def per_worker_mb(dpi):
    """The measured memory cost of one worker at this render resolution."""
    if dpi and dpi > HIGH_DPI:
        return WORKER_MB_HIGH_DPI
    return WORKER_MB


def size_pool(page_count, dpi, pinned=None, cores=None, memory_mb=None):
    """Return (workers, reason): how many workers, and why that number.

    ``pinned`` is a user-chosen count and wins outright, bounded only by the
    page count. ``cores`` and ``memory_mb`` exist so tests can inject readings;
    left as None, the real machine is probed.
    """
    page_count = max(1, page_count)
    if pinned is not None:
        workers = max(1, min(pinned, page_count))
        if workers < pinned:
            return workers, "pinned by --workers, bounded by %d pages" % page_count
        return workers, "pinned by --workers"

    if cores is None:
        cores = os.cpu_count() or 1
    if memory_mb is None:
        memory_mb = available_memory_mb()

    core_bound = max(1, cores // INTRA_OP_THREADS)
    if memory_mb <= 0:
        workers = max(1, min(core_bound, UNKNOWN_MEMORY_CAP, page_count))
        return workers, "memory probe failed; capped at %d" % UNKNOWN_MEMORY_CAP

    headroom = max(HEADROOM_MB, int(memory_mb * HEADROOM_FRACTION))
    cost = per_worker_mb(dpi)
    mem_bound = max(1, (memory_mb - headroom) // cost)
    workers = max(1, min(core_bound, mem_bound, page_count, MAX_WORKERS))
    reason = ("%d cores -> %d, %.1fGB free -> %d @ %dMB/worker, %d pages"
              % (cores, core_bound, memory_mb / 1024.0, mem_bound, cost, page_count))
    return workers, reason


def _worker_setup():
    """Runs once in each spawned worker: cap engine threads, pay the load cost.

    Warming in the initializer moves the multi-second model load to the moment
    the pool is announced, instead of hiding it inside the first page's timing.
    """
    rapidocr_backend.set_engine_params({
        "EngineConfig.onnxruntime.intra_op_num_threads": INTRA_OP_THREADS,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    })
    rapidocr_backend.warm_engine()


def _recognize_one(task):
    """One page, in a worker. Failures are returned, not raised, so the parent
    can name the page instead of unpickling a traceback of unknown shape."""
    page_number, image_path = task
    try:
        return "ok", rapidocr_backend.recognize_page(image_path, page_number), ""
    except Exception as exc:
        return "error", page_number, "%s: %s" % (type(exc).__name__, exc)


def recognize_parallel(image_paths, workers, progress=None):
    """Recognize all pages across a spawned pool; results in page order.

    ``progress`` is called with (done, total) as pages complete, in completion
    order -- the count is monotonic even though the pages are not. A failed page
    aborts the whole run naming that page. Exiting the ``with`` block on an
    error or an interrupt terminates the pool, so no orphan worker survives.
    """
    tasks = [(index + 1, path) for index, path in enumerate(image_paths)]
    total = len(tasks)
    results = []
    context = multiprocessing.get_context("spawn")
    with context.Pool(processes=workers, initializer=_worker_setup) as pool:
        for outcome in pool.imap_unordered(_recognize_one, tasks):
            if outcome[0] == "error":
                raise RuntimeError("recognition failed on page %d: %s"
                                   % (outcome[1], outcome[2]))
            results.append(outcome[1])
            if progress is not None:
                progress(len(results), total)
    results.sort(key=lambda page: page.page_number)
    if len(results) != total:
        raise RuntimeError("pool returned %d of %d pages" % (len(results), total))
    return results


# End of file #
