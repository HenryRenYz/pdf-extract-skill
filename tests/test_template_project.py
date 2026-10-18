"""User projects must copy their resources and use their actual engine."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.latex_template import load_template, template_hashes
from scripts.latex_delivery import annotate_main, latexmkrc
from scripts.format_preflight import load_format_profile, render_configuration

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))


class TemplateProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root/'template'
        (self.project/'style').mkdir(parents=True)
        (self.project/'main.tex').write_text('\\documentclass{book}\n\\input{style/settings.tex}\n\\begin{document}\n@@BODY@@\n\\end{document}\n')
        (self.project/'style/settings.tex').write_text('\\setlength{\\parindent}{1em}\n')
        self.manifest = dict(schema_version=1, entry='main.tex', resources=['main.tex','style/settings.tex'],
                             engine='pdflatex', family='book')
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        (self.project/'template.json').write_text(json.dumps(self.manifest))

    def test_inventory_includes_entry_manifest_and_nested_resources(self):
        project = load_template(self.project)
        hashes = template_hashes(project)
        self.assertEqual(set(hashes), {'main.tex','style/settings.tex','template.json'})
        (self.project/'style/settings.tex').write_text('changed')
        self.assertNotEqual(hashes, template_hashes(project))

    def test_skill_distribution_keeps_declared_resources_of_any_suffix(self):
        from scripts.package_skill import runtime_files
        target=self.root/'assets/custom'
        shutil.copytree(self.project,target)
        for name in ['references.bib','cover.pdf','LICENSE']:
            (target/name).write_text('test resource')
        manifest=json.loads((target/'template.json').read_text())
        manifest['resources']+=['references.bib','cover.pdf','LICENSE']
        (target/'template.json').write_text(json.dumps(manifest))
        (self.root/'SKILL.md').write_text('skill')
        (self.root/'pyproject.toml').write_text('')
        resources={str(p.relative_to(self.root)) for p in runtime_files(self.root)}
        self.assertTrue({'assets/custom/references.bib','assets/custom/cover.pdf','assets/custom/LICENSE'}<=resources)

    def test_missing_escape_and_output_collision_are_rejected(self):
        for value in ['../outside.sty','missing.sty','chapters/001.tex','BUILD.txt']:
            path = self.project/value
            path.parent.mkdir(parents=True, exist_ok=True)
            if value != 'missing.sty':
                path.write_text('resource')
            self.manifest['resources'] = ['main.tex', value]
            self.save()
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_template(self.project)

    def test_engine_hints_and_profile_follow_manifest(self):
        profile = dict(schema_version=1,status='draft',version='v1',source_sha256='a'*64,
                       template_dir=str(self.project),representative_pages=[12],tested_families=['paragraph'])
        file = self.root/'profile.json';file.write_text(json.dumps(profile))
        resolved = load_format_profile(file,'a'*64)
        self.assertEqual(render_configuration(resolved)['engine'],'pdflatex')
        self.assertIn('program = pdflatex',annotate_main('% !TeX program = xelatex\n', 'pdflatex'))
        self.assertIn('$pdf_mode = 1',latexmkrc('pdflatex'))
        profile['render_configuration'] = {'engine':'xelatex'}
        file.write_text(json.dumps(profile))
        with self.assertRaisesRegex(ValueError,'engine differs'):
            load_format_profile(file,'a'*64)

    @unittest.skipUnless(shutil.which('pdflatex'), 'pdfLaTeX required')
    def test_public_export_copies_resources_and_builds_with_pdftex(self):
        blocks = [dict(id='h',block_id='h',type='heading',text='Chapter 3 Test',heading_depth=1,
                       chapter_id='chapter-3',page=12,pages=[12],source_sha256='a'*64,styles=[]),
                  dict(id='p',block_id='p',type='paragraph',text='Native portable body.',
                       chapter_id='chapter-3',page=12,pages=[12],source_sha256='a'*64,styles=[])]
        source = self.root/'blocks.jsonl'
        source.write_text('\n'.join(json.dumps(b) for b in blocks))
        profile = self.root/'profile.json'
        profile.write_text(json.dumps(dict(schema_version=1,status='draft',version='v1',source_sha256='a'*64,
                       template_dir=str(self.project),representative_pages=[12],tested_families=['heading','paragraph'])))
        cmd = [sys.executable,str(ROOT/'scripts/export_latex.py'),'--blocks',str(source),
               '--output',str(self.root/'dist'),'--expected-pages','12','--format-profile',str(profile),
               '--preflight-mode','--compile']
        result = subprocess.run(cmd,capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'dist/style/settings.tex').read_bytes(),(self.project/'style/settings.tex').read_bytes())
        self.assertTrue((self.root/'dist/main.pdf').is_file())
        self.assertIn('program = pdflatex',(self.root/'dist/main.tex').read_text())
        report = json.loads((self.root/'dist-export.json').read_text())
        self.assertEqual(report['compile']['missing_glyph_count'],0)
        self.assertTrue(any(d['path']=='style/settings.tex' and d['scope']=='package'
                            for d in report['compile']['dependencies']))

    def test_article_heading_mapping_uses_section_counters(self):
        from scripts.latex_numbering import build_numbering_plan
        block = dict(block_id='intro',type='heading',text='3 Introduction',heading_depth=1,
                     chapter_id='article',styles=[])
        plan = build_numbering_plan([block], family='article')
        heading = plan['headings']['intro']
        self.assertEqual(heading['command'],'section')
        self.assertEqual(heading['counter_precommands'],[r'\setcounter{section}{2}'])

    def test_excerpt_chapter_context_needs_no_invented_title(self):
        from scripts.latex_numbering import build_numbering_plan
        block = dict(block_id='formula',type='formula',text=r'\[x=1\tag{5.3}\]',
                     chapter_id='chapter-5',styles=[])
        plan = build_numbering_plan([block])
        self.assertEqual(plan['headings'],{})
        self.assertEqual(plan['chapter_contexts']['formula'][0],r'\setcounter{chapter}{5}')
        self.assertEqual(plan['counts']['numbered_equations'],1)

    def test_native_literals_and_monospace_need_no_private_wrapper(self):
        from scripts.export_latex import InlineRenderer
        self.assertEqual(InlineRenderer('<Integer>').render(),r'\textless{}Integer\textgreater{}')
        text = InlineRenderer('glVertex', [{'start':0,'end':8,'kind':'monospace'}]).render()
        self.assertEqual(text,r'\texttt{glVertex}')

    @unittest.skipUnless(shutil.which('pdflatex'), 'pdfLaTeX required')
    def test_figure_row_uses_declared_physical_width_once(self):
        import fitz
        from PIL import Image
        from scripts.export_latex import compose_source_layout
        Image.new('RGB',(20,10),'#336699').save(self.root/'image.png')
        rendered = {name:r'\begin{figure}[H]\centering\includegraphics[width=0.4\linewidth,keepaspectratio]{image.png}\end{figure}'
                    for name in ('a','b')}
        group = dict(kind='figure_row',figures=[dict(block_id='a',width_mm=40),dict(block_id='b',width_mm=60)],gap_widths_mm=[5])
        body = compose_source_layout(group,rendered)
        (self.root/'main.tex').write_text(r'\documentclass{article}\usepackage{graphicx,float,caption}\begin{document}'
                                         +body+r'\end{document}')
        result = subprocess.run(['pdflatex','-interaction=nonstopmode','-halt-on-error','main.tex'],
                                 cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout)
        doc = fitz.open(self.root/'main.pdf')
        rectangles = doc[0].get_image_rects(doc[0].get_images()[0][0])
        self.assertEqual(len(rectangles),2)
        self.assertEqual([round(rect.width*25.4/72,1) for rect in rectangles],[40.0,60.0])


if __name__ == '__main__':
    unittest.main()
