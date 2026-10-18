"""Integrity tests exercise source identities and real boundary accounting."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assemble_book import (reading_records, apply_decisions, merge_candidates,
                           validate_assembly, check_page, apply_footnotes)
from semantic_contract import (SEMANTIC_FIELDS, TYPES, STYLES,
                               text_hash, validate_semantic_block)


class ContractTests(unittest.TestCase):
    def test_list_width_normalization_is_scoped_to_labels(self):
        block = dict(type='list', text='raw (1) a / （2） b',
                     items=[dict(label='(1)', text='literal（a）'), dict(label='（2）', text='literal(b)')])
        block['list_presentation'] = dict(schema_version=1, kind='ordered')
        self.assertTrue(any('mixes ASCII/CJK' in error for error in validate_semantic_block(block)))
        block['list_presentation']['label_normalizations'] = [dict(item_index=1, original='（2）',
            replacement='(2)', rule_id='source_label_group', rule_version='1')]
        self.assertFalse(any('mixes ASCII/CJK' in error for error in validate_semantic_block(block)))
        self.assertEqual(block['items'][0]['text'], 'literal（a）')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.image = self.root / 'source.png'
        self.image.write_bytes(b'source image identity')
        self.image_hash = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.source_hash = 'a' * 64

    def tearDown(self):
        self.tmp.cleanup()

    def block(self, identifier, text, page=1, kind='paragraph'):
        return dict(id=identifier, block_id=identifier, type=kind, text=text,
                    text_sha256=text_hash(text), styles=[], evidence='unreviewed',
                    bbox=[0, 0, 100, 100], page=page, printed_page=page,
                    chapter_id='chapter-1', parent_id=None,
                    source_sha256=self.source_hash,
                    source_image='source.png', source_image_absolute_path=str(self.image),
                    source_image_sha256=self.image_hash)

    def presentation(self, block, **fields):
        return dict(schema_version=1, source_sha256=self.source_hash,
                    source_image_sha256=self.image_hash, text_sha256=text_hash(block['text']),
                    review=dict(source_checked=True, reviewer='human reviewer',
                                evidence_path=str(self.image)), **fields)

    def report(self):
        return dict(merge_candidates=[], reviewed_boundaries=[], applied_merges=[])

    def decisions(self, left, right, action='merge', **fields):
        path = self.root / 'decisions.json'
        path.write_text(json.dumps(dict(source_sha256=self.source_hash, decisions=[dict(
            left_block_id=left['block_id'], right_block_id=right['block_id'], action=action,
            source_sha256=self.source_hash, source_checked=True, reason='compared native source',
            left_text_sha256=left['text_sha256'], right_text_sha256=right['text_sha256'],
            left_source_image_sha256=self.image_hash, right_source_image_sha256=self.image_hash,
            **fields)])))
        return path

    def test_reviewed_continuation_and_unindented_new_paragraph_are_distinct(self):
        block = self.block('one', '中文$x$ 后续')
        for boundary, first in [('continue', 'inherit'), ('new_paragraph', 'noindent')]:
            block['paragraph_presentation'] = self.presentation(block, boundary_before=boundary, first_line=first)
            self.assertEqual(validate_semantic_block(block), [])
            record = reading_records([block])[0]
            self.assertEqual(record['paragraph_presentation']['boundary_before'], boundary)
            self.assertEqual(record['source_semantics'][0]['fields']['paragraph_presentation']['first_line'], first)

    def test_unknown_enum_and_changed_hashes_are_rejected(self):
        block = self.block('one', '正文')
        block['paragraph_presentation'] = self.presentation(block, boundary_before='invented', first_line='inherit')
        self.assertTrue(validate_semantic_block(block))
        block['paragraph_presentation']['boundary_before'] = 'unknown'
        self.assertFalse(validate_semantic_block(block))  # preserved for review; never a confirmed boundary
        for key in ('source_sha256', 'source_image_sha256', 'text_sha256'):
            changed = copy.deepcopy(block)
            changed['paragraph_presentation'][key] = 'b' * 64
            self.assertTrue(validate_semantic_block(changed))
        self.image.write_bytes(b'changed native source')
        self.assertTrue(validate_semantic_block(block))

    def test_structured_list_nested_source_styles_and_geometry_survive(self):
        block = self.block('list', '① 中文\n子项', kind='list')
        block['items'] = [dict(text='中文', label='①', marker='circled', styles=[dict(
            start=0, end=2, kind='kai', evidence='source_checked')], children=[dict(
                text='子项', styles=[dict(start=0, end=2, kind='underline', evidence='uncertain')])])]
        block['list_presentation'] = self.presentation(block, kind='steps', outer_indent_ccwd=2,
                                                       hanging_ccwd=1, labelsep_ccwd=0.5)
        self.assertEqual(validate_semantic_block(block), [])
        record = reading_records([block])[0]
        self.assertEqual(record['items'], block['items'])
        self.assertEqual(validate_assembly([block], [record]), [])
        record['items'][0]['children'][0]['styles'] = []
        self.assertTrue(validate_assembly([block], [record]))

    def test_code_grammar_and_groups_unicode_styles_survive(self):
        code = self.block('code', 'λ = 1\n  中文注释()', kind='code')
        code['styles'] = [dict(start=8, end=12, kind='underline', evidence='unreviewed')]
        code['code_presentation'] = self.presentation(code, language='unknown', completeness='fragment',
            outer_indent_ccwd=2, font_policy='source', wrap='none', tabsize=4,
            annotations=[dict(start_line=1, end_line=2, side='right', label='循环')])
        grammar = self.block('grammar', r'$S\to\left\{\begin{array}{l}a\\b\end{array}\right.$', kind='grammar')
        group = self.block('group', '', kind='display_group')
        group['groups'] = [dict(block_ids=['code', 'grammar'], column_weights=[1, 1])]
        for block in (code, grammar, group):
            self.assertIn(block['type'], TYPES)
            self.assertEqual(validate_semantic_block(block), [])
        all_blocks = [code, grammar, group]
        self.assertEqual(validate_assembly(all_blocks, reading_records(all_blocks)), [])
        self.assertTrue({'underline', 'kai'} <= STYLES)
        grammar['text'] = '⎧ a\n⎩ b'
        grammar['text_sha256'] = text_hash(grammar['text'])
        self.assertTrue(validate_semantic_block(grammar))
        code['code_presentation']['annotations'][0]['end_line'] = 3
        self.assertTrue(validate_semantic_block(code))

    def test_canonical_page_checker_accepts_shared_types_and_rejects_stale_identity(self):
        code = self.block('code', 'λ = 1\n中文注释()', kind='code')
        code['code_presentation'] = self.presentation(code, language='unknown', completeness='fragment',
            outer_indent_ccwd=2, font_policy='source', wrap='none')
        page = dict(page=1, printed_page=1, blocks=[code], uncertainties=[], changes=[], viewed_images=['0001/source.png'])
        prepared = dict(width=100, height=100, original=dict(path='0001/source.png', sha256=self.image_hash),
                        images=[dict(path='0001/source.png', sha256=self.image_hash)])
        check_page(page, 1, prepared, set(), self.source_hash)
        code['code_presentation']['text_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            check_page(page, 1, prepared, set(), self.source_hash)

    def test_all_boundaries_are_candidates_without_chinese_keyword_guessing(self):
        a, b = self.block('a', '上一段已经结束。'), self.block('b', '独立名词开头', page=2)
        report = self.report()
        merge_candidates(reading_records([a, b]), report)
        self.assertEqual(len(report['merge_candidates']), 1)
        self.assertTrue(report['merge_candidates'][0]['eligible_after_source_review'])

    def test_word_continuation_merge_and_negative_new_paragraph_keep(self):
        a, b = self.block('a', 'attrib'), self.block('b', 'ution 内容', page=2)
        b['styles'] = [dict(start=0, end=5, kind='underline', evidence='unreviewed')]
        report = self.report()
        result = apply_decisions(reading_records([a, b]), self.decisions(a, b), self.source_hash, report)
        self.assertEqual(result[0]['text'], 'attribution 内容')
        self.assertEqual(result[0]['styles'][0]['start'], 6)
        self.assertEqual(validate_assembly([a, b], result), [])
        result = apply_decisions(reading_records([a, b]), self.decisions(a, b, action='keep'), self.source_hash, self.report())
        self.assertEqual([r['text'] for r in result], ['attrib', 'ution 内容'])
        self.assertEqual(validate_assembly([a, b], result), [])

    def test_list_continuation_preserves_both_item_sources(self):
        a, b = self.block('a', '① 前项', kind='list'), self.block('b', '② 后项', page=2, kind='list')
        a['items'], b['items'] = [dict(text='前项', label='①')], [dict(text='后项', label='②')]
        result = apply_decisions(reading_records([a, b]), self.decisions(a, b, allow_nonparagraph=True),
                                 self.source_hash, self.report())
        self.assertEqual(len(result[0]['items']), 2)
        self.assertEqual(validate_assembly([a, b], result), [])

    def test_structured_list_prose_continuation_needs_reviewed_item_anchor(self):
        a, b = self.block('a', '① attrib', kind='list'), self.block('b', 'ution', page=2)
        a['items'] = [dict(text='attrib', label='①', styles=[])]
        b['styles'] = [dict(start=0, end=5, kind='underline', evidence='unreviewed')]
        with self.assertRaises(ValueError):
            apply_decisions(reading_records([a, b]), self.decisions(a, b, allow_nonparagraph=True),
                            self.source_hash, self.report())
        result = apply_decisions(reading_records([a, b]), self.decisions(a, b, allow_nonparagraph=True, list_item_path=[0]),
                                 self.source_hash, self.report())
        self.assertEqual(result[0]['items'][0]['text'], 'attribution')
        self.assertEqual(result[0]['items'][0]['styles'][0]['start'], 6)
        self.assertEqual(validate_assembly([a, b], result), [])

    def test_footnote_nested_semantics_keep_exact_once_source_accounting(self):
        anchor, note = self.block('anchor', '正文¹'), self.block('note', '¹ 脚注正文')
        note['paragraph_presentation'] = self.presentation(note, boundary_before='new_paragraph', first_line='noindent')
        path = self.root / 'footnotes.json'
        path.write_text(json.dumps([dict(anchor_block_id='anchor', block_ids=['note'], marker='¹',
            source_sha256=self.source_hash, source_checked=True, reason='native marker and body reviewed',
            evidence_image=str(self.image), evidence_image_sha256=self.image_hash)]))
        records = apply_footnotes(reading_records([anchor, note]), path, self.source_hash,
                                  [anchor, note], dict(applied_footnotes=[]))
        self.assertEqual(len(records), 1)
        self.assertEqual(validate_assembly([anchor, note], records), [])
        records[0]['footnotes'][0]['blocks'][0].pop('paragraph_presentation')
        self.assertTrue(validate_assembly([anchor, note], records))

    def test_tampered_and_unreviewed_keep_are_rejected(self):
        a, b = self.block('a', '甲'), self.block('b', '乙', page=2)
        path = self.decisions(a, b, action='keep')
        value = json.loads(path.read_text())
        value['decisions'][0]['right_text_sha256'] = 'b' * 64
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            apply_decisions(reading_records([a, b]), path, self.source_hash, self.report())
        value['decisions'][0]['right_text_sha256'] = b['text_sha256']
        value['decisions'][0]['source_checked'] = False
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            apply_decisions(reading_records([a, b]), path, self.source_hash, self.report())

    def test_manifest_identity_cannot_be_overwritten_by_assembly(self):
        block = self.block('one', 'source')
        for field in ('source_sha256', 'source_image_sha256'):
            changed = copy.deepcopy(block)
            changed[field] = 'f' * 64
            self.assertTrue(validate_semantic_block(changed, self.source_hash, self.image_hash))
        block['block_id'] = 'foreign'
        self.assertTrue(validate_semantic_block(block, self.source_hash, self.image_hash))

    def test_rehashed_reading_text_or_style_tampering_is_rejected(self):
        a, b = self.block('a', 'attrib'), self.block('b', 'ution', page=2)
        a['styles'] = [dict(start=0, end=6, kind='kai', evidence='unreviewed')]
        result = apply_decisions(reading_records([a, b]), self.decisions(a, b), self.source_hash, self.report())
        self.assertEqual(validate_assembly([a, b], result), [])
        changed = copy.deepcopy(result)
        changed[0]['text'] = 'unauthorized replacement'
        changed[0]['text_sha256'] = text_hash(changed[0]['text'])
        self.assertTrue(validate_assembly([a, b], changed))
        for field in ('styles', 'source_styles'):
            changed = copy.deepcopy(result)
            changed[0][field] = []
            self.assertTrue(validate_assembly([a, b], changed))

    def test_separate_description_label_styles_and_normalization_identity(self):
        block = self.block('list', '(1) body', kind='list')
        block['items'] = [dict(text='body', label='(1)', label_styles=[dict(
            start=0, end=3, kind='bold', evidence='unreviewed')])]
        block['list_presentation'] = self.presentation(block, kind='description', label_body_gap_ccwd=0.5,
            label_normalizations=[dict(item_index=0, original='(1)', replacement='1.',
                                       rule_id='source-labels', rule_version='1')])
        self.assertEqual(validate_semantic_block(block), [])
        reading = reading_records([block])
        self.assertEqual(validate_assembly([block], reading), [])
        reading[0]['items'][0]['label_styles'] = []
        self.assertTrue(validate_assembly([block], reading))
        for field, bad in [('original', '(2)'), ('item_index', True), ('replacement', ''), ('rule_version', '')]:
            changed = copy.deepcopy(block)
            changed['list_presentation']['label_normalizations'][0][field] = bad
            self.assertTrue(validate_semantic_block(changed))
        changed = copy.deepcopy(block)
        changed['list_presentation']['label_normalizations'] *= 2
        self.assertTrue(validate_semantic_block(changed))
        changed = copy.deepcopy(block)
        changed['items'][0]['label_styles'][0]['end'] = 4
        self.assertTrue(validate_semantic_block(changed))

    def test_display_caption_members_and_reading_order_are_accounted_once(self):
        code = self.block('code', 'x := 1', kind='code')
        code['code_presentation'] = self.presentation(code, language='unknown', completeness='fragment')
        caption = self.block('caption', '(a) code', kind='caption')
        group = self.block('group', '', kind='display_group')
        group['groups'] = [dict(block_ids=['code'], caption_block_ids=['caption'],
                                reading_order=['code', 'caption'])]
        source = [code, caption, group]
        self.assertEqual(validate_assembly(source, reading_records(source)), [])
        changed = copy.deepcopy(source)
        changed[-1]['groups'][0]['caption_block_ids'] = ['missing']
        self.assertTrue(validate_assembly(changed, reading_records(changed)))
        changed = copy.deepcopy(source)
        changed[-1]['groups'][0]['reading_order'] = ['code']
        self.assertTrue(validate_assembly(changed, reading_records(changed)))

    def test_real_cli_chain_preserves_semantics_and_cross_page_fragments(self):
        # Small real PDF/image artifacts exercise CLI namespaces, producer
        # checkpoint inventories and canonical hashes, rather than bypassing
        # them with direct reading_records calls. This is an integrity fixture,
        # not an OCR quality or visual-format acceptance claim.
        import fitz
        pdf = self.root / 'source.pdf'
        document = fitz.open()
        texts = [['Local sample', '1. item', 'begin\n  x := 1', '(a) left',
                  'while p do\n  x := 2', '(b) right', 'begin | end', 'attrib'], ['ution']]
        for values in texts:
            page = document.new_page(width=300, height=500)
            for index, value in enumerate(values):
                page.insert_text((20, 30 + index * 50), value, fontname='cour', fontsize=10)
        document.save(pdf)
        self.source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
        prepared, jobs = self.root / 'prepared', self.root / 'jobs'
        prepared.mkdir(); jobs.mkdir()
        manifest = dict(schema_version=2, preparation_status='complete', requested_pages=[1, 2],
                        completed_pages=[1, 2], source=dict(pdf_sha256=self.source_hash), pages=[])
        all_blocks = []
        for number, source_page in enumerate(document, 1):
            image = prepared / f'{number:04d}' / 'source.png'
            image.parent.mkdir()
            source_page.get_pixmap().save(image)
            image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
            image_record = dict(path=f'{number:04d}/source.png', sha256=image_hash)
            page_meta = dict(page=number, width=300, height=500,
                             original=image_record, images=[image_record])
            manifest['pages'].append(page_meta)
            specs = ([('h', 'Local sample', 'heading'), ('list', '1. item', 'list'),
                      ('left', 'begin\n  x := 1', 'code'), ('cap-left', '(a) left', 'caption'),
                      ('right', 'while p do\n  x := 2', 'code'), ('cap-right', '(b) right', 'caption'),
                      ('grammar', 'begin | end', 'grammar'), ('group', '', 'display_group'),
                      ('a', 'attrib', 'paragraph')] if number == 1 else [('b', 'ution', 'paragraph')])
            blocks = [self.block(identifier, text, number, kind) for identifier, text, kind in specs]
            for block in blocks:
                block.update(source_image_sha256=image_hash, source_image_absolute_path=str(image))
                identity = dict(schema_version=1, source_sha256=self.source_hash,
                                source_image_sha256=image_hash, text_sha256=block['text_sha256'])
                if block['type'] == 'heading':
                    block['heading_presentation'] = dict(identity, role='local_heading', font_size='body')
                elif block['type'] == 'list':
                    block['items'] = [dict(text='item', label='1.', label_styles=[dict(
                        start=0, end=2, kind='bold', evidence='unreviewed')])]
                    block['list_presentation'] = dict(identity, kind='ordered', label_body_gap_ccwd=0.5)
                elif block['type'] == 'code':
                    block['code_presentation'] = dict(identity, language='unknown', completeness='fragment',
                                                     font_policy='monospace', outer_indent_ccwd=1)
                    block['styles'] = [dict(start=0, end=5, kind='underline', evidence='unreviewed')]
                elif block['type'] == 'grammar':
                    block['alternatives'] = [dict(text='begin', keyword=True), dict(text='end', keyword=True)]
                elif block['type'] == 'display_group':
                    block['groups'] = [dict(columns=[dict(code_id='left', caption_id='cap-left'),
                                                     dict(code_id='right', caption_id='cap-right')],
                                            reading_order=['left', 'cap-left', 'right', 'cap-right'])]
                else:
                    block['paragraph_presentation'] = dict(identity, boundary_before='unknown', first_line='inherit')
            all_blocks.extend(blocks)
            attempt = jobs / 'pages' / f'{number:04d}' / 'attempt-001'
            (attempt / 'input').mkdir(parents=True); (attempt / 'output').mkdir()
            local_manifest = dict(source=manifest['source'], pages=[page_meta])
            (attempt / 'input/manifest.json').write_text(json.dumps(local_manifest))
            data = dict(page=number, printed_page=number, blocks=blocks,
                        changes=[], uncertainties=[], viewed_images=[image_record['path']])
            (attempt / f'output/{number:04d}.json').write_text(json.dumps(data))
            inventory = {path.relative_to(attempt).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in attempt.rglob('*') if path.is_file()}
            (attempt / 'checkpoint.json').write_text(json.dumps(dict(status='validated', exit_code=0, timed_out=False, file_hashes=inventory)))
        document.close()
        (prepared / 'manifest.json').write_text(json.dumps(manifest))
        (jobs / 'contract.json').write_text(json.dumps(dict(source_pdf_sha256=self.source_hash, expected_pages=[1, 2])))
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        canonical, book = self.root / 'canonical', self.root / 'book'
        common = ['--manifest', str(prepared / 'manifest.json'), '--source-pdf', str(pdf), '--expected-pages', '1-2']
        result = subprocess.run([sys.executable, str(scripts / 'canonicalize_pages.py'), *common,
                                 '--jobs-dir', str(jobs), '--output', str(canonical)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        patch_path = self.root / 'bad-evidence-patch.json'
        patch_path.write_text(json.dumps([dict(page=1, block_id='h', field='text', before='Local sample',
            after='Unauthorized replacement', source_sha256=self.source_hash,
            source_image_sha256=manifest['pages'][0]['original']['sha256'], evidence='source_checked',
            reason='fixture evidence identity rejection', evidence_image=str(self.image))]))
        rejected = subprocess.run([sys.executable, str(scripts / 'canonicalize_pages.py'), *common,
            '--jobs-dir', str(jobs), '--output', str(self.root / 'rejected'), '--patches', str(patch_path)],
            capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('Patch evidence image hash differs', rejected.stderr)
        self.assertFalse((self.root / 'rejected/manifest.json').exists())
        a, b = all_blocks[-2:]
        # Group is before a; a is the last fragment on page 1, b first on 2.
        decision = self.decisions(a, b)
        value = json.loads(decision.read_text())
        value['decisions'][0].update(left_source_image_sha256=a['source_image_sha256'],
                                      right_source_image_sha256=b['source_image_sha256'])
        decision.write_text(json.dumps(value))
        result = subprocess.run([sys.executable, str(scripts / 'assemble_book.py'), *common,
                                 '--pages-dir', str(canonical / 'pages'), '--merge-decisions', str(decision),
                                 '--output', str(book)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = [json.loads(line) for line in (book / 'source-blocks.jsonl').read_text().splitlines()]
        reading = [json.loads(line) for line in (book / 'blocks.jsonl').read_text().splitlines()]
        self.assertEqual(validate_assembly(source, reading), [])
        by_id = {block['block_id']: block for block in reading}
        self.assertEqual(by_id['a']['text'], 'attribution')
        self.assertEqual(by_id['a']['text_source_block_ids'], ['a', 'b'])
        self.assertEqual([row['block_id'] for row in by_id['a']['source_semantics']], ['a', 'b'])
        tampered = copy.deepcopy(reading)
        next(row for row in tampered if row['block_id'] == 'a')['merged_presentations']['paragraph_presentation']['first_line'] = 'noindent'
        self.assertTrue(validate_assembly(source, tampered))
        for block in all_blocks[:-2]:
            for field in SEMANTIC_FIELDS:
                if field in block:
                    self.assertEqual(by_id[block['id']][field], block[field])

    def test_annotation_offsets_and_caption_alignment_are_bounded(self):
        code = self.block('code', 'begin\nend', kind='code')
        code['code_presentation'] = self.presentation(code, language='unknown', completeness='fragment',
            annotations=[dict(start_line=1, end_line=2, side='right', label='two lines',
                              x_offset_ccwd=-6, start_yshift_em=-3, end_yshift_em=3)])
        self.assertEqual(validate_semantic_block(code), [])
        for key, invalids in [('x_offset_ccwd', [-6.1, 6.1, float('inf'), True]),
                              ('start_yshift_em', [-3.1, 3.1, float('nan'), '0']),
                              ('end_yshift_em', [-3.1, 3.1, float('-inf'), None])]:
            for value in invalids:
                changed = copy.deepcopy(code)
                changed['code_presentation']['annotations'][0][key] = value
                self.assertTrue(validate_semantic_block(changed))
        group = self.block('group', '', kind='display_group')
        group['groups'] = [dict(block_ids=['left', 'right'], caption_alignment='bottom')]
        self.assertEqual(validate_semantic_block(group), [])
        group['groups'][0]['caption_alignment'] = 'natural'
        self.assertEqual(validate_semantic_block(group), [])
        group['groups'][0]['caption_alignment'] = 'invented'
        self.assertTrue(validate_semantic_block(group))
if __name__ == '__main__':
    unittest.main()
