"""One-shot 35-page sender: no retries, polling, resume or OCR quality claims."""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.dont_write_bytecode = True
from pilot import (ARTIFACTS, ORIGINAL, IMAGE, ENDPOINT, NoRedirect, digest,
                   load_key, write_json, log_phase, error_category)

ROOT = ARTIFACTS / 'remaining-ocr-v1'
EXECUTION_NAME = 'execution'
SPECS = (
    ('res137', ORIGINAL.parent / 'Res00137ReglamentaRentaJoven.pdf',
     'c855bd4b0f33947857778afc9271250e9c7fe78b6fdadf890dd3b2b56920e362',
     'c855bd4b0f33947857778afc9271250e9c7fe78b6fdadf890dd3b2b56920e362',
     19579889, list(range(1, 21))),
    ('conv2026', ORIGINAL,
     '3ebfd85b26199382c759240321622a1009b791e16dd895f4f7ea34428da2aa26',
     '2bc87c7392c170bd3546e5ed7bd8754aeaaecd2138f6d9ca6b33bef9e055dc79',
     7048730, list(range(1, 13)) + [15, 16, 17]),
)


def entry(spec):
    """Build the exact approved identity and OCR options from independent pins."""
    name, source, source_sha, sha, size, mapping = spec
    return dict(file=name + '.pdf', source=str(source), source_sha256_before=source_sha,
        source_sha256_after=source_sha, source_page_count=20 if name == 'res137' else 17,
        sha256=sha, bytes=size, page_count=len(mapping),
        mapping={str(i): n for i, n in enumerate(mapping, 1)},
        options={'formats': ['markdown'], 'timeout': 300000, 'parsers': [{
            'type': 'pdf', 'mode': 'ocr', 'maxPages': len(mapping), 'pages': True,
            'blocks': True, 'pageMarkers': True}]})


def safe(path):
    """Refuse symlinks in the fixed path and every ancestor."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink refused')
    return path


def unique(pairs):
    """Reject ambiguous JSON objects instead of silently accepting duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key')
        result[key] = value
    return result


def decode(raw):
    """Decode JSON without permitting duplicate identity or status fields."""
    return json.loads(raw, object_pairs_hook=unique)


def preflight():
    """Pin BOTH lots to Docker-counted PDFs (reports/remaining-preparation.md)."""
    plan = decode(safe(ROOT / 'preparation.json').read_bytes())
    expected = dict(schema_version=1, state='prepared_offline_not_sent', image=IMAGE,
                    estimated_credits=35, spent_by_preparation=0, historical_ceiling=100,
                    payloads=[entry(spec) for spec in SPECS])
    # Canonical JSON comparison distinguishes booleans from numeric identities.
    actual = {k: plan[k] for k in expected}
    if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError('Plan mismatch')
    payloads = []
    for spec, item in zip(SPECS, expected['payloads']):
        source = safe(spec[1])
        if digest(source.read_bytes()) != spec[2]:
            raise ValueError('Source mismatch')
        pdf = safe(ROOT / item['file'])
        data = pdf.read_bytes()
        if digest(data) != spec[3] or len(data) != spec[4] or not data.startswith(b'%PDF-'):
            raise ValueError('Payload mismatch')
        payloads.append((item, data))
    return payloads


