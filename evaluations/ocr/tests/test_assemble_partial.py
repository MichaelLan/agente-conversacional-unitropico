"""Offline partial assembly through its CLI; synthetic evidence only."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assemble_partial as app
from pilot import digest, write_json
from remaining_send import entry

class PartialTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = self.root / 'evaluations/ocr'
        self.execution = self.base / '.artifacts/remaining-ocr-v1/execution'
        self.output = self.base / '.artifacts/extraction-corpus-v2'
        specs = []
        for name, count in (('res137', 20), ('conv2026', 17)):
            path = self.root / 'documents' / (name + '.pdf')
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'%PDF-' + name.encode())
            sha = digest(path.read_bytes())
            specs.append((name, path, sha, sha, path.stat().st_size,
                          list(range(1, 21)) if count == 20 else list(range(1, 13)) + [15, 16, 17]))
        self.docs = []
        for i, count in enumerate((20, 17, 16, 2, 62, 75, 24)):
            source = specs[i][1] if i < 2 else self.root / f'documents/{i}.pdf'
            if i >= 2:
                source.write_bytes(b'%PDF-other')
            pages = [dict(physical_page=n, context_available=(i > 1 or (i == 1 and n in (13, 14))),
                **app.GUARDS, issues=[{'pilot': n}] if i == 1 and n in (13, 14) else [],
                evidence=[dict(method='pdftotext', page=dict(text='' if i < 2 else 'direct'))])
                for n in range(1, count + 1)]
            self.docs.append(dict(direct_document=dict(source_path=str(source.relative_to(self.root)),
                sha256_before=digest(source.read_bytes()), sha256_after=digest(source.read_bytes()),
                page_count=count), pages=pages, **app.GUARDS))
        self.previous = dict(schema_version=1, documents=self.docs, totals=app.OLD_TOTALS, **app.GUARDS)
        self.save('.artifacts/extraction-corpus-v1/documents.json', self.previous)
        self.save('.artifacts/extraction-corpus-v1/manifest.json', dict(state='complete_requires_review',
            documents_sha256=self.sha('.artifacts/extraction-corpus-v1/documents.json'), totals=app.OLD_TOTALS, **app.GUARDS))
        self.raw = dict(success=True, data=dict(markdown='global',
            pages=[dict(pageNumber=n, markdown=f'page {n}') for n in range(1, 21)],
            blocks=[dict(pageNumber=n, status='ok', items=[dict(id=str(n), content='' if n in (6, 14) else 'block', markdownSpan=[5, 9])]) for n in range(1, 21)],
            metadata=dict(numPages=20, totalPages=20, creditsUsed=20)))
        self.save('.artifacts/remaining-ocr-v1/execution/res137/response.raw', self.raw)
        lots = [dict(file=name+'.pdf', state='complete' if i == 0 else 'stopped_no_replay',
            http_status=200 if i == 0 else None, coverage=i == 0, credits=20 if i == 0 else None,
            category='none' if i == 0 else 'network_error') for i, name in enumerate(('res137', 'conv2026'))]
        for spec, lot in zip(specs, lots):
            self.save(f'.artifacts/remaining-ocr-v1/execution/{spec[0]}/attempt.json', dict(entry(spec), state='reserved_may_have_been_sent'))
            self.save(f'.artifacts/remaining-ocr-v1/execution/{spec[0]}/result.json', lot)
        self.save('.artifacts/remaining-ocr-v1/execution/result.json', dict(state='stopped_no_replay', known_credits=20, unknown_consumption=True, category='network_error', lots=lots))
        for name, value in dict(BASE=self.base, ROOT=self.root, SPECS=tuple(specs),
                PREVIOUS_SHA=self.sha('.artifacts/extraction-corpus-v1/documents.json'),
                RAW_SHA=self.sha('.artifacts/remaining-ocr-v1/execution/res137/response.raw'),
                RAW_BYTES=(self.execution / 'res137/response.raw').stat().st_size).items():
            self.enterContext(patch.object(app, name, value))
    def save(self, name, value):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    def sha(self, name):
        return digest((self.base / name).read_bytes())
    def invoke(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['assemble_partial.py']), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = app.main()
        return code, out.getvalue(), err.getvalue()
    def rejected(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.invoke(), (1, '', 'Partial assembly failed: invalid_input.\n'))
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertEqual(before, {p: p.read_bytes() for p in before})
    def test_mapping_preservation_pending_and_no_overwrite(self):
        before = copy.deepcopy(self.previous)
        self.assertEqual(self.invoke(), (0, app.SUCCESS + '\n', ''))
        data = json.loads((self.output / 'documents.json').read_bytes())
        self.assertEqual(data['totals'], dict(app.OLD_TOTALS, ocr_evidence=22, context_available=201, without_text=15))
        self.assertEqual(sum(len(d['pages']) for d in data['documents']), 216)
        for old, new in zip(before['documents'], data['documents']):
            self.assertEqual(old['direct_document'], new['direct_document'])
            for p, q in zip(old['pages'], new['pages']):
                self.assertEqual(q['evidence'][:len(p['evidence'])], p['evidence'])
                self.assertEqual(q['issues'][:len(p['issues'])], p['issues'])
                self.assertTrue(q['requires_review'] and q['publication_blocked'])
        for n, page in enumerate(data['documents'][0]['pages'], 1):
            evidence = page['evidence'][1]
            self.assertEqual(evidence['mapping'], dict(fragment_page=n, original_physical_page=n))
            self.assertEqual(evidence['page'], self.raw['data']['pages'][n-1])
            self.assertEqual(evidence['blocks'], self.raw['data']['blocks'][n-1])
            self.assertEqual(evidence['metadata'], self.raw['data']['metadata'])
            self.assertEqual(evidence['source_artifact_sha256'], app.RAW_SHA)
            if n in (6, 14):
                self.assertEqual(page['issues'][-1]['code'], 'block_content_incomplete')
                self.assertEqual(page['issues'][-1]['empty_content_items'], 1)
        self.assertEqual(data['ocr_response_data_res137'], self.raw['data'])
        self.assertEqual(data['batch']['conv2026_status'], 'pendiente_evidencia_consumo_remoto_desconocido')
        self.assertEqual(data['batch']['result']['known_credits'], 20)
        self.assertFalse((self.execution / 'conv2026/response.raw').exists())
        manifest = json.loads((self.output / 'manifest.json').read_bytes())
        self.assertEqual(manifest['documents_sha256'], digest((self.output / 'documents.json').read_bytes()))
        self.assertEqual(manifest['inputs_sha256_before'], manifest['inputs_sha256_after'])
        saved = {p: p.read_bytes() for p in self.output.iterdir()}
        self.assertEqual(self.invoke(), (1, '', 'Partial assembly failed: existing_artifact.\n'))
        self.assertEqual(saved, {p: p.read_bytes() for p in saved})
    def test_invalid_response(self):
        cases = [('success', False), ('success', 1), ('missing', None), ('duplicate', None),
                 ('credit', True), ('count', True), ('error', 'bad'), ('empty', ''), ('block', False)]
        for kind, value in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as out:
                raw = copy.deepcopy(self.raw)
                if kind == 'success': raw['success'] = value
                elif kind == 'missing': raw['data']['pages'].pop()
                elif kind == 'duplicate': raw['data']['pages'][1]['pageNumber'] = 1
                elif kind == 'credit': raw['data']['metadata']['creditsUsed'] = value
                elif kind == 'count': raw['data']['metadata']['numPages'] = value
                elif kind == 'error': raw['data']['error'] = value
                elif kind == 'empty': raw['data']['pages'][0]['markdown'] = value
                else: raw['data']['blocks'][0]['success'] = value
                self.save('.artifacts/remaining-ocr-v1/execution/res137/response.raw', raw)
                with patch.object(app, 'RAW_SHA', self.sha('.artifacts/remaining-ocr-v1/execution/res137/response.raw')), patch.object(app, 'RAW_BYTES', (self.execution / 'res137/response.raw').stat().st_size), patch.object(app, 'OUTPUT_NAME', str(Path(out) / 'reserved')):
                    self.rejected()
    def test_independent_hash_and_mapping(self):
        self.save('.artifacts/extraction-corpus-v1/documents.json', {})
        self.save('.artifacts/extraction-corpus-v1/manifest.json', dict(documents_sha256=self.sha('.artifacts/extraction-corpus-v1/documents.json')))
        self.rejected()
    def test_pinned_raw_mapping_state_and_paths(self):
        mutations = [('res137/attempt.json', 'mapping', {'1': 2}),
                     ('res137/attempt.json', 'source', '../wrong.pdf'),
                     ('res137/result.json', 'coverage', 1),
                     ('res137/result.json', 'state', 'stopped_no_replay'),
                     ('res137/result.json', 'credits', True)]
        for filename, key, value in mutations:
            path = self.execution / filename
            original = path.read_bytes()
            obj = json.loads(original)
            obj[key] = value
            path.write_text(json.dumps(obj))
            with self.subTest(key=key), tempfile.TemporaryDirectory() as out, patch.object(app, 'OUTPUT_NAME', str(Path(out) / 'reserved')):
                self.rejected()
            path.write_bytes(original)
    def test_raw_hash_duplicate_keys_and_symlink(self):
        path = self.execution / 'res137/response.raw'
        for raw in (b'{}', b'{"success":true,"success":true}'):
            path.write_bytes(raw)
            with tempfile.TemporaryDirectory() as out, patch.object(app, 'OUTPUT_NAME', str(Path(out) / 'reserved')):
                self.rejected()
        with tempfile.TemporaryDirectory() as out, patch.object(app, 'OUTPUT_NAME', str(Path(out) / 'reserved')), patch.object(app, 'RAW_SHA', digest(raw)), patch.object(app, 'RAW_BYTES', len(raw)):
            self.rejected()  # Duplicate keys rejected even with a matching fixture pin.
        source = app.SPECS[0][1]
        path.rename(path.with_name('fixture-saved.raw'))
        path.symlink_to(source)
        self.rejected()
    def test_manifest_failure_no_completion(self):
        def fail(path, value):
            if path.name == 'manifest.record.json': raise OSError('private')
            write_json(path, value)
        with patch.object(app, 'write_json', side_effect=fail):
            self.assertEqual(self.invoke(), (1, '', 'Partial assembly failed: local_io_error.\n'))
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertEqual(self.invoke(), (1, '', 'Partial assembly failed: existing_artifact.\n'))

if __name__ == '__main__':
    unittest.main()
