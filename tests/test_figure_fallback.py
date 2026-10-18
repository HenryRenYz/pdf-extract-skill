"""A fallback must describe the selected source and a real fidelity limit."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from figure_fallback import validate_fallback


class FallbackTests(unittest.TestCase):
    def test_assessment_binds_pixels_and_failed_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.png'
            source.write_bytes(b'source photo pixels')
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            decision = dict(status='source_checked', category='photograph', reviewer='reviewer',
                            reason='Photographic subject with continuous textures',
                            evidence=dict(path=str(source), sha256=sha), asset_sha256=sha)
            self.assertEqual(validate_fallback({'fallback_assessment': decision}, source), decision)
            invalid = copy.deepcopy(decision)
            invalid['category'] = 'labels_dense'
            with self.assertRaises(ValueError):
                validate_fallback({'fallback_assessment': invalid}, source)
            failed = copy.deepcopy(decision)
            failed.update(category='reconstruction_failed', missing_details=['unreadable edge endpoint'],
                          alternatives=[dict(method='native vectors', limitation='unavailable')])
            with self.assertRaises(ValueError):
                validate_fallback({'fallback_assessment': failed}, source)
            failed['attempts'] = [dict(path=str(source), sha256=sha, defect='endpoint cannot be resolved')]
            validate_fallback({'fallback_assessment': failed}, source)
            source.write_bytes(b'different pixels')
            with self.assertRaises(ValueError):
                validate_fallback({'fallback_assessment': decision}, source)


if __name__ == '__main__':
    unittest.main()
