"""Reading-label formatting must preserve the source marker's identity."""
import copy
import hashlib
import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from semantic_contract import validate_semantic_block
from presentation_render import local_heading


class LabelNormalizationTests(unittest.TestCase):
    def block(self, replacement):
        return dict(type='list', text='immutable source text',
                    items=[dict(label='（1）', text='first condition'),
                           dict(label='(2)', text='second condition')],
                    list_presentation=dict(schema_version=1, kind='ordered',
                        label_normalizations=[dict(item_index=0, original='（1）',
                            replacement=replacement, rule_id='paren-width', rule_version='1')]))

    def test_width_and_spacing_change_preserves_marker(self):
        for replacement in ['(1)', '( 1 )', '（1）', '1.']:
            block=self.block(replacement)
            # The final variant intentionally retains the mixed-width issue;
            # identity checks and same-group consistency are independent.
            errors=validate_semantic_block(block)
            self.assertFalse(any('marker identity' in e for e in errors), errors)
            if replacement=='(1)':
                self.assertEqual(errors, [])
            self.assertEqual(block['items'][0]['label'], '（1）')
            self.assertEqual(block['text'], 'immutable source text')

    def test_changed_number_or_label_content_is_rejected(self):
        for replacement in ['(999)', '(2)', '(11)', 'condition one']:
            self.assertTrue(any('marker identity' in e
                for e in validate_semantic_block(self.block(replacement))), replacement)

    def test_original_mismatch_still_rejected(self):
        block=self.block('(1)')
        block['list_presentation']['label_normalizations'][0]['original']='(1)'
        self.assertTrue(any('index/original' in e for e in validate_semantic_block(block)))


    def test_heading_prefix_normalization_keeps_source_and_offsets(self):
        original='（3）Chomsky 文法'
        block=dict(type='heading', text=original,
            styles=[dict(start=0,end=len(original),kind='bold',evidence='unreviewed')],
            heading_presentation=dict(schema_version=1, role='local_heading', font_size='body',
                review=dict(source_checked=True),
                label_normalization=dict(original='（3）',replacement='(3)',
                    rule_id='group-parens',rule_version='1')))
        before=copy.deepcopy(block)
        tex=local_heading(block,lambda b:b['text'])
        self.assertIn('(3)Chomsky 文法',tex)
        self.assertEqual(block,before)
        block['heading_presentation']['label_normalization']['replacement']='(9)'
        self.assertTrue(any('original marker prefix' in e for e in validate_semantic_block(block)))
        with self.assertRaises(ValueError):
            local_heading(block,lambda b:b['text'])
        block['heading_presentation']['label_normalization']['replacement']='3.'
        with self.assertRaises(ValueError):
            local_heading(block,lambda b:b['text'])



    def test_actual_renderer_projects_whole_label_styles_for_both_list_roles(self):
        from export_latex import render_block
        with tempfile.TemporaryDirectory() as folder:
            evidence=Path(folder)/'source'
            evidence.write_bytes(b'reviewed source image')
            image_hash=hashlib.sha256(evidence.read_bytes()).hexdigest()
            for kind in ['description','ordered']:
                block=self.block('1.')
                block.update(block_id='list',source_sha256='f'*64,source_image_sha256=image_hash)
                block['items'][0]['label_styles']=[dict(start=0,end=3,kind='bold',evidence='source_checked')]
                block['list_presentation'].update(kind=kind,source_sha256='f'*64,
                    source_image_sha256=image_hash,text_sha256=hashlib.sha256(block['text'].encode()).hexdigest(),
                    review=dict(source_checked=True,reviewer='test',evidence_path=str(evidence)))
                before=copy.deepcopy(block)
                self.assertEqual(validate_semantic_block(block), [])
                self.assertIn(r'\textbf{1.}',render_block(block))
                self.assertEqual(block,before)
                block['items'][0]['label_styles'][0].update(start=1,end=2)
                self.assertTrue(any('partial label_styles' in e for e in validate_semantic_block(block)))
                with self.assertRaisesRegex(ValueError,'partial label_styles'):
                    render_block(block)

    def test_actual_renderer_retains_partial_styles_on_equal_length_width_change(self):
        from export_latex import render_block
        block=self.block('(1)')
        block['list_presentation']['review']=dict(source_checked=True)
        block['items'][0]['label_styles']=[dict(start=1,end=2,kind='bold',evidence='unreviewed')]
        self.assertIn(r'(\textbf{1})',render_block(block))



if __name__=='__main__':
    unittest.main()
