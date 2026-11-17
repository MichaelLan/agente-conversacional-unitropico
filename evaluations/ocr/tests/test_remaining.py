"""Offline preparation contract; no real Docker or source documents."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import remaining as r


class RemainingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.art = self.root / 'artifacts'
        self.art.mkdir()
        (self.art / 'prior.json').write_bytes(b'prior evidence')
        self.out = self.art / 'remaining-ocr-v1'
        self.sources = [self.root / 'res.pdf', self.root / 'conv.pdf']
        for i, source in enumerate(self.sources):
            source.write_bytes(b'%PDF-source-' + str(i).encode())
        self.pins = tuple((p, r.digest(p.read_bytes()), n)
                          for p, n in zip(self.sources, (20, 17)))
        for name, value in [('SOURCES', self.pins), ('ARTIFACTS', self.art),
                            ('OUTPUT', self.out)]:
            p = patch.object(r, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.commands = []
        self.counter = patch.object(r, 'page_count', side_effect=lambda p:
            20 if p in (self.sources[0], self.out / 'res137.pdf') else
            17 if p == self.sources[1] else 15).start()
        self.addCleanup(patch.stopall)
        self.run = patch.object(r.subprocess, 'run', side_effect=self.extract).start()

    def extract(self, args, **kwargs):
        self.commands.append(args)
        self.assertEqual(kwargs, dict(check=True, capture_output=True, timeout=15))
        self.assertTrue(self.out.is_dir())
        for flag, value in [('--pull', 'never'), ('--network', 'none'),
                            ('--cap-drop', 'ALL'), ('--security-opt', 'no-new-privileges')]:
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn('--read-only', args)
        self.assertIn(f'type=bind,src={self.sources[1]},dst=/source.pdf,readonly', args)
        expected = ('pdfseparate -f 1 -l 12 /source.pdf /out/page-%d.pdf && '
                    'pdfseparate -f 15 -l 17 /source.pdf /out/page-%d.pdf && pdfunite ' +
                    ' '.join(f'/out/page-{n}.pdf' for n in list(range(1, 13)) + [15, 16, 17]) +
                    ' /out/conv2026.pdf')
        self.assertEqual(args[-1], expected)
        (self.out / 'conv2026.pdf').write_bytes(b'%PDF-fifteen-pages')

    def invoke(self, expected, category=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['remaining.py', 'prepare']), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(r.main(), expected)
        self.assertEqual(stdout.getvalue(), 'Prepared offline: 35 pages; no POST.\n' if expected == 0 else '')
        self.assertEqual(stderr.getvalue(), '' if expected == 0 else
                         f'Preparation failed: {category}; no POST; do not retry.\n')

    def test_exact_payloads_mapping_and_provenance(self):
        self.invoke(0)
        self.assertEqual(self.run.call_count, 1)
        result = json.loads((self.out / 'preparation.json').read_text())
        self.assertEqual(result['state'], 'prepared_offline_not_sent')
        self.assertEqual(result['estimated_credits'], 35)
        self.assertEqual(result['historical_ceiling'], 100)
        self.assertEqual(result['spent_by_preparation'], 0)
        for entry, (source, sha, pages), mapping in zip(result['payloads'], self.pins,
                [list(range(1, 21)), list(range(1, 13)) + [15, 16, 17]]):
            data = (self.out / entry['file']).read_bytes()
            self.assertEqual(entry['mapping'], {str(i): n for i, n in enumerate(mapping, 1)})
            self.assertEqual(entry['page_count'], len(mapping))
            self.assertEqual(entry['source'], str(source))
            self.assertEqual(entry['source_page_count'], pages)
            self.assertEqual(entry['source_sha256_before'], sha)
            self.assertEqual(entry['source_sha256_after'], sha)
            self.assertEqual(entry['sha256'], r.digest(data))
            self.assertEqual(entry['bytes'], len(data))
            self.assertEqual(entry['options']['parsers'][0]['maxPages'], len(mapping))
        self.assertEqual((self.out / 'res137.pdf').read_bytes(), self.sources[0].read_bytes())
        self.assertEqual(result['evidence_before'], result['evidence_after'])
        self.assertIn('prior.json', result['evidence_before'])
        self.assertEqual((self.art / 'prior.json').read_bytes(), b'prior evidence')
        self.assertFalse((self.out / 'page-13.pdf').exists())
        self.assertFalse((self.out / 'page-14.pdf').exists())

    def test_source_hash_mismatch(self):
        self.sources[0].write_bytes(b'changed')
        self.invoke(1, 'invalid_input')
        self.run.assert_not_called()
        self.assertFalse((self.out / 'preparation.json').exists())

    def test_source_count_mismatch(self):
        self.counter.side_effect = [20, 18]
        self.invoke(1, 'invalid_input')
        self.run.assert_not_called()
        self.assertFalse((self.out / 'preparation.json').exists())

    def test_existing_directory_never_overwritten(self):
        self.out.mkdir()
        sentinel = self.out / 'partial.pdf'
        sentinel.write_bytes(b'partial')
        self.invoke(1, 'existing_artifact')
        self.assertEqual(list(self.out.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'partial')
        self.run.assert_not_called()
        self.counter.assert_not_called()

    def test_partial_timeout_never_success_or_retried(self):
        self.run.side_effect = subprocess.TimeoutExpired('private', 15)
        self.invoke(1, 'poppler_timeout')
        self.assertFalse((self.out / 'preparation.json').exists())
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        self.invoke(1, 'existing_artifact')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})
        self.assertEqual(self.run.call_count, 1)

    def test_integrity_failures_leave_no_success_record(self):
        for target in ['source', 'evidence', 'payload', 'count']:
            with self.subTest(target=target):
                # Separate output for each isolated invocation, not a retry.
                with tempfile.TemporaryDirectory() as td, patch.object(r, 'OUTPUT', Path(td) / 'new'):
                    def corrupt(args, **kwargs):
                        out = r.OUTPUT
                        (out / 'conv2026.pdf').write_bytes(b'%PDF-fragment' if target != 'payload' else b'bad')
                        if target == 'source':
                            self.sources[1].write_bytes(b'changed')
                        if target == 'evidence':
                            (self.art / 'prior.json').write_bytes(b'changed')
                    self.run.side_effect = corrupt
                    self.counter.side_effect = [20, 17, 20, 14 if target == 'count' else 15]
                    self.invoke(1, 'invalid_input')
                    self.assertFalse((r.OUTPUT / 'preparation.json').exists())
                self.sources[1].write_bytes(b'%PDF-source-1')
                (self.art / 'prior.json').write_bytes(b'prior evidence')

    def test_cli_rejects_send_and_arbitrary_paths(self):
        for args in [['send'], ['prepare', '--output', '/tmp/other']]:
            with patch.object(sys, 'argv', ['remaining.py'] + args), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                r.main()
            self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.out.exists())
