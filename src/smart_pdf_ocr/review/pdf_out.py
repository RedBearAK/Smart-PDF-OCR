"""Searchable PDF: the original scan with an invisible corrected text layer.

smart_pdf_ocr/review/pdf_out.py

The output everywhere else in this tool is text. A searchable PDF keeps the page --
and, importantly, keeps the *original* page: the scanned image is never decoded or
re-encoded, only annotated. Over each page, as an invisible text layer, sits the
corrected text, each line placed at the box the recognizer found it in. Select a
line and the selection lands on the pixels; search for a word and it is found, even
where the correction layer changed a misread `WORLOWIDE` into the `WORLDWIDE` the
page plainly shows.

The layer is appended to the original PDF with pikepdf, which copies the existing
image objects untouched. That is the whole reason the file barely grows: a 2.7 MB
scan gains a few kilobytes of text and stays 2.7 MB, where rebuilding the page from
its decoded image would inflate it many times over. The text is drawn in render
mode 3 -- present to a machine, invisible to a human, who sees only the untouched
scan.

Boxes come from the recognizer in image pixels, top-left origin. PDF user space is
bottom-left, so the y-axis is flipped once, at draw time. pikepdf is an optional
dependency, declared under the ``searchable-pdf`` extra; the text output needs none
of this.
"""

import importlib.util


# Text render mode 3: added to the content stream, drawn by nothing. The line is
# selectable and searchable but leaves no mark over the scan.
INVISIBLE = 3

# A line with no box cannot be placed over the image and is skipped rather than
# stacked at the origin.
MISSING_BOX = ()


def is_available():
    return importlib.util.find_spec("pikepdf") is not None


def _escape(text):
    """Escape the three characters that are special inside a PDF literal string."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _font_size(box_height):
    return max(4.0, box_height * 0.9)


def _text_stream(lines, page_height, scale_x, scale_y):
    """Build an invisible text content stream for one page.

    ``lines`` is a sequence of (text, box) with the box in image pixels. The box's
    lower edge in image space becomes the text baseline in PDF space after the
    y-flip.
    """
    ops = ["BT", "{0} Tr".format(INVISIBLE)]
    for text, box in lines:
        if not text or box == MISSING_BOX:
            continue
        x0, _y0, _x1, y1 = box
        x = x0 * scale_x
        baseline = page_height - (y1 * scale_y)
        size = _font_size((y1 - _y0) * scale_y)
        ops.append("/F1 {0:.1f} Tf".format(size))
        ops.append("1 0 0 1 {0:.1f} {1:.1f} Tm".format(x, baseline))
        ops.append("({0}) Tj".format(_escape(text)))
    ops.append("ET")
    return ("\n".join(ops)).encode("latin-1", "replace")


def write_searchable_pdf(path, source_pdf, pages, image_sizes):
    """Append an invisible text layer to ``source_pdf`` and save to ``path``.

    ``pages`` is a list, one entry per PDF page, of (text, box) line sequences with
    boxes in image pixels. ``image_sizes`` gives (width_px, height_px) per page, so
    pixel boxes map onto the page's point dimensions. The original image objects are
    copied untouched; only the text layer is added.
    """
    import pikepdf
    from pikepdf import Name, Stream, Dictionary

    pdf = pikepdf.open(source_pdf)
    try:
        for index, page in enumerate(pdf.pages):
            if index >= len(pages):
                break
            lines = pages[index]
            width_px, height_px = image_sizes[index]
            media = page.MediaBox
            page_width = float(media[2]) - float(media[0])
            page_height = float(media[3]) - float(media[1])
            scale_x = page_width / width_px
            scale_y = page_height / height_px

            font = pdf.make_indirect(Dictionary(
                Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
            if Name.Resources not in page:
                page.Resources = Dictionary()
            if Name.Font not in page.Resources:
                page.Resources.Font = Dictionary()
            page.Resources.Font.F1 = font

            stream = _text_stream(lines, page_height, scale_x, scale_y)
            page.contents_add(Stream(pdf, stream), prepend=False)
        pdf.save(path)
    finally:
        pdf.close()
    return path


def pages_from(page_results, corrected):
    """Pair each page's corrected lines with the boxes from recognition.

    ``corrected[page_number]`` is the list of corrected strings, index-aligned with
    ``page.lines``; the box rides the original line. Returns a list of line sequences
    ordered by page number, ready for ``write_searchable_pdf``.
    """
    ordered = sorted(page_results, key=lambda page: page.page_number)
    sheet = []
    for page in ordered:
        texts = corrected.get(page.page_number, page.texts)
        lines = []
        for index, line in enumerate(page.lines):
            text = texts[index] if index < len(texts) else line.text
            lines.append((text, tuple(line.box)))
        sheet.append(lines)
    return sheet


# End of file #
