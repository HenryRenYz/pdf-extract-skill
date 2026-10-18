"""Behavioral regressions for document, model and pipeline generalization."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from source_identity import source_digest, source_record
from model_identity import model_identity
from style_patches import apply_style_patches
from latex_contents import build_contents_plan
from latex_numbering import build_numbering_plan, heading_record
from assemble_book import chapter_number
from package_latex import package
from audit_figures import audit
from opencode_runtime import invocation, session_metadata
from format_preflight import DEFAULT_TOOLS
from package_skill import runtime_files


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, name, *args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts' / name), *map(str, args)],
                              capture_output=True, text=True, timeout=30)

    def test_chapter_forms_agree_across_assembly_and_native_numbering(self):
        for text in ['第1章 绪论', '第一章 绪论', 'Chapter 1 Introduction']:
            block = dict(type='heading', text=text, heading_depth=1, chapter_id='chapter-1', block_id='h')
            self.assertEqual(chapter_number(text), 1)
            self.assertEqual(heading_record(block)['number_parts'], [1])
            self.assertIn(text[heading_record(block)['title_start']:], ['绪论', 'Introduction'])
        self.assertEqual(chapter_number('Chapter 1'), 1)
        self.assertEqual(chapter_number('Chapter 1 Python 3'), 1)
        self.assertIsNone(chapter_number('Chapter 1 Introduction......2'))

    def test_mixed_contents_rows_are_all_matched_or_explicitly_unparsed(self):
        blocks = [dict(type='paragraph', text='目录\n第一章 绪论……（1）\n习题一……20',
                       chapter_id='front-contents', block_id='toc', page=1),
                  dict(type='heading', text='第一章 绪论', chapter_id='chapter-1',
                       heading_depth=1, block_id='ch', page=2),
                  dict(type='heading', text='习题一', chapter_id='chapter-1', heading_depth=2,
                       block_id='exercise', page=3)]
        plan = build_contents_plan(blocks, build_numbering_plan(blocks))
        self.assertEqual({r['heading_block_id'] for r in plan['matched_entries']}, {'ch', 'exercise'})
        self.assertIn('exercise', plan['supplemental_headings'])
        blocks[0]['text'] += '\nAn unrecognized directory row'
        with self.assertRaisesRegex(ValueError, 'Unparsed'):
            build_contents_plan(blocks, build_numbering_plan(blocks))
        blocks[0]['text'] = 'Chapter 1 Introduction......1'
        blocks[1]['text'] = 'Chapter 1 Introduction'
        self.assertEqual(len(build_contents_plan(blocks, build_numbering_plan(blocks))['matched_entries']), 1)

    def test_model_identity_covers_shards_and_processor_and_missing_index(self):
        model = self.root / 'model'; model.mkdir()
        write(model / 'config.json', {})
        write(model / 'model.safetensors.index.json', {'weight_map': {'a': 'part1.safetensors'}})
        (model / 'part1.safetensors').write_bytes(b'weight one')
        write(model / 'preprocessor_config.json', {'size': 800})
        before = model_identity(model)
        (model / 'part1.safetensors').write_bytes(b'weight two')
        self.assertNotEqual(model_identity(model)['sha256'], before['sha256'])
        before = model_identity(model)
        write(model / 'preprocessor_config.json', {'size': 900})
        self.assertNotEqual(model_identity(model)['sha256'], before['sha256'])
        (model / 'part1.safetensors').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing indexed'):
            model_identity(model)

    def test_style_patch_uses_scope_and_preserves_confirmed_source(self):
        source = 'a' * 64
        data = dict(page=1, blocks=[dict(id='h', type='heading', text='Title', bbox=None,
                   styles=[dict(start=0, end=5, kind='italic', evidence='source_checked')])], uncertainties=[])
        rules = dict(source_sha256=source, review_status='reviewed', version='v1',
                     rules=[dict(id='arbitrary-id', scope={'pages': [1], 'types': ['heading']})])
        style = dict(start=0, end=5, kind='italic', evidence='rule_inferred',
                     rule_id='arbitrary-id', rule_version='v1')
        doc = dict(page=1, source_sha256=source, rule_version='v1', patches=[dict(block_id='h',
                   base_text_sha256=hashlib.sha256(b'Title').hexdigest(), styles=[style])])
        output, _ = apply_style_patches(data, [doc], rules, source)
        self.assertEqual(output['blocks'][0]['styles'], data['blocks'][0]['styles'])
        style['kind'] = 'bold'
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            apply_style_patches(data, [doc], rules, source)
        rules['rules'][0]['scope']['pages'] = [99]
        with self.assertRaisesRegex(ValueError, 'outside'):
            apply_style_patches(data, [doc], rules, source)
        self.assertEqual(data['blocks'][0]['styles'][0]['kind'], 'italic')

    def prepare_page_queue(self, count=3):
        from PIL import Image
        source = self.root / 'source-images'; source.mkdir()
        for number in range(1, count + 1):
            Image.new('RGB', (80, 100), 'white').save(source / f'{number:04d}.png')
        prepared = self.root / 'prepared'
        result = self.cli('prepare_images.py', '--pages-dir', source, '--pages', f'1-{count}',
                          '--output', prepared, '--max-pixels', 1000000)
        self.assertEqual(result.returncode, 0, result.stderr)
        backend = self.root / 'fake-codex'
        backend.write_text('#!' + sys.executable + '\n' + '''import json,pathlib,sys,time
sys.stdin.read(); time.sleep(.15)
p=json.loads(pathlib.Path('input/manifest.json').read_text())['pages'][0];n=p['page']
b=[] if n==2 else [dict(id=f'p{n}-b',type='paragraph',text='sample',bbox=[1,1,20,20],styles=[],evidence='unreviewed')]
d=dict(page=n,printed_page=n,blocks=b,uncertainties=[],changes=[],viewed_images=[i['path'] for i in p['images']])
if n==2:d.update(page_status='blank',blank_reason='Actual fixture is blank')
pathlib.Path(f'output/{n:04d}.json').write_text(json.dumps(d))
''')
        backend.chmod(0o755)
        caps = self.root/'capabilities.json'
        write(caps, dict(schema_version=1, backend='codex', model='test', input_modalities=['text','image'],
                         output_modalities=['text'], tools=True, images={}))
        args = ['--capabilities', caps, '--prepared-dir', prepared, '--source', source, '--expected-pages', f'1-{count}',
                '--backend', 'codex', '--codex', backend, '--model', 'test', '--mode', 'extract',
                '--workers', '1', '--poll-seconds', '.01', '--producer-timeout', '.05', '--execute']
        return source, prepared, args

    def test_image_collection_and_blank_page_survive_real_queue_and_assembly(self):
        source, prepared, args = self.prepare_page_queue()
        jobs = self.root / 'jobs'
        result = self.cli('run_page_jobs.py', *args, '--output', jobs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads((jobs / 'summary.json').read_text())
        self.assertEqual(summary['validated_pages'], [1, 2, 3])
        self.assertEqual(summary['format_admission']['status'], 'not_required')
        canonical = self.root / 'canonical'
        common = ['--manifest', prepared / 'manifest.json', '--source', source, '--expected-pages', '1-3']
        result = self.cli('canonicalize_pages.py', *common, '--jobs-dir', jobs, '--output', canonical)
        self.assertEqual(result.returncode, 0, result.stderr)
        book = self.root / 'book'
        result = self.cli('assemble_book.py', *common, '--pages-dir', canonical / 'pages', '--output', book)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        blocks = [json.loads(line) for line in (book / 'blocks.jsonl').read_text().splitlines()]
        self.assertEqual({b['page'] for b in blocks}, {1, 2, 3})
        self.assertEqual(next(b for b in blocks if b['page'] == 2)['type'], 'blank')
        text_out = self.root / 'text-export'
        result = self.cli('export_text.py', '--blocks', book / 'blocks.jsonl', '--output', text_out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((text_out / 'document.md').read_text().count('sample'), 2)
        self.assertEqual((text_out / 'blocks.jsonl').read_bytes(), (book / 'blocks.jsonl').read_bytes())
        if shutil.which('xelatex'):
            profile = self.root / 'format.json'
            write(profile, dict(schema_version=1, status='draft', version='image-test',
                                source_sha256=source_digest(source), template_dir=str(ROOT / 'assets/latex'),
                                representative_pages=[1], tested_families=['paragraph', 'blank']))
            result = self.cli('export_latex.py', '--blocks', book / 'blocks.jsonl', '--source', source,
                              '--output', self.root / 'latex', '--expected-pages', '1-3',
                              '--format-profile', profile, '--preflight-mode', '--compile')
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads((self.root / 'latex-export.json').read_text())
            self.assertEqual(report['declared_pages'], [1, 2, 3])
        original = source_digest(source)
        moved = self.root / 'moved'; shutil.copytree(source, moved)
        self.assertEqual(source_digest(moved), original)
        (moved / '0001.png').write_bytes(b'changed')
        self.assertNotEqual(source_digest(moved), original)

    def test_invocation_budget_stops_before_second_cli_and_preserves_progress(self):
        _, _, args = self.prepare_page_queue()
        jobs = self.root / 'budget-jobs'
        template=self.root/'page-template.txt'
        template.write_text((ROOT/'assets/page-prompt.txt').read_text()+'\nTask-specific compact JSON output.\n')
        result = self.cli('run_page_jobs.py', *args, '--output', jobs, '--max-invocations', 1,
                          '--prompt-template',template)
        self.assertNotEqual(result.returncode, 0)
        summary = json.loads((jobs / 'summary.json').read_text())
        self.assertEqual(summary['model_invocations'], 1)
        self.assertEqual(summary['validated_pages'], [1])
        self.assertEqual(summary['pending_pages'], [2, 3])
        self.assertIn('budget exhausted', summary['error'])
        self.assertEqual(json.loads((jobs/'contract.json').read_text())['base_prompt_sha256'],sha(template))
        template.write_text(template.read_text()+'Changed instructions\n')
        result=self.cli('run_page_jobs.py',*args,'--output',jobs,'--resume','--max-invocations',1,
                        '--prompt-template',template)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('differs',result.stderr)

    def test_stopped_complete_output_requires_bound_recovery_and_preserves_failure(self):
        source, prepared, args = self.prepare_page_queue(count=1)
        backend = Path(args[args.index('--codex') + 1])
        backend.write_text(backend.read_text() + '\ntime.sleep(3)\n')
        jobs = self.root / 'interrupted'
        result = self.cli('run_page_jobs.py', *args, '--output', jobs, '--timeout', '.5')
        self.assertNotEqual(result.returncode, 0)
        job = jobs / 'pages/0001/attempt-001'
        checkpoint = json.loads((job/'checkpoint.json').read_text())
        self.assertTrue(checkpoint['timed_out'])
        raw = job/'output/0001.json'
        self.assertTrue(raw.is_file())
        meta = json.loads((prepared/'manifest.json').read_text())['pages'][0]
        evidence = prepared/meta['original']['path']
        receipt = dict(page=1, source_sha256=source_digest(source),
                       source_image_sha256=meta['original']['sha256'], checkpoint_sha256=sha(job/'checkpoint.json'),
                       output_sha256=sha(raw), decision='accept_complete_output',reviewer='independent fixture',
                       reason='Source reviewed after process terminated',evidence_image=str(evidence),
                       evidence_image_sha256=sha(evidence))
        receipts=self.root/'recoveries.json';write(receipts,[receipt])
        common=['--manifest',prepared/'manifest.json','--source',source,'--expected-pages','1',
                '--jobs-dir',jobs]
        self.assertNotEqual(self.cli('canonicalize_pages.py',*common,'--output',self.root/'reject').returncode,0)
        result=self.cli('canonicalize_pages.py',*common,'--recoveries',receipts,'--output',self.root/'recovered')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(sha(job/'checkpoint.json'),receipt['checkpoint_sha256'])
        canonical=json.loads((self.root/'recovered/pages/0001.json').read_text())
        self.assertEqual(canonical['normalization'][0]['original_timed_out'],True)
        patch=dict(page=1,block_id='p1-b',field='bbox',before=[1,1,20,20],after=[2,2,21,21],
                   source_sha256=receipt['source_sha256'],source_image_sha256=receipt['source_image_sha256'],
                   evidence='source_checked',reason='Reviewed coordinate correction',evidence_image=str(evidence))
        patches=self.root/'patches.json';write(patches,[patch])
        result=self.cli('canonicalize_pages.py',*common,'--recoveries',receipts,'--patches',patches,
                        '--output',self.root/'recovered-coordinates')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.root/'recovered-coordinates/pages/0001.json').read_text())['blocks'][0]['bbox'],[2,2,21,21])
        patch['after']=[0,0,99999,99999];write(patches,[patch])
        self.assertNotEqual(self.cli('canonicalize_pages.py',*common,'--recoveries',receipts,'--patches',patches,
                                    '--output',self.root/'invalid-coordinates').returncode,0)
        receipt['output_sha256']='0'*64;write(receipts,[receipt])
        self.assertNotEqual(self.cli('canonicalize_pages.py',*common,'--recoveries',receipts,
                                    '--output',self.root/'stale').returncode,0)

    def test_packager_rejects_source_changed_after_export(self):
        dist = self.root / 'dist'; dist.mkdir()
        tex = dist / 'main.tex';tex.write_text('accepted source')
        sidecar = self.root / 'export.json'
        write(sidecar, dict(figures={}, package_files=[dict(path='main.tex', sha256=sha(tex))]))
        tex.write_text('changed source')
        with self.assertRaisesRegex(ValueError, 'changed'):
            package(SimpleNamespace(dist=dist, export_manifest=sidecar, zip=self.root / 'source.zip'))
        self.assertFalse((self.root / 'source.zip').exists())

    def test_dict_asset_reference_is_checked_by_coverage_auditor(self):
        from PIL import Image
        image = self.root / 'image.png';Image.new('RGB', (80, 100), 'white').save(image)
        pdf = self.root / 'source.pdf';pdf.write_bytes(b'identity fixture')
        inv = self.root / 'inventory.json'; registry = self.root / 'registry.json';blocks = self.root / 'blocks.jsonl'
        write(inv, dict(source_sha256=sha(pdf), scope_pages=[1], method='source_first_visual_inventory',
                        reviewed_pages=[dict(physical_page=1, reviewer='fixture', viewed_evidence=True,
                                             source_image=str(image), source_image_sha256=sha(image))],
                        items=[dict(item_id='i', physical_page=1, bbox_px=[1,1,20,20], description='fixture',
                                    source_block_id='b', figure_id='f', kind='figure')]))
        write(registry, dict(source_sha256=sha(pdf), figures=[dict(figure_id='f', physical_page=1,
                             final_assets={'latex': {'path': image.name, 'sha256': sha(image)}})]))
        blocks.write_text(json.dumps(dict(block_id='b', page=1, type='figure', figure_id='f',
                              source_sha256=sha(pdf), bbox=[1,1,20,20]))+'\n')
        self.assertTrue(audit(pdf, inv, blocks, registry)['coverage_pass'])
        image.write_bytes(b'changed')
        self.assertFalse(audit(pdf, inv, blocks, registry)['coverage_pass'])

    def test_v2_default_model_invocation_uses_local_permissions_and_supported_flags(self):
        executable = self.root / 'fake-opencode'
        executable.write_text('#!' + sys.executable + '\nimport sys,json\nprint("opencode v2.0.32" if "--version" in sys.argv else "[]")\n')
        executable.chmod(0o755)
        job = self.root / 'job';job.mkdir()
        permission = {'*':'deny','read':{'input/*':'allow'},'edit':{'output/*':'allow'}}
        config = {'permission':permission,'agent':{'worker':dict(description='fixture',mode='primary',steps=8,prompt='Read assigned images')}}
        cmd, env, _ = invocation(str(executable),job,config,'source-grounded request',[], 'worker','fixture')
        self.assertEqual(env['PWD'], str(job))
        self.assertIn('--standalone',cmd)
        self.assertNotIn('--model',cmd)
        self.assertNotIn('--dir',cmd)
        actual=json.loads((job/'.opencode/opencode.json').read_text())
        self.assertNotIn('model',actual)
        self.assertEqual(actual['agents']['worker']['steps'],8)
        self.assertIn({'action':'*','resource':'*','effect':'deny'},actual['agents']['worker']['permissions'])

    def test_v2_session_metadata_records_actual_default_model_without_text_or_inferred_totals(self):
        executable = self.root/'session-export'
        document = dict(messages=[dict(type='assistant', id='message-1',
                        model=dict(providerID='custom-provider',id='default-vision'),
                        tokens=dict(input=12,output=3,reasoning=9,cache=dict(read=8,write=0)),
                        cost=.01,finish='stop',parts=[dict(text='SECRET RESPONSE')])],
                        credentials='SECRET CONFIG')
        executable.write_text('#!'+sys.executable+'\nimport json\nprint('+repr(json.dumps(document))+')\n')
        executable.chmod(0o755)
        metadata=session_metadata(str(executable),self.root,['ses_test'],{'major':2})
        self.assertEqual(metadata['actual_models'],[dict(provider_id='custom-provider',model_id='default-vision')])
        self.assertEqual(metadata['session_usage'][0]['tokens_as_returned']['output'],3)
        self.assertNotIn('total',metadata['session_usage'][0]['tokens_as_returned'])
        self.assertNotIn('SECRET',json.dumps(metadata))

    def test_skill_distribution_excludes_inputs_results_and_freezes_all_export_dependencies(self):
        names = {p.relative_to(ROOT).parts[0] for p in runtime_files(ROOT)}
        self.assertTrue(names.isdisjoint({'input','runs','tests','_stash','output'}))
        self.assertTrue({'latex_contents.py','table_layout.py','table_presentation.py','figure_layout.py',
                         'document_parts.py','prepare_covers.py','source_identity.py'} <= set(DEFAULT_TOOLS))

    def test_native_vector_package_rebuilds_without_original_work_directory(self):
        import fitz
        dist = self.root / 'dist'; (dist / 'images').mkdir(parents=True)
        image = dist / 'images/vector.pdf'
        with fitz.open() as doc:
            doc.new_page(width=100, height=100).draw_line((10, 10), (90, 90))
            doc.save(image)
        registry = self.root / 'registry.json'
        write(registry, dict(source_sha256='a' * 64, figures=[dict(figure_id='v',
              build_recipe=dict(backend='native_pdf', source=dict(path=str(image), sha256=sha(image))))]))
        sidecar = self.root / 'export.json'
        write(sidecar, dict(source_sha256='a' * 64, inputs={'figures': {'path': str(registry), 'sha256': sha(registry)}},
              figures={'v':dict(package_path='images/vector.pdf', sha256=sha(image), status='accepted')},
              package_files=[dict(path='images/vector.pdf',sha256=sha(image))]))
        archive = self.root / 'source.zip'
        package(SimpleNamespace(dist=dist, export_manifest=sidecar, zip=archive, figures=None,
                                drawing_inputs=None, renderer=None, force=False, verify_drawings=True,
                                verify_output=self.root / 'replay', font_license=[]))
        import zipfile
        unpacked = self.root / 'unpacked'
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(unpacked)
        original_hash = sha(image)
        shutil.rmtree(dist)
        result = subprocess.run([sys.executable, str(unpacked / 'figure-code/rebuild.py'),
                                 '--output', str(self.root / 'portable-replay')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sha(self.root / 'portable-replay/v/figure.pdf'), original_hash)

    def test_chunks_keep_whole_math_code_and_footnote_provenance(self):
        from export_text import chunk_blocks
        blocks = [dict(block_id='equation',type='formula',text='x'*100,chapter_id='c1',sources=[{'page':1}]),
                  dict(block_id='code',type='code',text='a\nb',chapter_id='c1',sources=[{'page':2}],
                       footnotes=[{'marker':'1','blocks':[{'text':'note'}]}])]
        chunks = chunk_blocks(blocks, 20)
        self.assertEqual([c['block_ids'] for c in chunks], [['equation'], ['code']])
        self.assertTrue(chunks[0]['oversize'])
        self.assertEqual(chunks[1]['footnotes'], blocks[1]['footnotes'])
        self.assertIn('note',chunks[1]['text'])

    def test_svg_and_tikz_recipes_produce_real_vector_pdfs_and_check_source_hashes(self):
        if importlib.util.find_spec('cairosvg') is None:
            self.skipTest('Install the optional figures dependencies to test SVG/TikZ recipes together')
        import fitz
        from figure_recipe import bundle_recipe, rebuild_recipe
        svg=self.root/'original.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="50pt" viewBox="0 0 100 50">'
                       '<path d="M10 10 L90 40" fill="none" stroke="black" stroke-dasharray="4 3"/></svg>')
        recipe=bundle_recipe(dict(backend='svg',source=dict(path=str(svg),sha256=sha(svg))),
                             self.root/'svg-bundle',self.root)
        pdf=rebuild_recipe(recipe,self.root/'svg-bundle',self.root/'svg-replay')
        with fitz.open(pdf) as doc:
            self.assertAlmostEqual(doc[0].rect.width,100,places=2)
            self.assertAlmostEqual(doc[0].rect.height,50,places=2)
            self.assertTrue(doc[0].get_drawings()[0]['dashes'].strip(' []0') )
        (self.root/'svg-bundle/source.svg').write_text('changed')
        with self.assertRaisesRegex(ValueError,'changed'):
            rebuild_recipe(recipe,self.root/'svg-bundle',self.root/'stale-replay')
        if shutil.which('xelatex'):
            tex=self.root/'original.tex'
            tex.write_text(r'\documentclass[tikz]{standalone}\begin{document}'
                           r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}\end{document}')
            recipe=bundle_recipe(dict(backend='tikz',source=str(tex)),self.root/'tikz-bundle',self.root)
            pdf=rebuild_recipe(recipe,self.root/'tikz-bundle',self.root/'tikz-replay')
            with fitz.open(pdf) as doc:
                self.assertEqual(len(doc),1)
                self.assertTrue(doc[0].get_drawings())
                self.assertFalse(doc[0].get_images())

    def test_native_pdf_draft_retains_raw_structure_and_source_ready_identity(self):
        import fitz
        source = self.root / 'digital.pdf'
        with fitz.open() as document:
            page=document.new_page()
            page.insert_text((40, 50), 'Native draft example')
            page.set_rotation(90)
            document.save(source)
        prepared = self.root / 'prepared'
        result = self.cli('prepare_images.py', '--pdf', source, '--pages', '1', '--output', prepared,
                          '--dpi', '36', '--max-pixels', '1000000')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.root / 'native'
        result = self.cli('native_text.py', '--source-pdf', source, '--prepared-dir', prepared,
                          '--pages', '1', '--output', output)
        self.assertEqual(result.returncode, 0, result.stderr)
        ready = json.loads((output/'0001/page-ready.json').read_text())
        self.assertEqual(ready['source_sha256'], sha(source))
        self.assertIn('Native draft example', (output/'0001/original.md').read_text())
        raw = json.loads((output/'0001/raw-blocks.json').read_text())
        self.assertIn('spans', raw['blocks'][0]['lines'][0])
        self.assertEqual(raw['rotation'],90)
        box=fitz.Rect(raw['blocks'][0]['bbox'])*fitz.Matrix(*raw['pdf_to_prepared_matrix'])
        self.assertTrue(fitz.Rect(0,0,*raw['prepared_size_px']).contains(box))

    def test_optional_font_dependency_covers_nested_rendered_styles(self):
        from export_latex import uses_style
        blocks=[dict(type='list',text='item',styles=[],items=[dict(text='item',styles=[
                    dict(start=0,end=4,kind='kai',evidence='unreviewed')])])]
        self.assertTrue(uses_style(blocks,'kai'))
        self.assertFalse(uses_style(blocks,'underline'))
        self.assertTrue(uses_style([dict(footnotes=[dict(blocks=blocks)])],'kai'))
        self.assertFalse(uses_style([dict(kind='kai')],'kai'))

    def test_reviewed_article_hierarchy_overrides_heuristics_without_inventing_chapters(self):
        from assemble_book import check_page,source_records
        source,prepared,_=self.prepare_page_queue(count=1)
        manifest=json.loads((prepared/'manifest.json').read_text());page=manifest['pages'][0]
        evidence=prepared/page['original']['path'];checksum=source_digest(source)
        blocks=[dict(id='intro',type='paragraph',text='Continued text',bbox=None,styles=[],evidence='unreviewed')]
        for ident,text,depth,role in [('section','II. BACKGROUND',1,'section'),('subsection','A. Architecture',2,'subsection')]:
            review=dict(schema_version=1,role=role,font_size='section',depth=depth,
                        source_sha256=checksum,source_image_sha256=page['original']['sha256'],
                        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        review=dict(source_checked=True,reviewer='fixture',evidence_path=str(evidence)))
            if depth==1:review['container_id']='section-II'
            blocks.append(dict(id=ident,type='heading',text=text,bbox=None,styles=[],evidence='unreviewed',heading_presentation=review))
        data=dict(page=1,printed_page=1,blocks=blocks,uncertainties=[],changes=[],viewed_images=[p['path'] for p in page['images']])
        check_page(data,1,page,set(),checksum)
        def assemble():
            return source_records({1:data},{1:page},{1:{}},prepared,self.root/'book',checksum,
                                  {'needs_review':[],'evidence_downgrades':[]})
        records=assemble()
        self.assertEqual(records[0]['chapter_id'],'unclassified')
        self.assertEqual(records[1]['chapter_id'],'section-II')
        self.assertEqual(records[2]['parent_id'],'section')
        self.assertEqual(records[2]['heading_depth'],2)
        self.assertNotIn('chapter_number',records[1])
        for block in blocks[1:]:block['heading_presentation']['review']['source_checked']=False
        self.assertEqual(assemble()[2]['chapter_id'],'unclassified')
        self.assertEqual(assemble()[2]['heading_depth'],1)

    def test_explicit_unordered_bullets_use_template_marker_without_rewriting_source(self):
        from presentation_render import structured_list
        block=dict(type='list',text='● body',list_kind='unordered',items=[dict(label='●',text='literal ● body')])
        before=copy.deepcopy(block)
        rendered=structured_list(block,lambda value:value['text'])
        self.assertIn(r'\item[{\labelitemi}]',rendered)
        self.assertIn('literal ● body',rendered)
        self.assertEqual(block,before)
        block['list_kind']='ordered'
        self.assertIn('●',structured_list(block,lambda value:value['text']))


if __name__ == '__main__':
    unittest.main()
