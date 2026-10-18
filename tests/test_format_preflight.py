"""Admission must depend on current artifacts, not unverified accepted JSON."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import fitz
from scripts.format_preflight import (
    DEFAULT_TOOLS, TEMPLATE_FILES, check_template_ready, load_format_profile,
    validate_preflight_scope, render_configuration, render_environment,
)

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FormatPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.template = self.root / 'template'
        self.template.mkdir()
        for name in TEMPLATE_FILES:
            (self.template / name).write_text('real template ' + name)
        self.evidence = self.root / 'source.png'
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
        pix.clear_with(255)
        pix.save(self.evidence)
        self.pdf = self.root / 'compiled.pdf'
        with fitz.open() as document:
            document.new_page().insert_text((50, 50), 'real sample')
            document.save(self.pdf)
        self.source = 'a' * 64
        self.profile_path = self.root / 'format.json'
        self.ready_path = self.root / 'ready.json'
        self.profile = {'schema_version': 1, 'status': 'frozen', 'version': 'sample-v1',
                        'source_sha256': self.source, 'template_dir': 'template',
                        'representative_pages': [27], 'tested_families': ['paragraph', 'code'],
                        'template_rules': {'code': {'font_family': 'Latin Modern Mono', 'font_size_pt': 10},
                                           'descriptive_field': 'never automatically executed'}}
        self.write(self.profile_path, self.profile)
        self.ready = {'schema_version': 1, 'status': 'accepted', 'source_sha256': self.source,
                      'profile_sha256': digest(self.profile_path), 'template_dir': 'template',
                      'render_configuration': render_configuration(self.profile),
                      'render_environment': render_environment(self.profile),
                      'template_files': {n: digest(self.template / n) for n in TEMPLATE_FILES},
                      'tool_sha256': {n: digest(ROOT / 'scripts' / n) for n in DEFAULT_TOOLS},
                      'compiled_pdf': {'path': 'compiled.pdf', 'sha256': digest(self.pdf)},
                      'checks': [{'name': n, 'status': 'pass'} for n in ['compile', 'source_fidelity']] +
                                [{'name': 'family_coverage', 'status': 'pass', 'families': ['paragraph', 'code']}],
                      'source_checks': [{'page': 27, 'output_page': 1, 'status': 'pass', 'reviewer': 'coordinator',
                                         'evidence': {'path': 'source.png', 'sha256': digest(self.evidence)}}]}
        self.write(self.ready_path, self.ready)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value))

    def check(self):
        return check_template_ready(self.profile_path, self.ready_path, self.source)

    def test_accept_real_artifacts_and_expose_supported_hooks(self):
        metadata = self.check()
        self.assertEqual(metadata['status'], 'accepted')
        self.assertEqual(metadata['compiled_pdf']['sha256'], digest(self.pdf))
        profile = load_format_profile(self.profile_path, self.source)
        self.assertEqual(profile['supported_template_rules']['code']['font_size_pt'], 10)
        self.assertNotIn('descriptive_field', profile['supported_template_rules'])

    def test_template_pdf_source_evidence_and_profile_tampering(self):
        for path in [self.template / TEMPLATE_FILES[0], self.pdf, self.evidence, self.profile_path]:
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.check()
            path.write_bytes(original)

    def test_reject_unknown_and_incomplete_admission(self):
        variants = []
        for key in ['compiled_pdf', 'source_checks', 'tool_sha256', 'checks']:
            ready = copy.deepcopy(self.ready)
            ready.pop(key)
            variants.append(ready)
        ready = copy.deepcopy(self.ready); ready['status'] = 'unknown'; variants.append(ready)
        ready = copy.deepcopy(self.ready); ready['checks'][0]['status'] = 'unknown'; variants.append(ready)
        ready = copy.deepcopy(self.ready); ready['checks'][2]['families'] = ['paragraph']; variants.append(ready)
        ready = copy.deepcopy(self.ready); ready['source_checks'][0]['output_page'] = 2; variants.append(ready)
        ready = copy.deepcopy(self.ready); ready['source_checks'][0]['page'] = 28; variants.append(ready)
        ready = copy.deepcopy(self.ready); ready['unresolved_issues'] = ['known loss']; variants.append(ready)
        ready = copy.deepcopy(self.ready); ready['tool_sha256']['semantic_contract.py'] = '0' * 64; variants.append(ready)
        for ready in variants:
            self.write(self.ready_path, ready)
            with self.subTest(ready=ready), self.assertRaises(ValueError):
                self.check()

    def test_fake_pdf_even_with_matching_hash_is_rejected(self):
        self.pdf.write_text('self reported compiled result')
        self.ready['compiled_pdf']['sha256'] = digest(self.pdf)
        self.write(self.ready_path, self.ready)
        with self.assertRaisesRegex(ValueError, 'real PDF'):
            self.check()

    def test_profile_source_and_unknown_version(self):
        with self.assertRaisesRegex(ValueError, 'source hash'):
            load_format_profile(self.profile_path, 'b' * 64)
        self.profile['schema_version'] = 2
        self.write(self.profile_path, self.profile)
        with self.assertRaisesRegex(ValueError, 'schema_version'):
            self.check()

    def test_sample_scope_limit_uses_count_not_book_page_numbers(self):
        validate_preflight_scope([301, 310, 400])
        validate_preflight_scope(list(range(101, 109)))
        with self.assertRaises(ValueError):
            validate_preflight_scope(list(range(101, 110)))

    def test_preparation_without_ready_succeeds_and_records_diagnostic(self):
        prepared = self.root / 'prepared'
        image_result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_images.py'),
                                       '--pdf', str(self.pdf), '--pages', '1', '--output', str(prepared),
                                       '--dpi', '24', '--max-pixels', '1000000'], capture_output=True, text=True)
        self.assertEqual(image_result.returncode, 0, image_result.stderr)
        jobs = self.root / 'prepared-jobs'
        arguments = [sys.executable, str(ROOT / 'scripts/run_page_jobs.py'), '--prepared-dir', str(prepared),
                     '--source-pdf', str(self.pdf), '--expected-pages', '1', '--output', str(jobs),
                     '--backend', 'codex', '--model', 'test', '--mode', 'typeset']
        result = subprocess.run(arguments, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        summary = json.loads((jobs / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'prepared')
        self.assertFalse(summary['production'])
        self.assertIn('diagnostic', summary['format_admission'])
        self.assertEqual(summary['model_invocations'], 0)

    def test_preflight_executes_small_sample_without_ready_and_stays_nonproduction(self):
        prepared = self.root / 'sample-images'
        image_result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_images.py'),
                                       '--pdf', str(self.pdf), '--pages', '1', '--output', str(prepared),
                                       '--dpi', '24', '--max-pixels', '1000000'], capture_output=True, text=True)
        self.assertEqual(image_result.returncode, 0, image_result.stderr)
        self.profile.update(source_sha256=digest(self.pdf), representative_pages=[1], status='draft')
        self.write(self.profile_path, self.profile)
        backend = self.root / 'fake-codex'
        backend.write_text('#!' + sys.executable + '\n' +
                           "import json, pathlib, sys\n"
                           "prompt = sys.stdin.read()\n"
                           "assert 'sample-v1' not in prompt\n"
                           "manifest = json.loads(pathlib.Path('input/manifest.json').read_text())\n"
                           "page = manifest['pages'][0]\n"
                           "output = {'page':1,'printed_page':None,'blocks':[{'id':'p0001-b001','type':'paragraph','text':'sample','bbox':[1,1,20,20],'styles':[],'evidence':'unreviewed'}],'uncertainties':[],'changes':[],'viewed_images':[i['path'] for i in page['images']]}\n"
                           "pathlib.Path('output/0001.json').write_text(json.dumps(output))\n")
        backend.chmod(0o755)
        caps = self.root/'capabilities.json'
        self.write(caps, dict(schema_version=1,backend='codex',model='test',input_modalities=['image'],output_modalities=['text'],tools=True,images={}))
        jobs = self.root / 'sample-jobs'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/run_page_jobs.py'),
                                 '--prepared-dir', str(prepared), '--source-pdf', str(self.pdf),
                                 '--expected-pages', '1', '--output', str(jobs), '--backend', 'codex',
                                 '--codex', str(backend), '--model', 'test', '--execute', '--mode', 'typeset',
                                 '--format-profile', str(self.profile_path), '--preflight-mode', '--capabilities',str(caps)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads((jobs / 'summary.json').read_text())
        self.assertTrue(summary['preflight_mode'])
        self.assertFalse(summary['production'])
        self.assertEqual(summary['validated_pages'], [1])
        self.assertEqual(summary['model_invocations'], 1)
        self.assertEqual(summary['format_admission']['status'], 'profile_validated')

    def test_execute_fails_before_model_call_without_admission(self):
        arguments = [sys.executable, str(ROOT / 'scripts/run_page_jobs.py'), '--prepared-dir', str(self.root),
                     '--source-pdf', str(self.pdf), '--expected-pages', '1', '--output', str(self.root / 'jobs'),
                     '--backend', 'codex', '--model', 'test', '--execute', '--mode', 'typeset']
        result = subprocess.run(arguments, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--format-profile', result.stderr)
        self.assertFalse((self.root / 'jobs').exists())
        result = subprocess.run(arguments + ['--pages', '1-9', '--expected-pages', '1-9', '--preflight-mode'],
                                capture_output=True, text=True)
        self.assertIn('at most 8', result.stderr)


if __name__ == '__main__':
    unittest.main()
