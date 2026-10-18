"""Exercise the public exporter, including real PDF text and admission refusal."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from presentation_render import boundary_separator
from latex_numbering import heading_record
from presentation_render import validate_fragment_presentations
from export_latex import InlineRenderer, select_figure


@unittest.skipUnless(shutil.which('xelatex'), 'XeLaTeX required')
class NativeExportTests(unittest.TestCase):
    def test_literal_pointer_stars_do_not_close_markdown_emphasis(self):
        literal = '前者是 *p+1，而后者也是 *p+1。'
        self.assertEqual(InlineRenderer(literal).render(), literal)
        self.assertIn(r'\textit{强调}', InlineRenderer('正常 *强调*').render())

    def test_all_source_fallback_statuses_require_fidelity_assessment(self):
        for status in ['retained_source', 'accepted_source']:
            figure = dict(id='f', acceptance={'status': status}, display_width_mm=10,
                          final_assets={'latex': {'path': str(self.image)}})
            with self.assertRaisesRegex(ValueError, 'fallback_assessment'):
                select_figure(figure, self.root, 170)

    def setUp(self):
        import fitz
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = 'a' * 64
        self.image = self.root / 'source.png'
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 30))
        pix.clear_with(255)
        pix.save(self.image)
        self.image_hash = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.template = self.root / 'template'
        self.template.mkdir()
        (self.template / 'book-template.tex').write_text(r'''\documentclass[fontset=none]{ctexart}
\input{book-config.tex}
\usepackage{book-style}
\begin{document}
@@CHAPTER_INPUTS@@
\end{document}
''')
        (self.template / 'book-style.sty').write_text(r'''\usepackage[a4paper,margin=20mm]{geometry}
\usepackage{fontspec,amsmath,graphicx}
\setmainfont{DejaVu Serif}
\setmonofont{DejaVu Sans Mono}
\setCJKmainfont{FandolSong-Regular.otf}
\setCJKmonofont{FandolSong-Regular.otf}
\setlength{\parindent}{2em}
''')
        self.profile = self.root / 'profile.json'
        self.profile.write_text(json.dumps(dict(schema_version=1, status='draft', version='v1',
            source_sha256=self.source, template_dir=str(self.template), representative_pages=[27],
            tested_families=['code', 'grammar', 'list', 'paragraph', 'heading'], template_rules={
                'code': {'font_family': 'DejaVu Sans Mono', 'font_size_pt': 10, 'line_spacing_pt': 14}})))

    def tearDown(self):
        self.tmp.cleanup()

    def block(self, ident, text, kind='paragraph'):
        return dict(id=ident, block_id=ident, text=text, type=kind, page=27, pages=[27],
            source_sha256=self.source, source_image_sha256=self.image_hash, chapter_id='excerpt',
            text_sha256=hashlib.sha256(text.encode()).hexdigest(), styles=[], evidence='source_checked')

    def meta(self, block, **fields):
        return dict(schema_version=1, source_sha256=self.source, source_image_sha256=self.image_hash,
            text_sha256=block['text_sha256'], review=dict(source_checked=True, reviewer='fixture',
            evidence_path=str(self.image)), **fields)

    def command(self, blocks, preflight=True):
        path = self.root / 'blocks.jsonl'
        path.write_text('\n'.join(json.dumps(b, ensure_ascii=False) for b in blocks))
        command = [sys.executable, str(ROOT / 'scripts/export_latex.py'), '--blocks', str(path),
            '--output', str(self.root / 'dist'), '--expected-pages', '27',
            '--format-profile', str(self.profile), '--compile']
        if preflight:
            command += ['--preflight-mode']
        return command

    def test_production_without_admission_fails_before_output(self):
        result = subprocess.run(self.command([self.block('p', '正文')], False), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--template-ready', result.stderr)
        self.assertFalse((self.root / 'dist').exists())

    def test_reviewed_math_groups_compile_and_preserve_member_coverage(self):
        import fitz
        a = self.block('a', r'\[x=1+\]', 'formula')
        b = self.block('b', r'\[2=3\]', 'formula')
        lead = self.block('lead', r'解 \(y=4\)', 'proof')
        members = {v['block_id']: dict(text_sha256=v['text_sha256'], source_sha256=self.source,
            source_image_sha256=self.image_hash, evidence_path=str(self.image)) for v in [a, b, lead]}
        common = dict(schema_version=1, members=members,
                      review=dict(source_checked=True, reviewer='source fixture'))
        decisions = dict(source_sha256=self.source, groups=[
            dict(common, kind='aligned_rows', block_ids=['a', 'b'],
                 rows=[dict(block_id='a', align_at=1), dict(block_id='b', align_at=1)]),
            dict(common, kind='labeled_display', block_ids=['lead'], label='解')])
        path = self.root / 'math.json'
        path.write_text(json.dumps(decisions, ensure_ascii=False))
        result = subprocess.run(self.command([a, b, lead]) + ['--math-presentations', str(path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tex = next((self.root / 'dist/chapters').glob('*.tex')).read_text()
        self.assertEqual(tex.count(r'\begin{aligned}'), 1)
        self.assertIn(r'\begin{flalign*}', tex)
        report = json.loads((self.root / 'dist-export.json').read_text())
        self.assertEqual({r['block_id'] for r in report['blocks']}, {'a', 'b', 'lead'})
        with fitz.open(self.root / 'dist/main.pdf') as pdf:
            text = ''.join(page.get_text() for page in pdf)
        self.assertIn('解', text)

    def test_merged_review_identity_and_group_candidates_cannot_bypass_export(self):
        merged = self.block('merged', '合并文')
        fragment = self.block('fragment', '原片段')
        metadata = self.meta(fragment, boundary_before='new_paragraph', first_line='indent')
        metadata['source_sha256'] = 'f' * 64
        merged['merged_presentations'] = {'paragraph_presentation': metadata}
        merged['source_semantics'] = [dict(block_id='fragment', text_sha256=fragment['text_sha256'],
            fields={'paragraph_presentation': metadata})]
        merged['sources'] = [dict(block_id='fragment', source_sha256=self.source,
            text_sha256=fragment['text_sha256'], source_image_sha256=self.image_hash)]
        result = subprocess.run(self.command([merged]), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Fragment presentation source_sha256 mismatch', result.stderr)
        group = self.block('candidate', '', 'display_group')
        group['groups'] = [dict(columns=[dict(code_id='left', caption_id='cap'),
                                        dict(code_id='right', caption_id='cap2')])]
        result = subprocess.run(self.command([group]), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('source-reviewed group_presentation', result.stderr)

    def test_footnote_child_bad_presentation_is_rejected(self):
        anchor = self.block('anchor', '正文①')
        child = self.block('child', '① 脚注')
        child['paragraph_presentation'] = self.meta(child, boundary_before='new_paragraph', first_line='noindent')
        child['paragraph_presentation']['source_sha256'] = 'f' * 64
        anchor['footnotes'] = [dict(marker='①', anchor_block_id='anchor', blocks=[child],
                                   review={'source_checked': True})]
        result = subprocess.run(self.command([anchor]), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('source_sha256 mismatch', result.stderr)

    def test_public_export_native_groups_lists_and_paragraph_boundaries(self):
        import fitz
        p = self.block('intro', '公式前文字')
        f = self.block('formula', '$$x=1$$', 'formula')
        continuation = self.block('continuation', '公式后同段文字')
        continuation['paragraph_presentation'] = self.meta(continuation, boundary_before='continue', first_line='inherit')
        fresh = self.block('new', '真正的新段')
        fresh['paragraph_presentation'] = self.meta(fresh, boundary_before='new_paragraph', first_line='indent')
        head = self.block('local', '1. 局部标题', 'heading')
        head['heading_presentation'] = self.meta(head, role='local_heading', font_size='body', indent_ccwd=2)
        listing = self.block('list', '媒体性 原文\n（4） 属性', 'list')
        listing['items'] = [dict(label='媒体性', text='标签之后的正文'), dict(label='（4）', text='一致标签')]
        listing['list_presentation'] = self.meta(listing, kind='steps', outer_indent_ccwd=0,
            labelsep_ccwd=0.5, labelwidth_ccwd=4, hanging_ccwd=4.5,
            label_normalizations=[dict(item_index=1, original='（4）', replacement='(4)', rule_id='labels', rule_version='v1')])
        blocks = [p, f, continuation, fresh, head, listing]
        for ident, text in [('left', 'if p then\n    λ := 1;\nend'), ('right', 'while p do\n    x := 2;')]:
            code = self.block(ident, text, 'code')
            code['code_presentation'] = self.meta(code, language='unknown', completeness='fragment',
                                                wrap='none', font_policy='monospace')
            blocks += [code, self.block(ident+'caption', '('+ident+') 程序', 'caption')]
        group = self.block('pair', '', 'display_group')
        group['group_presentation'] = self.meta(group)
        group['groups'] = [dict(columns=[dict(code_id='left', caption_id='leftcaption'),
                                        dict(code_id='right', caption_id='rightcaption')], caption_alignment='bottom')]
        blocks.append(group)
        grammar = self.block('grammar', 'ADD begin | end', 'grammar')
        grammar['alternatives'] = [dict(text='begin', keyword=True), dict(text='end', keyword=True)]
        blocks.append(grammar)
        annotated = self.block('annotated', 'begin\nend', 'code')
        annotated['code_presentation'] = self.meta(annotated, language='unknown', completeness='fragment',
            wrap='none', font_policy='native', annotations=[dict(start_line=1, end_line=2, label_block_id='annotation-label')])
        blocks += [annotated, self.block('annotation-label', '唯一注解')]
        before = json.dumps(blocks, sort_keys=True)
        result = subprocess.run(self.command(blocks), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.dumps(blocks, sort_keys=True), before)
        report = json.loads((self.root / 'dist-export.json').read_text())
        self.assertFalse(report['format_admission']['production'])
        self.assertEqual(report['compile']['overfull_box_count'], 0)
        self.assertEqual(report['compile']['missing_glyph_count'], 0)
        self.assertEqual(len(report['blocks']), len(blocks))
        self.assertEqual(len({b['block_id'] for b in report['blocks']}), len(blocks))
        source = report['special_text_assets']['left']['source_path']
        self.assertEqual((self.root / 'dist' / source).read_bytes(), blocks[6]['text'].encode())
        tex = next((self.root / 'dist/chapters').glob('*.tex')).read_text()
        self.assertIn('公式后同段文字\n\n\\indent 真正的新段', tex)
        self.assertNotIn('\\subsection*{1.', tex)
        self.assertNotIn("'text':", tex)
        with fitz.open(self.root / 'dist/main.pdf') as pdf:
            self.assertTrue(all(not page.get_images() for page in pdf))
            text = '\n'.join(page.get_text() for page in pdf)
            self.assertEqual(text.count('if p then'), 1)
            self.assertEqual(text.count('while p do'), 1)
            self.assertEqual(text.count('唯一注解'), 1)
            self.assertIn('(4)', text)
            self.assertNotIn('（4）', text)
            spans = [span for page in pdf for b in page.get_text('dict')['blocks']
                     for line in b.get('lines', []) for span in line['spans']]
            left = next(s for s in spans if '(left)' in s['text'])
            right = next(s for s in spans if '(right)' in s['text'])
            self.assertAlmostEqual(left['bbox'][1], right['bbox'][1], places=1)
            custom = next(s for s in spans if s['text'] == 'begin')
            self.assertIn('DejaVuSansMono', custom['font'])
        # No-figure books still need the shared source packager. Code assets are
        # real render dependencies, while drawing runtimes and PDF are excluded.
        archive = self.root / 'source.zip'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/package_latex.py'),
            '--dist', str(self.root / 'dist'), '--export-manifest', str(self.root / 'dist-export.json'),
            '--zip', str(archive)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(archive) as package:
            self.assertIsNone(package.testzip())
            self.assertEqual(package.read(source), blocks[6]['text'].encode())
            self.assertNotIn('main.pdf', package.namelist())
            self.assertFalse(any(n.startswith('figure-code/') for n in package.namelist()))


    def test_long_grammar_keeps_its_introduction_across_a_real_page_boundary(self):
        import fitz
        filler = self.block('filler', '\n'.join('source line '+str(i) for i in range(40)), 'code')
        filler['code_presentation'] = self.meta(filler, language='unknown', completeness='fragment', wrap='none')
        intro = self.block('intro', 'Introduction to these productions:', 'example')
        grammar = self.block('grammar-flow', '\n'.join('rule%02d ::= token' % i for i in range(20)), 'grammar')
        grammar['grammar_lines'] = [dict(text=line) for line in grammar['text'].splitlines()]
        result = subprocess.run(self.command([filler,intro,grammar]),capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        doc = fitz.open(self.root/'dist/main.pdf')
        intro_page = [i for i,p in enumerate(doc) if 'Introduction to these productions:' in p.get_text()]
        grammar_page = [i for i,p in enumerate(doc) if 'rule00' in p.get_text()]
        self.assertEqual(intro_page,grammar_page)
        self.assertTrue(intro_page)


class BoundaryTests(unittest.TestCase):
    def test_unknown_or_unreviewed_relations_cannot_render(self):
        for meta in [dict(boundary_before='unknown', review={'source_checked': True}),
                     dict(boundary_before='continue', review={'source_checked': False})]:
            with self.assertRaises(ValueError):
                boundary_separator({'paragraph_presentation': meta}, '续段', lambda _: False)

    def test_chapter_zero_retains_native_source_number(self):
        record = heading_record(dict(type='heading', id='zero', chapter_id='chapter-0',
                                     heading_depth=1, text='第0章 绪论'))
        self.assertEqual(record['number_parts'], [0])



if __name__ == '__main__':
    unittest.main()
