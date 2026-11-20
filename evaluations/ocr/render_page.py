"""Render one page of an original PDF to PNG for human comparison (PyMuPDF).

Not part of the project dependencies; run isolated, writing to a scratch dir:

    uvx --with pymupdf python -I evaluations/ocr/render_page.py \\
        "documents/documentos_bienestar/DOCUMENTO TECNICO RENTA JOVEN.pdf" \\
        54 46af91fb3a4efc942070444631141442e0343b75ff5584c5a77f9598f302fc0a \\
        <scratch-dir>

Checks the PDF SHA-256 first; writes ``<stem>-p<N>-150dpi.png`` (whole page)
and ``<stem>-p<N>-top-300dpi.png`` (upper 55 %: DT p54 Insumos diagram).
"""

import hashlib
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    """Render; exit 1 on SHA mismatch or bad arguments."""
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 1
    source, number, sha, out = Path(argv[0]), int(argv[1]), argv[2], Path(argv[3])
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        print("SHA-256 mismatch", file=sys.stderr)
        return 1
    import pymupdf

    out.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(stream=data, filetype="pdf")
    page = doc[number - 1]
    stem = f"{sha[:12]}-p{number}"
    page.get_pixmap(dpi=150).save(out / f"{stem}-150dpi.png")
    r = page.rect
    top = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * 0.55)
    page.get_pixmap(dpi=300, clip=top).save(out / f"{stem}-top-300dpi.png")
    print(out / f"{stem}-150dpi.png")
    print(out / f"{stem}-top-300dpi.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
