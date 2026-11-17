"""Prepare exactly 35 authorized OCR pages offline; never send or retry."""
import argparse
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
from pilot import (ARTIFACTS, IMAGE, ORIGINAL, ORIGINAL_SHA256, digest,
                   docker_args, error_category, page_count, write_json)

OUTPUT = ARTIFACTS / 'remaining-ocr-v1'
# Independent pins from the existing audit and approved pilot, not a new manifest.
SOURCES = (
    (ORIGINAL.parent / 'Res00137ReglamentaRentaJoven.pdf',
     'c855bd4b0f33947857778afc9271250e9c7fe78b6fdadf890dd3b2b56920e362', 20),
    (ORIGINAL, ORIGINAL_SHA256, 17),
)
PAGES = tuple(range(1, 13)) + (15, 16, 17)


def evidence() -> dict:
    """Hash all prior artifact bytes without interpreting or printing contents."""
    result = {}
    for path in sorted(ARTIFACTS.rglob('*')):
        if path == OUTPUT or OUTPUT in path.parents:
            continue
        if path.is_symlink():
            raise ValueError('Symlink evidence')
        if path.is_file():
            data = path.read_bytes()
            result[str(path.relative_to(ARTIFACTS))] = dict(
                sha256=digest(data), bytes=len(data))
    return result


def prepare() -> int:
    """Reserve a new directory even on failure; a partial run is never reusable."""
    OUTPUT.mkdir()  # Exclusive, before work; never mkdir(exist_ok=True).
    before = evidence()
    source_before = []
    for source, pin, pages in SOURCES:
        if source.is_symlink() or not source.is_file():
            raise ValueError('Invalid source')
        sha = digest(source.read_bytes())
        if sha != pin or page_count(source) != pages:
            raise ValueError('Source identity mismatch')
        source_before.append(sha)
    # Res137 must be an exact copy, not a needless Poppler re-extraction.
    with (OUTPUT / 'res137.pdf').open('xb') as stream:
        stream.write(SOURCES[0][0].read_bytes())
    ordered = ' '.join(f'/out/page-{n}.pdf' for n in PAGES)
    subprocess.run(docker_args() + ['--mount',
        f'type=bind,src={SOURCES[1][0]},dst=/source.pdf,readonly', '--mount',
        f'type=bind,src={OUTPUT},dst=/out', IMAGE, 'sh', '-c',
        'pdfseparate -f 1 -l 12 /source.pdf /out/page-%d.pdf && '
        'pdfseparate -f 15 -l 17 /source.pdf /out/page-%d.pdf && '
        f'pdfunite {ordered} /out/conv2026.pdf'],
        check=True, capture_output=True, timeout=15)
    payloads = []
    for index, (name, mapping) in enumerate((('res137.pdf', tuple(range(1, 21))),
                                            ('conv2026.pdf', PAGES))):
        source, pin, source_pages = SOURCES[index]
        pdf = OUTPUT / name
        data = pdf.read_bytes()
        count = page_count(pdf)
        if (not data.startswith(b'%PDF-') or not 0 < len(data) < 50_000_000
                or count != len(mapping)
                or (index == 0 and digest(data) != pin)):
            raise ValueError('Payload integrity mismatch')
        payloads.append(dict(file=name, sha256=digest(data), bytes=len(data),
            page_count=count, mapping={str(i): n for i, n in enumerate(mapping, 1)},
            source=str(source), source_page_count=source_pages,
            source_sha256_before=source_before[index],
            options={'formats': ['markdown'], 'timeout': 300000, 'parsers': [{
                'type': 'pdf', 'mode': 'ocr', 'maxPages': count, 'pages': True,
                'blocks': True, 'pageMarkers': True}]}))
    for entry, (source, pin, _) in zip(payloads, SOURCES):
        after = digest(source.read_bytes())
        if after != pin:
            raise ValueError('Source changed')
        entry['source_sha256_after'] = after
    after = evidence()
    if before != after:
        raise ValueError('Prior evidence changed')
    # The success record is last and uses the existing exclusive, fsynced writer.
    write_json(OUTPUT / 'preparation.json', dict(
        schema_version=1, state='prepared_offline_not_sent', image=IMAGE,
        payloads=payloads, estimated_credits=35, spent_by_preparation=0,
        historical_ceiling=100, evidence_before=before, evidence_after=after))
    print('Prepared offline: 35 pages; no POST.')
    return 0


def main() -> int:
    """Only prepare, fixed sources/output, no credentials or network operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare', help='Offline only: 20 Res137 + 15 convocatoria; '
                        'excludes original 13–14; exclusive directory, no retries')
    parser.parse_args()
    try:
        return prepare()
    except Exception as error:
        print(f'Preparation failed: {error_category(error)}; no POST; do not retry.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
