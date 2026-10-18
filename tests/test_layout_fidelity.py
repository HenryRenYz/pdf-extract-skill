"""Native rendering regressions for geometry and literal source styles."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from semantic_contract import validate_semantic_block, span_color
from special_text import collect_special_assets, render_special_block, SPECIAL_TEXT_PREAMBLE, SpecialTextError
from export_latex import InlineRenderer, render_table
from latex_template import NATIVE_SUPPORT


class LayoutFidelityTests(unittest.TestCase):
    def test_color_evidence_values_and_conflicts(self):
        b = dict(type='paragraph', text='abcd', styles=[dict(start=0,end=2,kind='color',value='#123AbC',evidence='unreviewed')])
        self.assertEqual(validate_semantic_block(b), [])
        self.assertEqual(span_color(b['styles'],0,2),'123ABC')
        self.assertIn(r'\textcolor[HTML]{123ABC}{ab}', InlineRenderer(b['text'],b['styles']).render())
        b['styles'].append(dict(start=1,end=3,kind='color',value='#abcdef',evidence='unreviewed'))
        self.assertTrue(any('disagree' in e for e in validate_semantic_block(b)))
        b['styles'][0]['value'] = r'\input{bad}'
        self.assertTrue(any('#RRGGBB' in e for e in validate_semantic_block(b)))

    def test_grammar_mapping_rejects_changed_source_and_overlapping_scripts(self):
        b = dict(id='g',type='grammar',text='A + B*',grammar_lines=[dict(text='A + B*',scripts=[dict(start=5,end=6,position='superscript')])])
        self.assertEqual(validate_semantic_block(b), [])
        self.assertIn(r'^{\texttt{*}}', render_special_block(b))
        b['grammar_lines'][0]['text'] = 'A + C*'
        with self.assertRaisesRegex(SpecialTextError,'every original'):
            render_special_block(b)
        self.assertTrue(validate_semantic_block(b))

    def test_flow_candidates_do_not_claim_review(self):
        b = dict(text='Introduction',type='paragraph',flow_presentation=dict(schema_version=1,keep_with_next=True))
        self.assertEqual(validate_semantic_block(b), [])
        b['flow_presentation']['keep_with_next'] = 'yes'
        self.assertTrue(validate_semantic_block(b))

    def test_source_inventory_detects_omitted_fragment_and_allows_no_graphics(self):
        from PIL import Image
        from audit_figures import audit
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.pdf';source.write_bytes(b'identity fixture')
            image=root/'page.png';Image.new('RGB',(80,100),'white').save(image)
            digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            inv=dict(source_sha256=digest(source),scope_pages=[1],method='source_first_visual_inventory',
                     reviewed_pages=[dict(physical_page=1,reviewer='independent-fixture',viewed_evidence=True,
                       source_image=str(image),source_image_sha256=digest(image))],items=[])
            path=root/'inventory.json';path.write_text(json.dumps(inv));blocks=root/'blocks.jsonl';blocks.write_text('')
            self.assertTrue(audit(source,path,blocks,None)['coverage_pass'])
            inv['items']=[dict(item_id='fragment',physical_page=1,bbox_px=[10,90,60,100],description='clipped source graphic',kind='figure')]
            path.write_text(json.dumps(inv))
            result=audit(source,path,blocks,None)
            self.assertFalse(result['coverage_pass'])
            self.assertTrue(any('no semantic block' in error for error in result['errors']))

    @unittest.skipUnless(shutil.which('xelatex'), 'XeLaTeX required')
    def test_actual_pdf_colors_whitespace_scripts_baselines_and_caption(self):
        import fitz
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        stage=Path(temporary.name)/'native-regression'
        stage.mkdir(parents=True,exist_ok=True)
        code=dict(id='code',type='code',text='blue red\n  cyan',styles=[
            dict(start=0,end=4,kind='color',value='#31429B'),
            dict(start=5,end=8,kind='color',value='#DF1F26'),
            dict(start=11,end=15,kind='color',value='#0F5B5B')])
        assets=collect_special_assets([code],stage)
        self.assertEqual((stage/assets['code']['source_path']).read_text(),code['text'])
        grammar=dict(id='grammar',type='grammar',text='base*\n   indented\nprefix ::= token\n   | alternative',grammar_lines=[
            dict(text='base*',scripts=[dict(start=4,end=5,position='superscript')]),dict(text='   indented'),dict(text='prefix ::= token'),dict(text='   | alternative',align_to=dict(line=2,offset=7))])
        cells=[[dict(text=t) for t in row] for row in [['A','',''],['','X',''],['B','','Z'],['','Y',''],['C','','']]]
        table=dict(type='table',text='fixture',cells=cells,styles=[],_table_presentation=dict(width_policy='natural_width',
            caption_position='bottom',row_layout=dict(baseline_offsets_em=[0,.75,1.5,2.25,3])))
        table['source_image_sha256']='a'*64
        table['table_layout']=dict(schema_version=1,evidence='source_checked',row_count=5,column_count=3,
            text_sha256=hashlib.sha256(b'fixture').hexdigest(),source_image_sha256='a'*64,horizontal=[],vertical=[])
        compact=render_table(table,caption=r'\caption*{Bottom caption}')
        table['_table_presentation'].pop('row_layout')
        normal=render_table(table)
        doc=r'''\documentclass{article}
\usepackage[margin=25mm]{geometry}
\usepackage{fontspec}
\setmainfont{DejaVu Serif}\setmonofont{DejaVu Sans Mono}
''' + NATIVE_SUPPORT + SPECIAL_TEXT_PREAMBLE + r'''
\AddToHook{env/tabular/begin}{\renewcommand{\arraystretch}{1.5}\setlength{\extrarowheight}{3pt}}
\begin{document}
''' + render_special_block(code,assets)+'\n'+render_special_block(grammar)+'\n'+compact+'\n'+normal+r'\end{document}'
        (stage/'fixture.tex').write_text(doc)
        proc=subprocess.run(['xelatex','-halt-on-error','-interaction=nonstopmode','fixture.tex'],cwd=stage,capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stdout[-2500:])
        pdf=fitz.open(stage/'fixture.pdf');page=pdf[0]
        spans=[s for b in page.get_text('dict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans']]
        for token,color in [('blue',0x31429B),('red',0xDF1F26),('cyan',0x0F5B5B)]:
            self.assertTrue(any(token in s['text'] and s['color']==color for s in spans),(token,spans))
        chars=[c for b in page.get_text('rawdict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans'] for c in s['chars']]
        star=next(c for c in chars if c['c']=='*');base=next(s for s in spans if 'base' in s['text'])
        self.assertLess(star['origin'][1],base['origin'][1]-1)
        self.assertGreater(page.search_for('indented')[0].x0,page.search_for('base')[0].x0+10)
        self.assertAlmostEqual(page.search_for('| alternative')[0].x0,page.search_for('::= token')[0].x0,delta=.2)
        aa=[fitz.Rect(s['bbox']) for s in spans if s['text'].strip()=='A'];cc=[fitz.Rect(s['bbox']) for s in spans if s['text'].strip()=='C']
        self.assertEqual(len(aa),2);self.assertEqual(len(cc),2)
        compact_height=cc[0].y0-aa[0].y0;normal_height=cc[1].y0-aa[1].y0
        self.assertLess(compact_height,.65*normal_height)
        self.assertGreater(page.search_for('Bottom caption')[0].y0,cc[0].y1)

if __name__=='__main__':unittest.main()
