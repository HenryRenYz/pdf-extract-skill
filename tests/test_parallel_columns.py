"""Real-source gaps: uncaptioned code and ordered list/code columns."""
import tempfile
from pathlib import Path
import unittest
from scripts.special_text import collect_special_assets, compose_code_group, SpecialTextError
from scripts.semantic_contract import validate_semantic_block
from scripts.assemble_book import display_group_members


class ParallelColumnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.blocks = {
            'left': {'id':'left','type':'code','text':'100 IF(P) GO TO 150\n  A段'},
            'right': {'id':'right','type':'code','text':'while NOT P do\n  A段'},
            'steps': {'id':'steps','type':'list','text':'取值；加1', 'items':[{'label':'1.','text':'取值'},{'label':'2.','text':'加1'}]},
            'intro': {'id':'intro','type':'paragraph','text':'两者都对，但效率不同。'},
            'grammar': {'id':'grammar','type':'grammar','text':'a | b', 'latex':r'\texttt{a}\mid\texttt{b}'},
            'caption': {'id':'caption','type':'caption','text':'左侧说明'},
        }
        self.assets = collect_special_assets(list(self.blocks.values()),self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_uncaptioned_code_preserves_two_external_sources(self):
        group = {'columns':[{'code_id':'left'},{'code_id':'right'}], 'reading_order':['left','right']}
        result = compose_code_group(group,self.blocks,self.assets)
        self.assertEqual(result.count('VerbatimInput'),2)
        self.assertNotIn('smallskip',result)
        for ident in ('left','right'):
            self.assertEqual((self.root/self.assets[ident]['source_path']).read_text(),self.blocks[ident]['text'])

    def test_mixed_columns_use_full_renderer_in_source_order(self):
        calls=[]
        def renderer(block):
            calls.append(block['id'])
            return {'steps':r'\begin{enumerate}\item 取值\item 加1\end{enumerate}',
                    'intro':r'\par 两者都对，但效率不同。\par'}[block['id']]
        group={'columns':[{'block_ids':['steps','left'],'caption_id':'caption'},
                          {'block_ids':['intro','grammar','right']}],
               'reading_order':['steps','left','caption','intro','grammar','right'],
               'caption_alignment':'bottom'}
        result=compose_code_group(group,self.blocks,self.assets,block_renderer=renderer)
        self.assertEqual(calls,['steps','intro'])
        self.assertIn(r'\begin{enumerate}\item',result)
        self.assertIn(r'\newsavebox',result)
        self.assertEqual(result.count('VerbatimInput'),2)
        self.assertEqual(result.count('左侧说明'),1)
        self.assertLess(result.index('enumerate'),result.index(self.assets['left']['render_path']))
        with self.assertRaisesRegex(SpecialTextError,'full block_renderer'):
            compose_code_group(group,self.blocks,self.assets)

    def test_invalid_ownership_forms_types_and_order_rejected(self):
        invalid=[
            [{'code_id':'left','block_ids':['steps']},{'code_id':'right'}],
            [{'block_ids':[]},{'code_id':'right'}],
            [{'block_ids':['left','left']},{'code_id':'right'}],
            [{'code_id':'left'},{'block_ids':['left']}],
            [{'code_id':'steps'},{'code_id':'right'}],
            [{'block_ids':['caption']},{'code_id':'right'}],
            [{'code_id':'left','caption_id':'caption'},{'code_id':'right','caption_id':'caption'}],
            [{'code_id':'left','caption_id':None},{'code_id':'right'}],
            [{'code_id':'left'}],
        ]
        for columns in invalid:
            with self.subTest(columns=columns),self.assertRaises(SpecialTextError):
                compose_code_group({'columns':columns},self.blocks,self.assets)
        with self.assertRaisesRegex(SpecialTextError,'reading_order'):
            compose_code_group({'columns':[{'code_id':'left'},{'code_id':'right'}],
                                'reading_order':['left','left']},self.blocks,self.assets)

    def test_assembly_resolves_ordered_mixed_columns_without_captions(self):
        group={'columns':[{'block_ids':['steps','left']},{'block_ids':['intro','grammar','right']}],
               'reading_order':['steps','left','intro','grammar','right']}
        members,errors=display_group_members(group,self.blocks,'owner')
        self.assertEqual(members,group['reading_order'])
        self.assertEqual(errors,[])
        invalid=[dict(group,reading_order=['left','right']),
                 {'columns':[{'block_ids':['left','left']},{'code_id':'right'}]},
                 {'columns':[{'code_id':'steps'},{'code_id':'right'}]},
                 {'columns':[{'block_ids':['missing']},{'code_id':'right'}]},
                 {'columns':[{'block_ids':['steps'],'code_id':'left'},{'code_id':'right'}]},
                 {'columns':[{'block_ids':['caption']},{'code_id':'right'}]}]
        for candidate in invalid:
            with self.subTest(group=candidate):
                self.assertTrue(display_group_members(candidate,self.blocks,'owner')[1])

    def test_semantic_contract_preserves_mixed_members_and_captions(self):
        def errors(cols):
            return validate_semantic_block({'id':'g','type':'display_group','text':'',
                                            'groups':[{'columns':cols}]})
        self.assertFalse(errors([{'block_ids':['steps','left'],'caption_id':'caption'}, {'code_id':'right'}]))
        self.assertTrue(errors([{'code_id':'left','block_ids':['steps']},{'code_id':'right'}]))
        self.assertTrue(errors([{'block_ids':['left','left']},{'code_id':'right'}]))
        self.assertTrue(errors([{'code_id':'left','caption_id':'left'},{'code_id':'right'}]))
        self.assertTrue(errors([{'code_id':'left'}]))


if __name__=='__main__':unittest.main()
