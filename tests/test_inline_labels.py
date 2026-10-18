"""Source label recognition must preserve short proof and integer example labels."""
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inline_labels import annotate_inline_labels, validate_inline_label


class InlineLabelTests(unittest.TestCase):
    source = 'a' * 64

    def rule(self):
        return {'source_sha256': self.source, 'id': 'label-gap', 'version': '1',
                'gap_ccwd': 1, 'review': {'source_checked': True}}

    def block(self, kind, label, body='原文'):
        return {'id': 'b1', 'type': kind, 'source_sha256': self.source,
                'text': label + ' ' + body,
                'styles': [{'start': 0, 'end': len(label), 'kind': 'bold'}]}

    def test_short_proof_and_integer_example_keep_original_text_and_ranges(self):
        for kind, label in [('proof', '证'), ('proof', '证明'),
                            ('example', '例4'), ('theorem', '定理5.1'),
                            ('theorem', '引理2．3')]:
            with self.subTest(label=label):
                original = self.block(kind, label)
                output, decisions = annotate_inline_labels([original], self.rule())
                self.assertEqual(output[0]['text'], original['text'])
                self.assertNotIn('inline_label', original)
                self.assertEqual(len(decisions), 1)
                binding = validate_inline_label(output[0])
                self.assertEqual(binding['label'], label)
                self.assertEqual(binding['end'], len(label))
                self.assertEqual(binding['separator_end'], len(label) + 1)
                self.assertEqual(binding['text_sha256'], hashlib.sha256(original['text'].encode()).hexdigest())

    def test_ordinary_prose_and_unemphasized_label_are_not_promoted(self):
        prose = self.block('paragraph', '证', '据充足')
        mention = self.block('paragraph', '例4', '给出结果')
        unstyled = {**self.block('proof', '证'), 'styles': []}
        output, decisions = annotate_inline_labels([prose, mention, unstyled], self.rule())
        self.assertEqual(decisions, [])
        self.assertTrue(all('inline_label' not in block for block in output))


if __name__ == '__main__':
    unittest.main()
