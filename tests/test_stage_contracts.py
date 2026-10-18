"""Stage boundaries must reject stale artifacts without making new model calls."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from backend_capabilities import load_capabilities, check_images
from runtime_contract import tool_closure
from import_drafts import import_drafts
from run_page_jobs import verify_draft
from reuse_artifacts import adopt_page
from collect_usage import collect, normalize
from evaluate_quality import evaluate
from table_data import table_rows, structured_rows
from export_latex import prepare_render_block
from export_text import markdown
from source_identity import file_hash, source_digest
import test_audit_regressions as historical


class StageContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def cli(self, name, *args):
        return subprocess.run([sys.executable, str(ROOT/'scripts'/name), *map(str, args)], capture_output=True, text=True)

    def queue(self):
        return historical.AuditRegressions.prepare_page_queue(self, count=1)

    def write(self, name, value):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_model_execution_requires_vision_and_tools_declaration(self):
        self.assertIsNone(load_capabilities(backend='opencode'))
        with self.assertRaisesRegex(ValueError, '--capabilities'):
            load_capabilities(backend='opencode',required=True)
        profile = dict(schema_version=1,backend='opencode',input_modalities=['text'],output_modalities=['text'],tools=True,images={})
        path = self.write('text-only.json',profile)
        with self.assertRaisesRegex(ValueError,'image input'):
            load_capabilities(path,backend='opencode',required=True)
        profile['input_modalities'].append('image');profile['tools']=False;path.write_text(json.dumps(profile))
        with self.assertRaisesRegex(ValueError,'tools'):
            load_capabilities(path,backend='opencode',required=True)

    def test_capability_path_is_resolved_before_worker_changes_directory(self):
        source,prepared,args = self.queue()
        args[args.index('--capabilities')+1] = 'capabilities.json'
        result = subprocess.run([sys.executable,str(ROOT/'scripts/run_page_jobs.py'),*map(str,args),
                                 '--output',str(self.root/'relative-caps-jobs')],cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        summary=json.loads((self.root/'relative-caps-jobs/summary.json').read_text())
        self.assertEqual(summary['validated_pages'],[1])

    def test_neutral_capabilities_bind_model_and_check_readable_original(self):
        profile = dict(schema_version=1, backend='opencode', model='arbitrary/vendor-model',
                       input_modalities=['image'], output_modalities=['text'], tools=True,
                       images=dict(max_attachments=1, max_edge=100, max_pixels=10000, max_total_pixels=10000))
        path = self.write('caps.json', profile)
        with self.assertRaisesRegex(ValueError, 'same explicit model'):
            load_capabilities(path, backend='opencode')
        caps = load_capabilities(path, backend='opencode', model='arbitrary/vendor-model')
        small, large = self.root/'small.png', self.root/'large.png'
        Image.new('RGB', (20, 20)).save(small)
        Image.new('RGB', (200, 200)).save(large)
        self.assertEqual(check_images(caps, [small])['status'], 'within_declared_limits')
        with self.assertRaisesRegex(ValueError, 'per-image'):
            check_images(caps, [small], [large])
        with self.assertRaisesRegex(ValueError, 'attachments'):
            check_images(caps, [small, small])
        profile['unrecognized'] = True
        path.write_text(json.dumps(profile))
        with self.assertRaises(ValueError):
            load_capabilities(path, backend='opencode', model='arbitrary/vendor-model')

    def test_stage_fingerprint_includes_transitive_local_imports(self):
        (self.root/'entry.py').write_text('from middle import value\n')
        (self.root/'middle.py').write_text('from leaf import value\n')
        (self.root/'leaf.py').write_text('value = 1\n')
        before = tool_closure([self.root/'entry.py'], self.root)
        self.assertEqual(set(before), {'entry.py', 'middle.py', 'leaf.py'})
        (self.root/'leaf.py').write_text('value = 2\n')
        after = tool_closure([self.root/'entry.py'], self.root)
        self.assertNotEqual(before, after)
        self.assertEqual(before['entry.py'], after['entry.py'])

    def test_imported_draft_and_receipt_work_without_the_original_engine(self):
        source, prepared, _ = self.queue()
        page = json.loads((prepared/'manifest.json').read_text())['pages'][0]
        text = self.root/'draft.md'; text.write_text('External engine draft')
        blocks = self.write('engine-blocks.json', {'opaque': [1, 2], 'coordinates': 'engine-native'})
        spec = self.write('import.json', dict(schema_version=1, source_sha256=source_digest(source),
            producer=dict(name='uninstalled-foreign-engine', version='some-version'), pages=[dict(page=1,
            image_sha256=page['original']['sha256'], text=dict(path=text.name, sha256=file_hash(text)),
            raw_blocks=dict(path=blocks.name, sha256=file_hash(blocks)))]))
        output = self.root/'imported'
        import_drafts(source, prepared, spec, output)
        contract = json.loads((output/'contract.json').read_text())
        self.assertTrue(verify_draft(output/'0001/original.md', page, source_digest(source), contract))
        (output/'0001/original.md').write_text('changed after publishing')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            verify_draft(output/'0001/original.md', page, source_digest(source), contract)

    def test_reuse_validated_content_needs_no_backend_installation_or_paid_call(self):
        source, prepared, args = self.queue()
        old = self.root/'original-run'
        result = self.cli('run_page_jobs.py', *args, '--output', old)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        execution = json.loads((old/'pages/0001/attempt-001/run.json').read_text())
        self.assertEqual(execution['status'], 'validated')
        self.assertTrue(execution['paid_call_started'])
        self.assertEqual(execution['model_invocations'], 1)
        current = self.root/'new-stage'
        result = self.cli('run_page_jobs.py', '--source', source, '--prepared-dir', prepared,
                          '--expected-pages', '1', '--output', current, '--backend', 'codex',
                          '--codex', str(self.root/'missing-executable'), '--reuse-only', '--reuse-validated-from', old)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        summary = json.loads((current/'summary.json').read_text())
        self.assertEqual(summary['model_invocations'], 0)
        self.assertEqual(summary['validated_pages'], [1])
        self.assertEqual(summary['reused_pages'], [1])
        checkpoint = json.loads((current/'pages/0001/attempt-001/checkpoint.json').read_text())
        self.assertEqual(checkpoint['model'], 'test')
        self.assertFalse(checkpoint['model_call_started'])
        result = self.cli('canonicalize_pages.py', '--source', source, '--manifest', prepared/'manifest.json',
                          '--expected-pages', '1', '--jobs-dir', current, '--output', self.root/'canonical')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        old_output = old/'pages/0001/attempt-001/output/0001.json'
        old_output.write_text(old_output.read_text()+'\n')
        result = self.cli('run_page_jobs.py', '--source', source, '--prepared-dir', prepared,
                          '--expected-pages', '1', '--output', self.root/'stale-stage',
                          '--reuse-only', '--reuse-validated-from', old)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('inventory changed', result.stdout)

    def test_reuse_rejects_same_source_with_changed_geometry(self):
        source, prepared, args = self.queue()
        old = self.root/'original-run'
        result = self.cli('run_page_jobs.py', *args, '--output', old)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = json.loads((prepared/'manifest.json').read_text())['pages'][0]
        meta['original']['width'] += 1
        with self.assertRaisesRegex(ValueError, 'geometry'):
            adopt_page([old], self.root/'adopted', 1, meta, source_digest(source), 'new-stage')

    def test_reused_page_does_not_skip_the_new_backend_pilot(self):
        source, prepared, args = historical.AuditRegressions.prepare_page_queue(self, count=3)
        old = self.root/'old'
        result = self.cli('run_page_jobs.py', *args, '--pages', '1', '--output', old)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        backend = self.root/'new-backend'
        backend.write_text('#!'+sys.executable+'\nimport sys\nsys.stdin.read()\nsys.exit(1)\n')
        backend.chmod(0o755)
        current = self.root/'new'
        caps = self.write('new-capabilities.json',dict(schema_version=1,backend='codex',model='different',input_modalities=['image'],output_modalities=['text'],tools=True,images={}))
        result = self.cli('run_page_jobs.py', '--capabilities',caps, '--source', source, '--prepared-dir', prepared,
                          '--expected-pages', '1-3', '--output', current, '--backend', 'codex',
                          '--codex', backend, '--model', 'different', '--workers', '2', '--poll-seconds', '.01',
                          '--reuse-validated-from', old, '--execute')
        self.assertNotEqual(result.returncode, 0)
        summary = json.loads((current/'summary.json').read_text())
        self.assertEqual(summary['model_invocations'], 1)
        self.assertIn('failed validation', summary['error'])
        self.assertEqual(summary['pending_pages'], [3])

    def test_proofread_requires_drafts_before_any_backend_call(self):
        source, prepared, _ = self.queue()
        result = self.cli('run_page_jobs.py', '--source', source, '--prepared-dir', prepared,
                          '--expected-pages', '1', '--output', self.root/'proofread', '--mode', 'proofread')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires drafts-dir', result.stderr)
        self.assertFalse((self.root/'proofread').exists())

    def test_usage_normalization_never_guesses_unknown_token_conventions(self):
        tokens = dict(input=100, output=20, reasoning=5, cache=dict(read=40, write=10))
        unknown = normalize(tokens, {})
        self.assertIsNone(unknown['input_uncached'])
        self.assertIsNone(unknown['output'])
        self.assertTrue(unknown['partial'])
        normalized = normalize(tokens, dict(input='includes_cache', output='separate_reasoning'))
        self.assertEqual(normalized['input_uncached'], 50)
        self.assertEqual(normalized['output'], 25)
        self.assertEqual(normalize(tokens, dict(input='uncached', output='includes_reasoning'))['output'], 20)
        with self.assertRaises(ValueError):
            normalize({**tokens, 'input': 1}, dict(input='includes_cache'))

    def test_coordinate_tables_share_evaluator_and_renderer_parsing_without_mutation(self):
        cells = [dict(row=0, col=0, text='A', rowspan=2), dict(row=0, col=1, text='B'),
                 dict(row=1, col=1, text='C')]
        block = dict(type='table', text='opaque native table', cells=cells, styles=[])
        before = copy.deepcopy(block)
        rows = table_rows(block)
        self.assertEqual([[c.text for c in row] for row in rows], [['A', 'B'], ['C']])
        rendered = prepare_render_block(block, [], [])
        self.assertEqual(rendered['cells'][1][0]['text'], 'C')
        self.assertEqual(block, before)
        result = evaluate(dict(checkpoints=[dict(id='t', dimension='table', cells=cells)]),
                          dict(blocks=[dict(id='table', **block)]), dict(units=dict(t=dict(block_id='table'))))
        self.assertEqual(result['dimensions']['table']['cell_text_and_span_exact'], 3)
        self.assertEqual(len(structured_rows([dict(row=0, col=0, text='A', rowspan=2)])), 2)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            structured_rows([*cells, dict(row=1, col=0, text='overlap')])
        with self.assertRaisesRegex(ValueError, 'hole'):
            structured_rows([dict(row=0, col=1, text='missing-first-cell')])

    def test_literal_latex_table_preserves_math_ampersands_and_rejects_macro_rows(self):
        text = (r'\begin{tabular}{|l|c|}\hline'
                r'\textbf{Term} & Value \\\hline'
                r'$a\&b$ & $\frac{x}{y}$ \\'
                r'\hline\end{tabular}')
        rows = table_rows(dict(text=text))
        self.assertEqual([[c.text for c in row] for row in rows],
                         [['Term', 'Value'], [r'$a\&b$', r'$\frac{x}{y}$']])
        self.assertEqual(rows[0][0].styles[0]['kind'], 'bold')
        with self.assertRaisesRegex(ValueError, 'explicit structured cells'):
            table_rows(dict(text=r'\begin{tabular}{c}\multicolumn{2}{c}{a}\\\end{tabular}'))
        with self.assertRaisesRegex(ValueError, 'Unclosed'):
            table_rows(dict(text=r'\begin{tabular}{c}$x\\\end{tabular}'))

    def test_markdown_uses_explicit_cells_with_spans_and_preserves_unsupported_raw(self):
        block = dict(id='grid', type='table', text='Original engine text remains intact', cells=[
            [dict(text='A&B', colspan=2)], ['<1', '$x$']])
        before = copy.deepcopy(block)
        result = markdown(block)
        self.assertIn('<td colspan="2">A&amp;B</td>', result)
        self.assertIn('&lt;1', result)
        self.assertEqual(block, before)
        unsupported = dict(id='complex', type='table', text=r'\begin{tabular}{c}\multicolumn{2}{c}{A}\\\end{tabular}')
        issues = []
        self.assertEqual(markdown(unsupported, issues), unsupported['text'])
        self.assertEqual(issues[0]['block_id'], 'complex')

    def test_usage_deduplicates_sessions_and_does_not_double_count_step_finish(self):
        item = dict(session_id='ses_x', message_id='msg_y', tokens_as_returned=dict(
                    input=100, output=20, reasoning=5, cache=dict(read=40, write=0)), opencode_reported_cost=.1)
        one = self.write('one.json', dict(model_invocations=1, session_usage=[item],
                         returned_usage=[dict(part_id='duplicate-step', tokens_as_returned=item['tokens_as_returned'])]))
        two = self.write('two.json', dict(model_invocations=1, session_usage=[item]))
        reused = self.write('reused.json', dict(model_invocations=0, reused=True, session_usage=[item]))
        ledger = collect([one, two, reused], dict(input='uncached', output='includes_reasoning'))
        self.assertEqual(ledger['record_count'], 1)
        self.assertEqual(len(ledger['records'][0]['sources']), 2)
        self.assertIsNone(ledger['records'][0]['actual_cost'])
        self.assertIsNone(ledger['provider_request_count'])
        changed = copy.deepcopy(item); changed['tokens_as_returned']['input'] += 1
        two.write_text(json.dumps(dict(session_usage=[changed])))
        with self.assertRaisesRegex(ValueError, 'Conflicting usage'):
            collect([one, two])

    def test_accounting_keeps_unknown_fields_and_provider_request_count_null(self):
        path = self.write('records.json', [dict(id='local-message', record_unit='assistant_message',
            input_uncached=None, cache_read=100, cache_write=0, output=None, reasoning=5, partial=True)])
        output = self.root/'accounting.json'
        result = self.cli('account_usage.py', '--records', path, '--output', output)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        report = json.loads(output.read_text())
        self.assertIsNone(report['tokens']['input_uncached'])
        self.assertIsNone(report['tokens']['output'])
        self.assertIsNone(report['requests'])
        self.assertEqual(report['usage_records'], 1)
        self.assertEqual(report['tokens_known_subtotals']['cache_read'], 100)

    def test_flat_codex_usage_keeps_known_fields_without_inventing_cache_writes(self):
        path = self.write('codex.json', dict(backend='codex', model_invocations=1,
            returned_usage=[dict(input_tokens=100, cached_input_tokens=40,
                                 output_tokens=20, reasoning_output_tokens=5)]))
        record = collect([path], dict(input='includes_cache', output='includes_reasoning'))['records'][0]
        self.assertEqual(record['cache_read'], 40)
        self.assertIsNone(record['cache_write'])
        self.assertIsNone(record['input_uncached'])
        self.assertEqual(record['output'], 20)
        self.assertEqual(record['raw_flat_usage']['input_tokens'], 100)
        self.assertTrue(record['partial'])


class QualityMetrics(unittest.TestCase):
    def block(self, ident, text, **fields):
        return dict(id=ident, text=text, styles=[], **fields)

    def test_missing_units_count_as_text_and_formula_failures(self):
        reference = dict(checkpoints=[dict(id='text', dimension='text', text='汉字'),
                         dict(id='math', dimension='formula', text='$x+1$', accepted_variants=['\\(x+1\\)'])])
        result = evaluate(reference, dict(blocks=[]), dict(units={}))['dimensions']
        self.assertEqual(result['text']['cer'], 1)
        self.assertEqual(result['text']['exact'], 0)
        self.assertEqual(result['formula']['accepted_exact'], 0)

    def test_formula_variants_are_explicit_and_text_whitespace_is_literal(self):
        reference = dict(checkpoints=[dict(id='text', dimension='text', text='a b'),
                         dict(id='math', dimension='formula', text='$x+1$', accepted_variants=['\\(x+1\\)'])])
        candidate = dict(blocks=[self.block('t', 'ab'), self.block('m', '\\(x+1\\)')])
        result = evaluate(reference, candidate, dict(units=dict(text=dict(block_id='t'), math=dict(block_id='m'))))['dimensions']
        self.assertEqual(result['text']['edit_distance'], 1)
        self.assertEqual(result['formula']['strict_exact'], 0)
        self.assertEqual(result['formula']['accepted_exact'], 1)

    def test_table_text_spans_and_extra_cells_have_separate_denominators(self):
        reference = dict(checkpoints=[dict(id='table', dimension='table', cells=[[
                         dict(text='A', colspan=2)], ['B', 'C']])])
        candidate = dict(blocks=[self.block('t', '', cells=[['A', 'extra'], ['B', 'wrong']])])
        result = evaluate(reference, candidate, dict(units=dict(table=dict(block_id='t'))))['dimensions']['table']
        self.assertEqual(result['reference_cells'], 3)
        self.assertEqual(result['candidate_cells'], 4)
        self.assertEqual(result['cell_text_exact'], 2)
        self.assertEqual(result['cell_text_and_span_exact'], 1)
        self.assertEqual(result['shape_exact'], 1)

    def test_unicode_style_precision_negative_example_and_text_coverage(self):
        reference = dict(checkpoints=[dict(id='s', dimension='styles', text='汉😀ab', styles=[
                         dict(start=1, end=2, kind='bold')]),
                         dict(id='missing', dimension='styles', text='other', styles=[])])
        block = self.block('b', 'prefix汉😀ab')
        block['styles'] = [dict(start=7, end=9, kind='bold')]
        result = evaluate(reference, dict(blocks=[block]), dict(units=dict(s=dict(block_id='b', start=6))))['dimensions']['styles']
        self.assertEqual(result['by_kind']['bold']['tp'], 1)
        self.assertEqual(result['by_kind']['bold']['fp'], 1)
        self.assertEqual(result['by_kind']['bold']['precision'], .5)
        self.assertEqual(result['scored_characters'], 4)
        self.assertEqual(result['reference_characters'], 9)

    def test_reading_order_reversal_missing_units_and_overlapping_mapping(self):
        reference = dict(checkpoints=[dict(id=x, dimension='text', text=x) for x in 'abc']+
                         [dict(id='order', dimension='reading_order', units=list('abc'))])
        candidate = dict(blocks=[self.block('b', 'b'), self.block('a', 'a')])
        result = evaluate(reference, candidate, dict(units=dict(a=dict(block_id='a'), b=dict(block_id='b'))))['dimensions']['reading_order']
        self.assertEqual(result['reference_pairs'], 3)
        self.assertEqual(result['comparable_pairs'], 1)
        self.assertEqual(result['concordant_pairs'], 0)
        result = evaluate(reference, candidate, dict(units=dict(a=dict(block_id='a'), b=dict(block_id='a'))))['dimensions']['reading_order']
        self.assertEqual(result['comparable_pairs'], 0)

    def test_fixed_synthetic_candidates_and_cli_identities(self):
        for fixture in sorted((ROOT/'tests/fixtures/quality').iterdir()):
            reference = json.loads((fixture/'reference.json').read_text())
            for kind in ('raw', 'reviewed'):
                candidate = fixture/(kind+'.json')
                alignment = fixture/(kind+'-alignment.json')
                expected = json.loads((fixture/(kind+'-expected.json')).read_text())
                with tempfile.TemporaryDirectory() as temporary:
                    output = Path(temporary)/'report.json'
                    result = subprocess.run([sys.executable, str(ROOT/'scripts/evaluate_quality.py'),
                        '--reference', str(fixture/'reference.json'), '--candidate', str(candidate),
                        '--alignment', str(alignment), '--manifest', str(fixture/'manifest.json'),
                        '--output', str(output)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    actual = json.loads(output.read_text())['dimensions']
                    self.assertEqual(actual, expected)
                    # The reviewed snapshots cover every selected text/style/order
                    # checkpoint; raw snapshots remain visible as separate baselines.
                    if kind == 'reviewed':
                        self.assertEqual(actual['text']['exact'], actual['text']['checkpoints'])
                        self.assertEqual(actual['styles']['exact_checkpoints'], actual['styles']['checkpoints'])
                        self.assertEqual(actual['reading_order']['concordant_pairs'], actual['reading_order']['reference_pairs'])
                        self.assertEqual(actual['table']['cell_text_and_span_exact'], actual['table']['reference_cells'])
                    tampered = Path(temporary)/'changed-alignment.json'
                    data = json.loads(alignment.read_text()); data['candidate_sha256'] = '0'*64
                    tampered.write_text(json.dumps(data))
                    result = subprocess.run([sys.executable, str(ROOT/'scripts/evaluate_quality.py'),
                        '--reference', str(fixture/'reference.json'), '--candidate', str(candidate),
                        '--alignment', str(tampered), '--manifest', str(fixture/'manifest.json'),
                        '--output', str(Path(temporary)/'untrusted.json')], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('exact reference and candidate', result.stderr)


if __name__ == '__main__':
    unittest.main()
