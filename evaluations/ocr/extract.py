"""Persist direct PDF text offline, unnormalized and unpublished; one exclusive run."""
import argparse
from pathlib import Path
import subprocess
import sys

import audit
from pilot import BASE, IMAGE, POPPLER_TIMEOUT, digest, docker_args, error_category, page_count, write_json

AUDIT = BASE / '.artifacts/corpus-audit.json'
OUTPUT = BASE / '.artifacts/extraction-direct-v1'


def identity(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), digest(data)


def source_path(path: Path) -> str:
    """Repository-relative in production; preserve absolute isolated fixture paths."""
    return str(path.relative_to(BASE.parents[1]) if path.is_relative_to(BASE.parents[1]) else path)


def extract() -> None:
    """Reserve first; publish the completion manifest only after every check succeeds."""
    import json

    OUTPUT.mkdir()  # Existing directory (including failed runs) forbids any retry.
    prior_bytes = AUDIT.read_bytes()
    prior_hash = digest(prior_bytes)
    previous = json.loads(prior_bytes)
    paths = audit.inventory()
    before = {p: identity(p) for p in paths}
    expected = {d['file']: d for d in previous['documents']}
    if len(previous['documents']) != 7 or set(expected) != {p.name for p in paths}:
        raise ValueError('Audit inventory mismatch')
    documents = []
    totals = dict(pages=0, pending_ocr=0, sparse_review=0, direct_candidate=0)
    for path in paths:
        old = expected[path.name]
        if before[path] != (old['bytes'], old['sha256_before']) or old['sha256_before'] != old['sha256_after']:
            raise ValueError('Audit source mismatch')
        count = page_count(path)
        if count != old['pages']:
            raise ValueError('Audit page count mismatch')
        result = subprocess.run(docker_args() + ['--mount',
            f'type=bind,src={path.resolve()},dst=/source.pdf,readonly',
            IMAGE, 'pdftotext', '-enc', 'UTF-8', '/source.pdf', '-'],
            check=True, capture_output=True, timeout=POPPLER_TIMEOUT)
        text = result.stdout.decode('utf-8')  # Bytes mode avoids newline translation.
        counts = audit.charcounts(text, count)
        if counts != old['page_charcounts']:
            raise ValueError('Audit text metric mismatch')
        pages = []
        for number, (chunk, chars) in enumerate(zip(text.split('\f')[:-1], counts), 1):
            status = 'pending_ocr' if chars == 0 else 'sparse_review' if chars < 80 else 'direct_candidate'
            pages.append(dict(physical_page=number, text=chunk, charcount=chars,
                              method='pdftotext', status=status, requires_review=True))
            totals[status] += 1
            totals['pages'] += 1
        documents.append(dict(id='sha256:' + before[path][1], source_name=path.name,
            source_path=source_path(path), bytes=before[path][0],
            sha256_before=before[path][1], page_count=count, pages=pages,
            requires_review=True))
    if audit.inventory() != paths:
        raise ValueError('Inventory changed')
    for path, doc in zip(paths, documents):
        after = identity(path)
        if before[path] != after:
            raise ValueError('Source changed')
        doc['sha256_after'] = after[1]
        doc['bytes_after'] = after[0]
    if digest(AUDIT.read_bytes()) != prior_hash:
        raise ValueError('Prior audit changed')
    payload = dict(schema_version=1, documents=documents, totals=totals,
                   requires_review=True, published=False)
    write_json(OUTPUT / 'documents.json', payload)
    write_json(OUTPUT / 'manifest.json', dict(schema_version=1,
        state='complete_requires_review', published=False, image=IMAGE,
        command=['pdftotext', '-enc', 'UTF-8', '/source.pdf', '-'],
        page_boundary='U+000C terminator removed per page; reconstruct with text + U+000C',
        charcount_rule='len(text.strip()) Unicode code points; triage only',
        prior_artifacts={str(AUDIT): prior_hash},
        documents_sha256=digest((OUTPUT / 'documents.json').read_bytes()),
        document_count=len(documents), totals=totals))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=
        'No OCR, ingestion or publishing. Existing output blocks retries, even after failure. '
        'Only a successful exit plus a valid final manifest indicates completion of direct extraction.')
    parser.parse_args()
    try:
        extract()
    except Exception as error:
        print(f'Extraction failed: {error_category(error)}.', file=sys.stderr)
        return 1
    print('Direct extraction saved: 7 PDFs; requires review.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
