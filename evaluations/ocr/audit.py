"""Audit seven local PDFs offline; exclusive metadata output, never full text."""
import argparse
from pathlib import Path
import subprocess
import sys

from pilot import (BASE, IMAGE, POPPLER_TIMEOUT, digest, docker_args,
                   error_category, page_count, write_json)

SOURCE = BASE.parents[1] / 'documents/documentos_bienestar'
OUTPUT = BASE / '.artifacts/corpus-audit.json'


def inventory() -> list[Path]:
    """Require exactly seven regular PDFs, never symlinks or another corpus."""
    paths = sorted(SOURCE.glob('*.pdf'))
    if len(paths) != 7 or any(p.is_symlink() or not p.is_file() for p in paths):
        raise ValueError('Invalid corpus')
    return paths


def charcounts(text: str, pages: int) -> list[int]:
    """Poppler terminates every physical page with FF, including blank pages."""
    if pages < 1 or not text.endswith('\f'):
        raise ValueError('Missing physical page delimiter')
    chunks = text.split('\f')
    # Remove only the delimiter sentinel, never strip/filter real blank pages.
    if chunks[-1] != '' or len(chunks) - 1 != pages:
        raise ValueError('Physical page mismatch')
    return [len(chunk.strip()) for chunk in chunks[:-1]]


def audit() -> dict:
    """Hash the whole inventory before and after bounded local extraction."""
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise FileExistsError('Existing output')
    paths = inventory()
    before = {p: (p.stat().st_size, digest(p.read_bytes())) for p in paths}
    documents = []
    totals = dict(pages=0, ocr_candidate=0, review=0, direct_candidate=0)
    for path in paths:
        pages = page_count(path)
        result = subprocess.run(docker_args() + ['--mount',
            f'type=bind,src={path.resolve()},dst=/source.pdf,readonly',
            IMAGE, 'pdftotext', '-enc', 'UTF-8', '/source.pdf', '-'],
            check=True, capture_output=True, timeout=POPPLER_TIMEOUT)
        counts = charcounts(result.stdout.decode('utf-8'), pages)
        summary = dict(pages=pages, ocr_candidate=counts.count(0),
                       review=sum(1 <= n <= 79 for n in counts),
                       direct_candidate=sum(n >= 80 for n in counts))
        documents.append(dict(file=path.name, bytes=before[path][0],
                              sha256_before=before[path][1], pages=pages,
                              page_charcounts=counts, **{k: v for k, v in summary.items()
                                                        if k != 'pages'}))
        for key, value in summary.items():
            totals[key] += value
    if inventory() != paths:
        raise ValueError('Inventory changed')
    for path, document in zip(paths, documents):
        after = (path.stat().st_size, digest(path.read_bytes()))
        if before[path] != after:
            raise ValueError('Source changed')
        document['sha256_after'] = after[1]
    return dict(schema_version=1, image=IMAGE, documents=documents, totals=totals,
                rule='Unicode len(page.strip()): 0 OCR candidate; 1..79 review; '
                     '>=80 direct extraction candidate, not quality assurance')


def main() -> int:
    """No retries, no network, and no successful partial audit on failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = audit()
        # Reuse the existing exclusive, flushed, fsynced record writer.
        write_json(OUTPUT, result)
    except Exception as error:
        print(f'Audit failed: {error_category(error)}.', file=sys.stderr)
        return 1
    print('Audit complete: 7 PDFs.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
