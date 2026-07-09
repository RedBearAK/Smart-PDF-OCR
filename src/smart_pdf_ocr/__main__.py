"""Module entry point, so the package runs as ``python -m smart_pdf_ocr``.

smart_pdf_ocr/__main__.py

Delegates to the argparse front-end in ``cli``. This exists because a package
without ``__main__`` cannot be executed with ``-m`` at all; the console script
declared in pyproject only becomes available after an install.
"""

import sys

from smart_pdf_ocr.cli import main


if __name__ == "__main__":
    sys.exit(main())


# End of file #
