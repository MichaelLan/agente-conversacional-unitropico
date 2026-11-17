"""Public offline extraction contract; no real Docker or source text logged."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'pdfs'
        self.source.mkdir()
        documents = []
        for i in range(7):
            data = f'%PDF-fixture-{i}'.encode()
            path = self.source / f'{i}.pdf'
            path.write_bytes(data)
            documents.append(dict(file=path.name, bytes=len(data), pages=6,
                sha256_before=hashlib.sha256(data).hexdigest(),
                sha256_after=hashlib.sha256(data).hexdigest(),
                page_charcounts=[0, 1, 79, 80, 0, 0]))
        self.audit = self.root / 'audit.json'
        self.audit.write_text(json.dumps(dict(documents=documents)))
        self.output = self.root / 'exclusive'
        self.text = '\f é\r\n\f' + '界' * 79 + '\f  ' + 'a' * 80 + '\n\f \u2003\f\f'
        self.before = {p: p.read_bytes() for p in [self.audit, *self.source.iterdir()]}

    def invoke(self, text=None, error=None, mutate=None, pages=6):
        def run(args, **kwargs):
            self.assertTrue(self.output.is_dir())
            self.assertTrue(kwargs['check'])
            self.assertTrue(kwargs['capture_output'])
            self.assertGreater(kwargs['timeout'], 0)
            self.assertLessEqual(kwargs['timeout'], 60)
            for flag, value in (('--pull', 'never'), ('--network', 'none'),
                                ('--cap-drop', 'ALL'),
                                ('--security-opt', 'no-new-privileges')):
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertIn('--read-only', args)
            self.assertTrue(args[args.index('--mount') + 1].endswith(',readonly'))
            if error:
                raise error
            if 'pdfinfo' in args:
                return Mock(stdout=f'Pages: {pages}\n')
            self.assertEqual(args[-5:], ['pdftotext', '-enc', 'UTF-8', '/source.pdf', '-'])
            if mutate:
                mutate()
            value = self.text if text is None else text
            return Mock(stdout=value.encode() if isinstance(value, str) else value)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(extract.audit, 'SOURCE', self.source), \
                patch.object(extract, 'AUDIT', self.audit), \
                patch.object(extract, 'OUTPUT', self.output), \
                patch.object(subprocess, 'run', side_effect=run) as runner, \
                patch.object(sys, 'argv', ['extract.py']), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = extract.main()
        return code, out.getvalue(), err.getvalue(), runner.call_count

    def test_exact_text_physical_pages_provenance_and_no_overwrite(self):
        self.assertEqual(self.invoke(), (0, 'Direct extraction saved: 7 PDFs; requires review.\n', '', 14))
        result = json.loads((self.output / 'documents.json').read_text())
        manifest = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(manifest['state'], 'complete_requires_review')
        self.assertEqual(manifest['documents_sha256'], hashlib.sha256(
            (self.output / 'documents.json').read_bytes()).hexdigest())
        self.assertEqual(manifest['prior_artifacts'][str(self.audit)],
                         hashlib.sha256(self.before[self.audit]).hexdigest())
        self.assertEqual(result['totals'], dict(pages=42, pending_ocr=21,
            sparse_review=14, direct_candidate=7))
        for doc in result['documents']:
            source = self.source / doc['source_name']
            self.assertEqual(doc['source_path'], str(source))
            self.assertEqual(doc['id'], 'sha256:' + doc['sha256_before'])
            self.assertEqual(doc['sha256_before'], doc['sha256_after'])
            self.assertEqual(doc['bytes'], len(self.before[source]))
            self.assertEqual(doc['page_count'], 6)
            self.assertEqual([p['physical_page'] for p in doc['pages']], list(range(1, 7)))
            self.assertEqual([p['charcount'] for p in doc['pages']], [0, 1, 79, 80, 0, 0])
            self.assertEqual([p['status'] for p in doc['pages']],
                ['pending_ocr', 'sparse_review', 'sparse_review', 'direct_candidate',
                 'pending_ocr', 'pending_ocr'])
            self.assertEqual('\f'.join(p['text'] for p in doc['pages']) + '\f', self.text)
            self.assertTrue(all(p['requires_review'] and p['method'] == 'pdftotext'
                                for p in doc['pages']))
        self.assertEqual(self.before, {p: p.read_bytes() for p in self.before})
        saved = {p: p.read_bytes() for p in self.output.iterdir()}
        self.assertEqual(self.invoke(), (1, '', 'Extraction failed: existing_artifact.\n', 0))
        self.assertEqual(saved, {p: p.read_bytes() for p in saved})

    def test_invalid_text_boundaries_encoding_and_audit_mismatch(self):
        for i, text in enumerate(('bad', '\f' * 5, '\f' * 7, b'\xff\f',
                                  self.text.replace('界' * 79, 'z' * 78))):
            with self.subTest(case=i):
                self.output = self.root / f'bad-{i}'
                self.assertEqual(self.invoke(text=text)[:3],
                                 (1, '', 'Extraction failed: invalid_input.\n'))
                self.assertFalse((self.output / 'manifest.json').exists())
                self.assertEqual(self.before, {p: p.read_bytes() for p in self.before})

    def test_pdfinfo_count_mismatch(self):
        self.assertEqual(self.invoke(pages=7),
                         (1, '', 'Extraction failed: invalid_input.\n', 1))
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertEqual(self.before, {p: p.read_bytes() for p in self.before})

    def test_runner_failures_no_retry_or_complete_manifest(self):
        for i, (error, category) in enumerate((
                (subprocess.TimeoutExpired('PRIVATE', 60), 'poppler_timeout'),
                (subprocess.CalledProcessError(1, 'PRIVATE'), 'poppler_error'))):
            self.output = self.root / f'runner-{i}'
            self.assertEqual(self.invoke(error=error),
                             (1, '', f'Extraction failed: {category}.\n', 1))
            self.assertFalse((self.output / 'manifest.json').exists())
            self.assertEqual(self.before, {p: p.read_bytes() for p in self.before})
            self.assertEqual(self.invoke(),
                             (1, '', 'Extraction failed: existing_artifact.\n', 0))

    def test_changes_to_source_or_prior_audit_refuse_completion(self):
        for i, path in enumerate((self.source / '0.pdf', self.audit)):
            self.output = self.root / f'mutation-{i}'
            original = path.read_bytes()
            self.assertEqual(self.invoke(mutate=lambda: path.write_bytes(b'changed'))[:3],
                             (1, '', 'Extraction failed: invalid_input.\n'))
            self.assertFalse((self.output / 'manifest.json').exists())
            path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
