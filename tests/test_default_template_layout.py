"""Render through the public exporter; check real list geometry in its PDF.

Synthetic input tests layout only. It is never a book-source acceptance record.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('xelatex'), 'XeLaTeX required')
class DefaultTemplateLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import fitz
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        source = hashlib.sha256(b'synthetic default-template layout regression').hexdigest()
        blocks = []

        def block(ident, text, kind='paragraph', page=1, **fields):
            value = dict(id=ident, block_id=ident, text=text, type=kind, page=page, pages=[page],
                         chapter_id=f'chapter-{page}', source_sha256=source, styles=[], **fields)
            blocks.append(value)
            return value

        block('h1', '第1章 编号与边界', 'heading', heading_depth=1)
        block('margin1', '正文边界锚点，用于测量本页正常正文边界。')
        block('wide', 'synthetic explicit labels', 'list', list_kind='ordered', items=[
            dict(label=label, text='首行正文锚点。' + '折行正文用于核对悬挂缩进，保证第一行与续行起点一致。' * 3)
            for label in ['（1）', '（10）', '（100）']])
        block('auto', 'synthetic automatic labels', 'list', list_kind='ordered', items=[
            dict(text=f'自动编号项目{i}。') for i in range(1, 13)])
        block('nested', 'synthetic nested list', 'list', list_kind='ordered', items=[
            dict(text='外层甲', children=[dict(text='内层甲'), dict(text='内层乙')]),
            dict(text='外层乙')])
        block('after1', '列表之后的独立段落锚点。')
        block('h2', '第2章 说明与分段', 'heading', page=2, heading_depth=1)
        block('margin2', '正文边界锚点，用于测量本页正常正文边界。', page=2)
        block('desc', 'synthetic description', 'list', page=2, list_kind='description', items=[
            dict(label='媒体性', text='说明列表不应凭空添加圆点。' * 4),
            dict(marker='•', label='完整性', text='明确指定的一个圆点应当保留。')])
        block('paragraphs', 'synthetic paragraphs', 'list', page=2, items=[
            dict(text='项目首段锚点。\n\n项目次段锚点。')])
        block('after2', '列表之后的独立段落锚点。', page=2)
        block('term-items', 'synthetic unordered marker-plus-label items', 'list', page=2,
              list_kind='unordered', items=[
                  dict(marker='•', label='无序标签甲', text='无序说明正文甲。'),
                  dict(marker='•', label='无序标签乙', text='无序说明正文乙。')])
        block('h3', '第3章 程序块间距', 'heading', page=3, heading_depth=1)
        block('code-before', '程序前正文锚点。', page=3)
        code = block('code', 'begin\n    中文注释\nend', 'code', page=3,
                     annotations=[dict(start_line=1, end_line=3, label='程序注解')])
        # A generated synthetic image authenticates only this test fixture.
        with fitz.open() as fixture:
            p = fixture.new_page()
            p.insert_text((30, 40), code['text'], fontname='china-s')
            p.get_pixmap().save(cls.root/'synthetic-code-source.png')
        image_hash = hashlib.sha256((cls.root/'synthetic-code-source.png').read_bytes()).hexdigest()
        code['source_image_sha256'] = image_hash
        code['code_presentation'] = dict(schema_version=1, language='unknown', completeness='fragment',
            font_policy='monospace', wrap='none', source_sha256=source, source_image_sha256=image_hash,
            text_sha256=hashlib.sha256(code['text'].encode()).hexdigest(),
            review=dict(source_checked=True, reviewer='synthetic-layout-fixture (not a book review)',
                        evidence_path=str(cls.root/'synthetic-code-source.png')))
        block('code-after', '程序后正文锚点。', page=3)
        block('h4', '第4章 公式留白', 'heading', page=4, heading_depth=1)
        block('math-before', '公式上方锚点。', page=4)
        block('math', r'\[a+b=c\]', 'formula', page=4)
        block('math-after', '公式下方锚点。', page=4)
        block('math-long-before', '较长引导句用于检查公式之前的正文接近整行宽度时是否仍保留适度间距。', page=4)
        block('math-long', r'\[a+b=c\]', 'formula', page=4)
        block('math-long-after', '长引导公式下方锚点。', page=4)
        cls.input_path = cls.root / 'blocks.jsonl'
        cls.input_path.write_text('\n'.join(json.dumps(b, ensure_ascii=False) for b in blocks))
        profile = dict(schema_version=1, status='draft', version='layout-regression-v1',
                       source_sha256=source, template_dir=str(ROOT / 'assets/latex'),
                       representative_pages=[1, 2, 3, 4], tested_families=['list', 'paragraph', 'heading', 'code', 'formula'])
        (cls.root / 'profile.json').write_text(json.dumps(profile))
        command = [sys.executable, str(ROOT/'scripts/export_latex.py'), '--blocks', str(cls.input_path),
                   '--output', str(cls.root/'dist'), '--expected-pages', '1-4',
                   '--format-profile', str(cls.root/'profile.json'), '--preflight-mode', '--compile']
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode:
            cls.tmp.cleanup()
            raise AssertionError(result.stdout + result.stderr)
        cls.pdf = fitz.open(cls.root/'dist/main.pdf')

    @classmethod
    def tearDownClass(cls):
        cls.pdf.close()
        cls.tmp.cleanup()

    def page_with(self, text):
        return next(page for page in self.pdf if text in page.get_text())

    def test_long_labels_remain_inside_the_text_area(self):
        import fitz
        from PIL import Image
        page = self.page_with('编号与边界')
        # The source paragraph has the default two-CJK-character indentation.
        left = page.search_for('正文边界锚点')[0].x0 - 24
        label = page.search_for('（100）')[0]
        # CJK punctuation's logical box includes blank side bearings. Measure
        # actual ink; text-extraction rectangles incorrectly flag that padding.
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), colorspace=fitz.csGRAY,
                             clip=fitz.Rect(0, label.y0, left + 10, label.y1))
        mask = Image.frombytes('L', (pix.width, pix.height), pix.samples).point(
            lambda pixel: 255 if pixel < 90 else 0)
        self.assertIsNotNone(mask.getbbox())
        self.assertGreaterEqual(mask.getbbox()[0] / 3, left - 0.5)
        # The longest label, its gap and all text remain within the text block.
        for rect in page.search_for('首行正文锚点'):
            self.assertGreater(rect.x0, label.x1)
        self.assertIn('12.', page.get_text())
        self.assertIn('2.', page.get_text(), 'measuring nested labels must not corrupt outer counters')

    def test_description_has_only_source_requested_markers(self):
        page = self.page_with('说明与分段')
        # One explicit description bullet, one ordinary bullet, and two term items.
        self.assertEqual(page.get_text().count('•'), 4)

    def test_item_paragraph_and_following_body_indentation(self):
        page = self.page_with('说明与分段')
        first = page.search_for('项目首段锚点')[0]
        second = page.search_for('项目次段锚点')[0]
        self.assertAlmostEqual(second.x0 - first.x0, 24, delta=0.5)
        anchor = page.search_for('正文边界锚点')[0]
        after = page.search_for('列表之后的独立段落锚点')[0]
        self.assertAlmostEqual(after.x0, anchor.x0, delta=0.5)

    def test_public_export_compiles_cleanly_and_copies_selected_style(self):
        report = json.loads((self.root/'dist-export.json').read_text())
        self.assertEqual(report['compile']['missing_glyph_count'], 0)
        self.assertEqual(report['compile']['overfull_box_count'], 0)
        self.assertFalse(report['format_admission']['production'])
        self.assertEqual((self.root/'dist/book-style.sty').read_bytes(),
                         (ROOT/'assets/latex/book-style.sty').read_bytes())

    def test_source_archive_rebuild_uses_xelatex_without_editor_configuration(self):
        import zipfile
        archive = self.root / 'portable-source.zip'
        result = subprocess.run([sys.executable, str(ROOT/'scripts/package_latex.py'),
            '--dist', str(self.root/'dist'), '--export-manifest', str(self.root/'dist-export.json'),
            '--zip', str(archive)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        replay = self.root / 'relocated source'
        with zipfile.ZipFile(archive) as package:
            self.assertIn('BUILD.txt', package.namelist())
            self.assertIn('.latexmkrc', package.namelist())
            self.assertNotIn('main.pdf', package.namelist())
            package.extractall(replay)
        self.assertTrue((replay/'main.tex').read_text().startswith(
            '% !TeX program = xelatex\n% !TeX encoding = UTF-8\n'))
        instructions = (replay/'BUILD.txt').read_text()
        self.assertIn('Noto Serif CJK SC', instructions)
        self.assertNotIn(str(ROOT), instructions)
        if not shutil.which('latexmk'):
            self.skipTest('latexmk required for the relocated build')
        # No -xelatex CLI override: the delivered rc must choose the engine.
        result = subprocess.run(['latexmk', '-norc', '-r', '.latexmkrc', 'main.tex'], cwd=replay,
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((replay/'main.pdf').is_file())
        self.assertIn('XeTeX', (replay/'main.log').read_text())

    @unittest.skipUnless(shutil.which('fc-match'), 'fontconfig required for selecting a test font')
    def test_body_fonts_are_opt_in_and_do_not_change_accepted_source(self):
        import zipfile
        font = Path(subprocess.check_output(['fc-match','-f','%{file}','Noto Serif'], text=True).strip())
        license_file = self.root/'font-license-fixture.txt'
        license_file.write_text('Test archive fixture: license text is copied without interpretation.\n')
        original_main = (self.root/'dist/main.tex').read_bytes()
        original_build = (self.root/'dist/BUILD.txt').read_bytes()
        archive = self.root/'with-fonts.zip'
        base = [sys.executable, str(ROOT/'scripts/package_latex.py'),
                '--dist', str(self.root/'dist'), '--export-manifest', str(self.root/'dist-export.json')]
        result = subprocess.run(base + ['--zip',str(archive),'--include-body-fonts',
            '--body-font',str(font),'--body-font-license',str(license_file)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(archive) as package:
            manifest = json.loads(package.read('body-fonts/manifest.json'))
            self.assertEqual(package.read(manifest['fonts'][0]['path']), font.read_bytes())
            self.assertEqual(package.read(manifest['licenses'][0]['path']), license_file.read_bytes())
            self.assertEqual(package.read('main.tex'), original_main)
            self.assertIn('本 ZIP 已附带', package.read('BUILD.txt').decode())
            self.assertNotIn(str(font), package.read('body-fonts/manifest.json').decode())
        self.assertEqual((self.root/'dist/BUILD.txt').read_bytes(), original_build)
        self.assertFalse((self.root/'dist/body-fonts').exists())
        # A later default packaging call must not accidentally reuse earlier fonts.
        without = self.root/'without-fonts.zip'
        result = subprocess.run(base + ['--zip',str(without)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(without) as package:
            self.assertFalse(any(n.startswith('body-fonts/') for n in package.namelist()))
            self.assertIn('字体文件未随本包提供', package.read('BUILD.txt').decode())

    @unittest.skipUnless(shutil.which('pdflatex'), 'pdfLaTeX required for rejection check')
    def test_wrong_engine_fails_with_explicit_xetex_requirement(self):
        wrong_build = self.root/'wrong-engine'
        wrong_build.mkdir()
        result = subprocess.run(['pdflatex','-interaction=nonstopmode','-halt-on-error',
            '-output-directory='+str(wrong_build),'main.tex'], cwd=self.root/'dist',
            capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('XeTeX', result.stdout)
        self.assertIn('required', result.stdout.lower())
        self.assertNotIn('font cannot be found', result.stdout)

    def test_native_code_has_space_on_both_sides_and_a_registered_cjk_font(self):
        page = self.page_with('程序块间距')
        before = page.search_for('程序前正文锚点')[0]
        first = page.search_for('begin')[0]
        last = page.search_for('end')[0]
        after = page.search_for('程序后正文锚点')[0]
        self.assertGreater(first.y0 - before.y1, 5)
        self.assertGreater(after.y0 - last.y1, 5)
        log = (self.root/'dist-build/main.log').read_text()
        self.assertNotIn('Unknown CJK family', log)

    def test_unordered_term_label_is_in_the_body_with_its_marker_preserved(self):
        page = self.page_with('说明与分段')
        term = page.search_for('无序标签甲')[0]
        body = page.search_for('无序说明正文甲')[0]
        self.assertGreater(body.x0, term.x1)
        # The term begins at the item's normal text start, not in the label box.
        item = page.search_for('项目首段锚点')[0]
        self.assertAlmostEqual(term.x0, item.x0, delta=0.5)
        spans = [span for block in page.get_text('dict')['blocks']
                 for line in block.get('lines', []) for span in line['spans']]
        self.assertTrue(any('无序标签甲' in s['text'] and 'Sans' in s['font'] for s in spans))

    def test_term_bullet_has_readable_size_and_is_visually_centered(self):
        import fitz
        from PIL import Image
        page = self.page_with('说明与分段')
        term = page.search_for('无序标签甲')[0]
        bullet = min(page.search_for('•'), key=lambda r: abs(r.y0 - term.y0))

        def ink(rect):
            pix = page.get_pixmap(matrix=fitz.Matrix(6, 6), colorspace=fitz.csGRAY, clip=rect)
            mask = Image.frombytes('L', (pix.width, pix.height), pix.samples).point(
                lambda pixel: 255 if pixel < 90 else 0)
            box = mask.getbbox()
            self.assertIsNotNone(box)
            return ((pix.y + box[1]) / 6, (pix.y + box[3]) / 6)

        dot_top, dot_bottom = ink(bullet)
        term_top, term_bottom = ink(term)
        self.assertGreaterEqual(dot_bottom - dot_top, 3.2)
        self.assertLessEqual(dot_bottom - dot_top, 3.9)
        self.assertLessEqual(abs((dot_top + dot_bottom - term_top - term_bottom) / 2), 0.8)

    def test_single_line_formula_ink_gaps_are_compact_after_chapter_font_reset(self):
        import fitz
        from PIL import Image
        page = self.page_with('公式留白')
        before = page.search_for('公式上方锚点')[0]
        after = page.search_for('公式下方锚点')[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), colorspace=fitz.csGRAY)
        image = Image.frombytes('L', (pix.width, pix.height), pix.samples)
        bands, start = [], None
        for y in range(int(before.y0 * 3), int(after.y1 * 3) + 1):
            ink = image.crop((0, y, pix.width, y + 1)).point(
                lambda pixel: 255 if pixel < 90 else 0).getbbox() is not None
            if ink and start is None:
                start = y
            if not ink and start is not None:
                bands.append((start / 3, y / 3))
                start = None
        if start is not None:
            bands.append((start / 3, (int(after.y1 * 3) + 1) / 3))
        self.assertEqual(len(bands), 3, bands)
        above, below = bands[1][0] - bands[0][1], bands[2][0] - bands[1][1]
        self.assertGreaterEqual(above, 3)
        self.assertLessEqual(above, 8)
        self.assertGreaterEqual(below, 3)
        self.assertLessEqual(below, 9)


if __name__ == '__main__':
    unittest.main()
