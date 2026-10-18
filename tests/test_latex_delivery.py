"""Compiler hints must not conceal unresolved system-font dependencies."""
import unittest
from types import SimpleNamespace
from scripts.body_font_bundle import prepare_body_fonts
from scripts.latex_delivery import annotate_main, font_dependencies, build_instructions
from scripts.format_preflight import RENDER_DEFAULTS


class LatexDeliveryTests(unittest.TestCase):
    def test_custom_template_hint_is_replaced_without_touching_tex(self):
        source = '% !TEX program = pdflatex\n% !TeX encoding = latin1\n\\documentclass{book}\n'
        result = annotate_main(source)
        self.assertEqual(result.count('program ='), 1)
        self.assertNotIn('pdflatex', result)
        self.assertTrue(result.endswith('\\documentclass{book}\n'))

    def test_font_requirements_distinguish_unused_kai_and_code(self):
        deps = font_dependencies(RENDER_DEFAULTS, code_family='Example Code', code_used=False)
        self.assertFalse(deps['fonts_bundled'])
        optional = [font['name'] for font in deps['configured_fonts'] if not font['required']]
        self.assertIn('KaiTi', optional)
        self.assertIn('Example Code', optional)
        used = font_dependencies(RENDER_DEFAULTS, kai_used=True, code_family='Example Code', code_used=True)
        self.assertTrue(all(font['required'] for font in used['configured_fonts']))
        self.assertIn('字体文件未随本包提供', build_instructions(used))

    def test_body_font_selection_needs_an_explicit_opt_in_and_license_files(self):
        self.assertEqual(prepare_body_fonts(SimpleNamespace()), ({}, None))
        with self.assertRaisesRegex(ValueError, 'explicit --include-body-fonts'):
            prepare_body_fonts(SimpleNamespace(body_font=['font.ttf']))
        with self.assertRaisesRegex(ValueError, '--body-font-license'):
            prepare_body_fonts(SimpleNamespace(include_body_fonts=True, body_font=['font.ttf']))


if __name__ == '__main__':
    unittest.main()
