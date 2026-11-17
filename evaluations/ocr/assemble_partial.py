"""Offline fixed-source partial corpus: no send, replay, correction or publication."""
import argparse
import copy
import json
import os
import sys
from assemble import BASE, ROOT, GUARDS, safe, require
from pilot import digest, write_json, error_category
from remaining_send import SPECS, entry, decode, coverage

PREVIOUS_SHA = '104160c01f34dff520fb67a28d0dd004f6866ccc3a1184704f59861df9dbbc1f'
RAW_SHA = 'f75e036f39f8c4f76dc595032df702d746d3a7c3499f13777db2aa411c89d612'
RAW_BYTES = 241580
OUTPUT_NAME = '.artifacts/extraction-corpus-v2'
OLD_TOTALS = dict(documents=7, pages=216, direct_text=179, ocr_evidence=2,
                  context_available=181, without_text=35)
SUCCESS = 'Partial corpus assembled: 7 documents; 216 pages; context 201; pending 15; publication blocked.'


def exact(actual, expected):
    require(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True))


def assemble():
    output = safe(BASE / OUTPUT_NAME)
    output.mkdir()  # Failed reservations block reuse too.
    inputs = {}

    def load(path, sha=None, size=None, binary=False):
        raw = safe(path).read_bytes()
        require((sha is None or digest(raw) == sha) and (size is None or len(raw) == size))
        inputs[path] = digest(raw)
        return raw if binary else decode(raw)

    previous_path = BASE / '.artifacts/extraction-corpus-v1/documents.json'
    previous = load(previous_path, PREVIOUS_SHA)
    manifest = load(previous_path.with_name('manifest.json'))
    require(manifest['state'] == 'complete_requires_review' and manifest['documents_sha256'] == PREVIOUS_SHA)
    for obj in (previous, manifest):
        exact({k: obj[k] for k in GUARDS}, GUARDS)
        exact(obj['totals'], OLD_TOTALS)
    require(len(previous['documents']) == 7)
    data = copy.deepcopy(previous)
    docs = data['documents']
    paths = [d['direct_document']['source_path'] for d in docs]
    require(len(set(paths)) == 7)
    require(sum(len(d['pages']) for d in docs) == 216)
    for doc in docs:
        exact({k: doc[k] for k in GUARDS}, GUARDS)
        require([p['physical_page'] for p in doc['pages']] == list(range(1, len(doc['pages']) + 1)))
        for page in doc['pages']:
            exact({k: page[k] for k in GUARDS}, GUARDS)
    execution = BASE / '.artifacts/remaining-ocr-v1/execution'
    batch = load(execution / 'result.json')
    lots, attempts = [], []
    for spec in SPECS:
        name, source, source_sha, _, _, mapping = spec
        attempt = load(execution / name / 'attempt.json')
        exact(attempt, dict(entry(spec), state='reserved_may_have_been_sent'))
        load(source, source_sha, binary=True)
        result = load(execution / name / 'result.json')
        lots.append(result)
        attempts.append(attempt)
        matched = [d for d in docs if d['direct_document']['source_path'] == str(source.relative_to(ROOT))]
        require(len(matched) == 1)
        identity = matched[0]['direct_document']
        require(identity['sha256_before'] == identity['sha256_after'] == source_sha)
        require(identity['page_count'] == len(matched[0]['pages']) == attempt['source_page_count'])
        require(all(not matched[0]['pages'][n-1]['context_available'] for n in mapping))
    expected_lots = [dict(file='res137.pdf', http_status=200, coverage=True, credits=20,
        category='none', state='complete'), dict(file='conv2026.pdf', http_status=None,
        coverage=False, credits=None, category='network_error', state='stopped_no_replay')]
    exact(lots, expected_lots)
    exact(batch, dict(state='stopped_no_replay', known_credits=20, unknown_consumption=True,
                      category='network_error', lots=expected_lots))
    absent = safe(execution / 'conv2026/response.raw')
    require(not absent.exists())  # Absent is not an empty response or remote failure.
    raw_path = execution / 'res137/response.raw'
    raw = load(raw_path, RAW_SHA, RAW_BYTES)
    require(raw['success'] is True)
    ocr = raw['data']
    for obj in (raw, ocr, ocr['metadata']):
        require(not obj.get('error') and obj.get('success', True) is True
                and obj.get('status', 'success') in ('completed', 'success'))
    coverage(raw, 20)
    for key in ('numPages', 'totalPages', 'creditsUsed'):
        require(type(ocr['metadata'][key]) is int and ocr['metadata'][key] == 20)
    require(isinstance(ocr['markdown'], str) and bool(ocr['markdown'].strip()))
    target = next(d for d in docs if d['direct_document']['source_path'] == str(SPECS[0][1].relative_to(ROOT)))
    pages = {p['pageNumber']: p for p in ocr['pages']}
    blocks = {p['pageNumber']: p for p in ocr['blocks']}
    for n in range(1, 21):
        page, group = pages[n], blocks[n]
        require(bool(page['markdown'].strip()) and group['status'] == 'ok' and bool(group['items']))
        require(len({b['id'] for b in group['items']}) == len(group['items']))
        record = target['pages'][n-1]
        require(all(not e['page']['text'].strip() for e in record['evidence']))
        record['context_available'] = True
        record['evidence'].append(dict(method='firecrawl_parse_ocr', faithful_transcription=False,
            source_artifact=str(raw_path.relative_to(BASE)), source_artifact_sha256=RAW_SHA,
            mapping=dict(fragment_page=n, original_physical_page=n), page=page, blocks=group,
            metadata=ocr['metadata'], attempt=attempts[0], result=lots[0]))
        empty = sum(not isinstance(b.get('content'), str) or not b['content'].strip() for b in group['items'])
        if empty:
            record['issues'].append(dict(code='block_content_incomplete', empty_content_items=empty,
                total_items=len(group['items']), correction_applied=False))
    data['ocr_response_data_res137'] = ocr
    data['batch'] = dict(state='partial_requires_review', result=batch, attempts=attempts,
        conv2026_status='pendiente_evidencia_consumo_remoto_desconocido', pending_physical_pages=SPECS[1][5],
        prior_pilot_declared_credits=2, first_pilot_consumption='unknown', billing_total='unknown')
    data['totals'] = dict(OLD_TOTALS, ocr_evidence=22, context_available=201, without_text=15)
    require(sum(p['context_available'] for d in docs for p in d['pages']) == 201)
    after = {p: digest(safe(p).read_bytes()) for p in inputs}
    require(inputs == after and not safe(absent).exists())
    write_json(output / 'documents.json', data)
    completion = dict(schema_version=1, state='partial_requires_review', **GUARDS, totals=data['totals'],
        documents_sha256=digest((output / 'documents.json').read_bytes()),
        inputs_sha256_before={str(p.relative_to(ROOT)): sha for p, sha in inputs.items()},
        inputs_sha256_after={str(p.relative_to(ROOT)): sha for p, sha in after.items()},
        absent_inputs=[str(absent.relative_to(ROOT))])
    write_json(output / 'manifest.record.json', completion)
    os.link(output / 'manifest.record.json', output / 'manifest.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='Fixed pinned evidence only; existing output, including failed reservations, blocks reuse. Partial 20/35 only; 15 pending with unknown remote consumption. No network or retries; requires review, publication blocked.')
    parser.parse_args()
    try:
        assemble()
    except Exception as error:
        print(f'Partial assembly failed: {error_category(error)}.', file=sys.stderr)
        return 1
    print(SUCCESS)
    return 0


if __name__ == '__main__':
    sys.exit(main())