def sync_dir(path):
    """Make exclusive directory entries durable before any network operation."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record(path, value):
    """Reuse the exclusive record writer, then persist its directory entry."""
    write_json(path, value)  # Existing exclusive, flushed, fsynced record writer.
    sync_dir(path.parent)


def coverage(value, n):
    """Require complete groups and mutually consistent declared page counts."""
    data = value['data']
    for field in ('pages', 'blocks'):
        groups = data[field]
        numbers = [g['pageNumber'] for g in groups]
        if (len(groups) != n or any(type(i) is not int for i in numbers)
                or sorted(numbers) != list(range(1, n + 1))):
            raise ValueError('Coverage mismatch')
        for group in groups:
            if (group.get('success') is False or group.get('error')
                    or ('status' in group and group['status'] not in ('ok', 'completed', 'success'))):
                raise ValueError('Group failed')
            if field == 'pages' and not isinstance(group['markdown'], str):
                raise ValueError('Markdown unavailable')
            if field == 'blocks':
                if not isinstance(group['items'], list) or any(not isinstance(b, dict) for b in group['items']):
                    raise ValueError('Invalid blocks')
                for block in group['items']:
                    if 'pageNumber' in block and block['pageNumber'] != group['pageNumber']:
                        raise ValueError('Block mismatch')
    metadata = data['metadata']
    for key in ('pageCount', 'numPages', 'totalPages'):
        if key in metadata and (type(metadata[key]) is not int or metadata[key] != n):
            raise ValueError('Metadata mismatch')


def send(key, transport=None):
    """Fixed paths, at most two POSTs; any execution directory blocks replay."""
    started = time.monotonic()
    reserved = False
    result = dict(state='stopped_no_replay', known_credits=0, unknown_consumption=False, lots=[])
    code = 1
    try:
        execution = safe(ROOT / EXECUTION_NAME)
        if execution.exists():
            raise FileExistsError('No replay')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key):
            raise ValueError('Invalid credential')
        log_phase('preflight_started', started)
        payloads = preflight()
        log_phase('preflight_validated', started)
        execution.mkdir()  # Exclusive even after failures; NEVER exist_ok.
        reserved = True
        sync_dir(execution.parent)
        record(execution / 'batch.json', dict(state='reserved_no_replay', estimated_credits=35))
        log_phase('batch_reserved', started)
        if transport is None:
            transport = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open
        for item, pdf in payloads:
            folder = execution / item['file'][:-4]
            folder.mkdir()
            sync_dir(execution)
            boundary = 'remaining-' + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="options"\r\n'
                    'Content-Type: application/json\r\n\r\n' + json.dumps(item['options']) +
                    f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                    f'filename="{item["file"]}"\r\nContent-Type: application/pdf\r\n\r\n').encode()
            request = urllib.request.Request(ENDPOINT, method='POST',
                data=body + pdf + f'\r\n--{boundary}--\r\n'.encode(), headers={
                'Authorization': 'Bearer ' + key, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
            log_phase('ledger_started', started)
            record(folder / 'attempt.json', dict(item, state='reserved_may_have_been_sent'))
            log_phase('ledger_reserved', started)
            lot = dict(file=item['file'], http_status=None, coverage=False, credits=None,
                       category='unknown', state='stopped_no_replay')
            result['lots'].append(lot)
            result['unknown_consumption'] = True
            try:
                log_phase('http_started', started)
                try:
                    response = transport(request, timeout=330)
                except urllib.error.HTTPError as error:
                    response = error
                with response, (folder / 'response.raw').open('xb') as stream:
                    status = response.status
                    lot['http_status'] = status if type(status) is int else None
                    log_phase('http_status', started, status=lot['http_status'])
                    try:
                        raw = response.read()
                        stream.write(raw)
                    except http.client.IncompleteRead as error:
                        stream.write(error.partial)
                        raise
                    finally:
                        stream.flush()
                        os.fsync(stream.fileno())
                    length = response.headers.get('Content-Length')
                    if length is not None and int(length) != len(raw):
                        raise ValueError('Incomplete response')
                sync_dir(folder)
                log_phase('raw_saved', started, status=lot['http_status'])
                value = decode(raw)
                credits = value['data']['metadata']['creditsUsed']
                if type(credits) is not int or not 0 <= credits <= item['page_count']:
                    raise ValueError('Unknown credits')
                lot['credits'] = credits
                result['known_credits'] += credits
                result['unknown_consumption'] = False
                if (status != 200 or value['success'] is not True or result['known_credits'] > 35
                        or any('status' in obj and obj['status'] not in ('completed', 'success')
                               for obj in (value, value['data'], value['data']['metadata']))):
                    raise ValueError('Unsuccessful response')
                coverage(value, item['page_count'])
                lot.update(coverage=True, category='none', state='complete')
            except (Exception, KeyboardInterrupt) as error:
                lot['category'] = error_category(error)
                raise
            finally:
                interrupted = sys.exc_info()[0] is KeyboardInterrupt
                try:
                    record(folder / 'result.json', lot)
                    log_phase('result_saved', started, status=lot['http_status'])
                except Exception as error:
                    log_phase('result_save_failed', started, category=error_category(error))
                    if not interrupted:
                        raise
        result['state'] = 'complete'
        code = 0
    except (Exception, KeyboardInterrupt) as error:
        result['category'] = error_category(error)
        log_phase('stopped', started, category=result['category'])
        if isinstance(error, KeyboardInterrupt):
            raise
    finally:
        if reserved:
            try:
                record(execution / 'result.json', result)
            except Exception as error:
                code = 1
                log_phase('result_save_failed', started, category=error_category(error))
        log_phase('finished', started)
        print('Cobertura/disponibilidad y créditos solo según payload; no calidad OCR. '
              'Consumo incierto si no declarado; no reenviar. Ver registros locales.', file=sys.stderr, flush=True)
    return code


def main():
    """Expose standalone offline checks or the exact manually confirmed send."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Offline pinned identities (verified 20/15 pages); no Docker, key, reservation or POST')
    parser.add_argument('--confirm-thirty-five-credits', action='store_true', help='35 pages estimated 35 credits; max two POSTs, no replay')
    parser.add_argument('--env-file', type=Path, help='Explicit dotenv read by pilot.load_key, never shell evaluated')
    args = parser.parse_args()
    if args.check and (args.env_file or args.confirm_thirty_five_credits):
        parser.error('--check is standalone')
    if not args.check and (not args.confirm_thirty_five_credits or args.env_file != Path('.env')):
        parser.error('send requires --confirm-thirty-five-credits --env-file .env')
    started = time.monotonic()
    try:
        if args.check:
            preflight()
            log_phase('offline_check_validated', started)
            return 0
        log_phase('credential_load_started', started)
        key = load_key(args.env_file)
        log_phase('credential_loaded', started)
        return send(key)
    except KeyboardInterrupt:
        log_phase('interrupted', started, category='interrupted')
        return 130
    except Exception as error:
        log_phase('stopped', started, category=error_category(error))
        return 1


if __name__ == '__main__':
    sys.exit(main())
