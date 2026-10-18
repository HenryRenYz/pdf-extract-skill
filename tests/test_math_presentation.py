import hashlib, pathlib, tempfile, unittest, sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from math_presentation import render_math_presentation

class MathComposition(unittest.TestCase):
 def test_provenance_and_native_output(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'source';p.write_bytes(b'source image'); ih=hashlib.sha256(p.read_bytes()).hexdigest()
   blocks={i:dict(text=t,source_sha256='pdfhash',source_image_sha256=ih,chapter_id='c') for i,t in [('a',r'\[x=1+\]'),('b',r'\[2=3\]')]}
   members={i:dict(text_sha256=hashlib.sha256(b['text'].encode()).hexdigest(),source_sha256='pdfhash',source_image_sha256=ih,evidence_path=str(p)) for i,b in blocks.items()}
   dec=dict(schema_version=1,kind='aligned_rows',block_ids=['a','b'],members=members,review=dict(source_checked=True,reviewer='test'),rows=[dict(block_id='a',align_at=0),dict(block_id='b',align_at=0,indent_em=1)])
   tex=render_math_presentation(dec,blocks,lambda s:s)
   self.assertIn(r'\begin{aligned}',tex);self.assertNotIn(r'\tag',tex)
   dec['rows'][1]['indent_em']=0.5
   fractional=render_math_presentation(dec,blocks,lambda s:s)
   self.assertIn(r'&\hspace{0.5em}2=3',fractional)
   dec['rows'][1]['indent_em']=1.25
   self.assertIn(r'&\hspace{1.25em}2=3',render_math_presentation(dec,blocks,lambda s:s))
   blocks['b']['text']=r'\[2=4\]'
   with self.assertRaisesRegex(ValueError,'text changed'):render_math_presentation(dec,blocks,lambda s:s)
 def test_label_identity(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'image';p.write_bytes(b'image'); ih=hashlib.sha256(p.read_bytes()).hexdigest();b=dict(text=r'解 \(x=2\)',source_sha256='p',source_image_sha256=ih,chapter_id='c')
   dec=dict(schema_version=1,kind='labeled_display',block_ids=['b'],members={'b':dict(text_sha256=hashlib.sha256(b['text'].encode()).hexdigest(),source_sha256='p',source_image_sha256=ih,evidence_path=str(p))},review=dict(source_checked=True,reviewer='test'),label='解',label_bold=True)
   tex=render_math_presentation(dec,{'b':b},lambda s:s);self.assertIn(r'\textbf{解}',tex);self.assertIn('&&x=2&&',tex)
   dec['label']='证'
   with self.assertRaisesRegex(ValueError,'prefix'):render_math_presentation(dec,{'b':b},lambda s:s)
 def test_projected_math_marker_and_terminal_punctuation(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'image';p.write_bytes(b'image'); ih=hashlib.sha256(p.read_bytes()).hexdigest();b=dict(text=r'\[ (2)\quad x=2 \]',source_sha256='p',source_image_sha256=ih,chapter_id='c')
   dec=dict(schema_version=1,kind='labeled_display',block_ids=['b'],members={'b':dict(text_sha256=hashlib.sha256(b['text'].encode()).hexdigest(),source_sha256='p',source_image_sha256=ih,evidence_path=str(p))},review=dict(source_checked=True,reviewer='test'),label='(2)',math_label_prefix=r'(2)\quad ')
   tex=render_math_presentation(dec,{'b':b},lambda s:s);self.assertIn('&&x=2&&',tex);self.assertEqual(tex.count('(2)'),1)
   dec['math_label_prefix']=r'(2)\quad y'
   with self.assertRaisesRegex(ValueError,'projection'):render_math_presentation(dec,{'b':b},lambda s:s)
 def test_terminal_punctuation_is_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'image';p.write_bytes(b'image'); ih=hashlib.sha256(p.read_bytes()).hexdigest();b=dict(text=r'(1) \(x=2\);',source_sha256='p',source_image_sha256=ih,chapter_id='c')
   dec=dict(schema_version=1,kind='labeled_display',block_ids=['b'],members={'b':dict(text_sha256=hashlib.sha256(b['text'].encode()).hexdigest(),source_sha256='p',source_image_sha256=ih,evidence_path=str(p))},review=dict(source_checked=True,reviewer='test'),label='(1)')
   tex=render_math_presentation(dec,{'b':b},lambda s:s);self.assertIn(r'\text{;}',tex)
if __name__=='__main__':unittest.main()
