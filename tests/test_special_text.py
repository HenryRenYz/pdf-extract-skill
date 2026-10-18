"""Regressions for source preservation and the actual native TeX text layer."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.special_text import (
    collect_special_assets, compose_code_group, render_special_block,
    validate_special_block, SpecialTextError, SPECIAL_TEXT_PREAMBLE,
)


class SpecialTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_source_utf8_chars_whitespace_and_render_isolation(self):
        text = 'α := %_#${}\\;\n    \\end{Verbatim}\n\t中文' + chr(0xE000)
        code = {'id': 'c', 'type': 'code', 'text': text,
                'code_presentation': {'tabsize': 4},
                'styles': [{'start': 0, 'end': 1, 'kind': 'underline'}]}
        assets = collect_special_assets([code], self.path)
        asset = assets['c']
        self.assertEqual((self.path / asset['source_path']).read_bytes(), text.encode())
        self.assertEqual(asset['source_sha256'], hashlib.sha256(text.encode()).hexdigest())
        render = (self.path / asset['render_path']).read_text()
        self.assertNotIn(chr(0xE000), asset['commandchars'])
        e, o, c = asset['commandchars']
        self.assertEqual(render, e + 'BookCodeUnderline' + o + 'α' + c + text[1:])
        self.assertIn('\n    \\end{Verbatim}', render)
        self.assertIn('\n\t中文', render)

    def test_crossing_styles_are_bound_to_original_unicode_ranges(self):
        code = {'id': 'c', 'type': 'code', 'text': 'ab\n中文x', 'styles': [
            {'start': 1, 'end': 5, 'kind': 'underline'},
            {'start': 3, 'end': 6, 'kind': 'bold'},
        ]}
        assets = collect_special_assets([code], self.path)
        e, o, c = assets['c']['commandchars']
        rendered = (self.path / assets['c']['render_path']).read_text()
        expected = 'a' + e+'BookCodeUnderline'+o+'b'+c+'\n'+ e+'BookCodeUnderline'+o+e+'textbf'+o+'中文'+c+c + e+'textbf'+o+'x'+c
        self.assertEqual(rendered, expected)

    def test_invalid_styles_tabs_and_annotation_ranges_fail(self):
        base = {'id': 'c', 'type': 'code', 'text': 'a\nb'}
        invalid = [dict(base, styles=[{'start': 0, 'end': 7, 'kind': 'underline'}]),
                   dict(base, styles=[{'start': 0, 'end': 1, 'kind': 'unknown'}]),
                   dict(base, annotations=[{'start_line': 0, 'end_line': 2, 'label': 'bad'}]),
                   dict(base, annotations=[{'start_line': 1, 'end_line': 3, 'label': 'bad'}]),
                   dict(base, annotations=[{'start_line': 1, 'end_line': 2, 'side': 'left', 'label': 'bad'}]),
                   dict(base, text='\ta'),
                   dict(base, code_presentation={'wrap':'soft'}, annotations=[{'start_line':1,'end_line':2,'label':'bad'}])]
        for code in invalid:
            with self.subTest(code=code), self.assertRaises(SpecialTextError):
                validate_special_block(code)

    def test_stale_assets_and_duplicate_group_ownership_fail(self):
        code = {'id': 'c', 'type': 'code', 'text': 'a'}
        assets = collect_special_assets([code], self.path)
        with self.assertRaises(SpecialTextError):
            render_special_block(dict(code, text='b'), assets)
        caption = {'id':'cap','type':'caption','text':'caption'}
        group = {'columns':[{'code_id':'c','caption_id':'cap'}]*2}
        with self.assertRaises(SpecialTextError):
            compose_code_group(group, {'c':code,'cap':caption}, assets)

    def test_grammar_requires_deliberate_native_latex(self):
        with self.assertRaises(SpecialTextError):
            render_special_block({'id':'g','type':'grammar','text':'begin'})
        with self.assertRaises(SpecialTextError):
            render_special_block({'id':'g','type':'grammar','text':'source','latex':r'\input{evil}'})
        with self.assertRaises(SpecialTextError):
            render_special_block({'id':'g','type':'grammar','text':'source','latex':r'\begin{filecontents}{bad}x\end{filecontents}'})
        result = render_special_block({'id':'g','type':'grammar','text':'begin | end',
                                      'alternatives':[{'text':'begin','keyword':True},{'text':'end','keyword':True}]})
        self.assertIn(r'\left\{\begin{array}{l}', result)
        self.assertIn(r'\underline{begin}', result)

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('pdftotext'), 'native TeX tools required')
    def test_native_text_layer_braces_styles_and_pair(self):
        # Render self-authored content in this test's isolated workspace.
        stage = self.path / 'native-renderer'
        stage.mkdir(parents=True, exist_ok=True)
        source = 'begin\n    %_#${}\\\n    \\end{Verbatim}\n    中文注释\nend'
        code = {'id':'native-code','type':'code','text':source,
                'code_presentation':{'font_policy':'monospace','outer_indent_ccwd':1},
                'styles':[{'start':0,'end':5,'kind':'underline'},
                          {'start':source.index('中文'),'end':source.index('中文')+4,'kind':'kai'}],
                'annotations':[{'start_line':2,'end_line':4,'label':'三行注解'}]}
        left = {'id':'left','type':'code','text':'if p then\n    x := 1;\nend'}
        right = {'id':'right','type':'code','text':'while p do\n    x := 2;\nend'}
        c1 = {'id':'cap1','type':'caption','text':'(a) 左代码'}
        c2 = {'id':'cap2','type':'caption','text':'(b) 右代码'}
        blocks = [code,left,right,c1,c2]
        assets = collect_special_assets(blocks,stage)
        pair = {'columns':[{'code_id':'left','caption_id':'cap1'}, {'code_id':'right','caption_id':'cap2'}]}
        document = r'''\documentclass[fontset=none]{ctexart}
\usepackage[a4paper,margin=20mm]{geometry}
\usepackage{amsmath}
\setmainfont{DejaVu Serif}
\setmonofont{DejaVu Sans Mono}
\setCJKmainfont{FandolSong-Regular.otf}
\setCJKmonofont{FandolSong-Regular.otf}
\setCJKfamilyfont{kai}{FandolKai-Regular.otf}
\providecommand{\kaishu}{\CJKfamily{kai}}
''' + SPECIAL_TEXT_PREAMBLE + '\n\\begin{document}\n' + render_special_block(code,assets) + '\n' + compose_code_group(pair,{b['id']:b for b in blocks},assets) + '\n' + render_special_block({'id':'grammar','type':'grammar','text':'begin | end','alternatives':[{'text':'begin','keyword':True},{'text':'end','keyword':True}]}) + '\n\\end{document}\n'
        (stage/'fixture.tex').write_text(document)
        for _ in range(2):
            result = subprocess.run(['xelatex','-interaction=nonstopmode','-halt-on-error','fixture.tex'],cwd=stage,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            self.assertEqual(result.returncode,0,result.stdout[-5000:])
        log = (stage/'fixture.log').read_text()
        self.assertNotIn('Overfull',log)
        self.assertNotIn('Missing character:',log)
        result = subprocess.run(['pdftotext','-layout','fixture.pdf','-'],cwd=stage,stdout=subprocess.PIPE,text=True,check=True)
        text = result.stdout
        for token in ('%_#${}\\',r'\end{Verbatim}','中文注释','三行注解','(a) 左代码','(b) 右代码'):
            self.assertIn(token,text)
        # Both code columns and both independent captions occupy shared rows.
        self.assertTrue(any('if p then' in line and 'while p do' in line for line in text.splitlines()))
        self.assertTrue(any('(a) 左代码' in line and '(b) 右代码' in line for line in text.splitlines()))
        self.assertIn('    %_#${}\\',text)
        import fitz
        page = fitz.open(stage/'fixture.pdf')[0]
        self.assertEqual(page.get_images(), [], 'code and grammar must remain native text')
        spans = [span for block in page.get_text('dict')['blocks'] for line in block.get('lines',[]) for span in line['spans']]
        begin = next(span for span in spans if span['text']=='begin')
        special = next(span for span in spans if span['text']=='%_#${}\\')
        self.assertEqual(begin['font'], special['font'], 'underline must not change code font')
        cell_width = (begin['bbox'][2]-begin['bbox'][0])/5
        self.assertAlmostEqual(special['bbox'][0]-begin['bbox'][0],4*cell_width,places=1)
        kai = next(span for span in spans if span['text']=='中文注释')
        self.assertEqual(kai['font'],'FandolKai-Regular')
        subprocess.run(['pdftoppm','-scale-to','1600','-png','-singlefile','fixture.pdf','fixture'],cwd=stage,check=True)


if __name__ == '__main__':
    unittest.main()
