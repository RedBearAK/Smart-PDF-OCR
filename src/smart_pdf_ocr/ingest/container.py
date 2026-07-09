"""Container ingest: sniff the real type, unpack bundles, enumerate pages.

smart_pdf_ocr/ingest/container.py

The file extension is not trusted. A ``.pdf`` may be a genuine PDF, or a ZIP
bundle of page images plus a manifest (as some ingestion pipelines emit), or a
bare image. The sniff reads magic bytes and routes accordingly, producing an
ordered list of page-image paths for the recognizer plus a temp directory the
caller cleans up.

Rasterizing a real PDF needs a renderer. ``pypdfium2`` is preferred: it ships as
a wheel, so ``pip install`` alone is sufficient, and it renders at least as well
as poppler on this corpus. The poppler ``pdftoppm`` binary is accepted as a
fallback when it is already on the system. When neither is present the failure is
reported with install instructions rather than as a bare FileNotFoundError.

The DPI is not a quality dial. A scanned page holds exactly as much detail as the
image embedded in it, and ``scale = dpi / 72`` decides only how that detail is
resampled on the way out. Render below the scan's native resolution and pixels are
destroyed for good. Render above it and nothing is gained -- only the interpolation
and compression artefacts change, and the recognizer's response to those is a
lottery, not a gradient. So the default is the native resolution itself: lossless,
and the one setting that cannot be wrong.
"""

import os
import json
import shutil
import zipfile
import tempfile
import subprocess
import importlib.util


# Used only when the embedded scan's own resolution cannot be discovered.
FALLBACK_DPI = 300

# pdfium's page-object type for an image. Compared numerically so this module
# imports on a machine without pypdfium2.
IMAGE_OBJECT = 3

PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"
JPEG_MAGIC = b"\xff\xd8\xff"

MISSING_RENDERER_MESSAGE = (
    "cannot rasterize a PDF: no PDF renderer found.\n"
    "  preferred:  pip install pypdfium2\n"
    "  or install the poppler tools that provide pdftoppm:\n"
    "    macOS          brew install poppler\n"
    "    Debian/Ubuntu  sudo apt install poppler-utils\n"
    "    Fedora/RHEL    sudo dnf install poppler-utils\n"
    "    Arch           sudo pacman -S poppler"
)


def sniff(path):
    with open(path, "rb") as handle:
        head = handle.read(8)
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(ZIP_MAGIC):
        return "bundle"
    if head.startswith(JPEG_MAGIC):
        return "image"
    return "unknown"


def available_renderer():
    """Return 'pypdfium2', 'pdftoppm', or '' when no renderer is installed."""
    if importlib.util.find_spec("pypdfium2") is not None:
        return "pypdfium2"
    if shutil.which("pdftoppm"):
        return "pdftoppm"
    return ""


def native_dpi(path, sample_pages=4):
    """The resolution of the scan inside the PDF, or 0 when there is none.

    A page may embed several images; the largest resolution wins, since that is
    the one whose detail would be lost. Pages without images (vector or text PDFs)
    contribute nothing, and a document of them reports 0.
    """
    if importlib.util.find_spec("pypdfium2") is None:
        return 0
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    best = 0
    for index in range(min(len(document), sample_pages)):
        for obj in document[index].get_objects():
            if obj.type != IMAGE_OBJECT:
                continue
            try:
                meta = obj.get_metadata()
            except Exception:
                continue
            for value in (meta.horizontal_dpi, meta.vertical_dpi):
                if value and value > best:
                    best = int(round(value))
    return best


def resolve_dpi(path, requested=None):
    """Return (dpi, native, warning). ``requested=None`` means use the native one."""
    native = native_dpi(path)
    if requested is None:
        if native:
            return native, native, ""
        return FALLBACK_DPI, 0, ("native resolution unknown; rendering at %d dpi"
                                 % FALLBACK_DPI)
    if native and requested < native:
        return requested, native, ("requested %d dpi is below the scan's native %d dpi; "
                                   "detail will be discarded" % (requested, native))
    if native and requested > native:
        return requested, native, ("requested %d dpi exceeds the scan's native %d dpi; "
                                   "this adds no detail" % (requested, native))
    return requested, native, ""


def _numeric_stem(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    digits = "".join(ch for ch in stem if ch.isdigit())
    return int(digits) if digits else 0


def _unpack_bundle(path, workdir):
    with zipfile.ZipFile(path) as archive:
        archive.extractall(workdir)
    order = []
    manifest_path = os.path.join(workdir, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        pages = manifest.get("pages", []) if isinstance(manifest, dict) else []
        for entry in pages:
            if not isinstance(entry, dict):
                continue
            image = entry.get("image", {})
            rel = image.get("path") if isinstance(image, dict) else None
            if rel:
                number = int(entry.get("page_number", len(order) + 1))
                order.append((number, os.path.join(workdir, rel)))
    if not order:
        names = [f for f in os.listdir(workdir) if f.lower().endswith((".jpeg", ".jpg", ".png"))]
        names.sort(key=_numeric_stem)
        order = [(i + 1, os.path.join(workdir, f)) for i, f in enumerate(names)]
    order.sort()
    return [image_path for _number, image_path in order]


def _rasterize_with_pdfium(path, workdir, dpi):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    scale = dpi / 72.0
    paths = []
    for index, page in enumerate(document):
        image = page.render(scale=scale).to_pil().convert("RGB")
        target = os.path.join(workdir, "page-%04d.jpg" % (index + 1))
        image.save(target, quality=95)
        paths.append(target)
    return paths


def _rasterize_with_poppler(path, workdir, dpi):
    prefix = os.path.join(workdir, "page")
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi), path, prefix],
        check=True,
        capture_output=True,
    )
    names = [f for f in os.listdir(workdir) if f.startswith("page") and f.endswith(".jpg")]
    names.sort(key=_numeric_stem)
    return [os.path.join(workdir, name) for name in names]


def _rasterize_pdf(path, workdir, dpi=FALLBACK_DPI):
    renderer = available_renderer()
    if renderer == "pypdfium2":
        return _rasterize_with_pdfium(path, workdir, dpi)
    if renderer == "pdftoppm":
        return _rasterize_with_poppler(path, workdir, dpi)
    raise RuntimeError(MISSING_RENDERER_MESSAGE)


def enumerate_pages(path, dpi=None):
    """Return (ordered_image_paths, workdir). The caller removes workdir."""
    kind = sniff(path)
    if kind == "pdf" and dpi is None:
        dpi = resolve_dpi(path)[0]
    workdir = tempfile.mkdtemp(prefix="smart_pdf_ocr_")
    if kind == "bundle":
        return _unpack_bundle(path, workdir), workdir
    if kind == "pdf":
        try:
            return _rasterize_pdf(path, workdir, dpi), workdir
        except Exception:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
    if kind == "image":
        target = os.path.join(workdir, os.path.basename(path))
        shutil.copy(path, target)
        return [target], workdir
    shutil.rmtree(workdir, ignore_errors=True)
    raise ValueError("unrecognized container: " + kind)


# End of file #
