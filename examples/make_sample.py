#!/usr/bin/env python3
"""Render a self-authored one-page PNG; no model call or external fonts."""
import argparse
from pathlib import Path

import pymupdf


def make_page():
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=780)
    page.insert_text((50, 70), 'Synthetic extraction sample', fontname='hebo', fontsize=20)
    page.insert_text((50, 115), 'Keep', fontname='hebo', fontsize=14)
    x = 50 + pymupdf.get_text_length('Keep ', fontname='hebo', fontsize=14)
    page.insert_text((x, 115), 'the original text.', fontname='helv', fontsize=14)
    page.insert_text((50, 160), 's = a + b', fontname='tiit', fontsize=18)
    for y in (200, 230, 260, 290):
        page.draw_line((50, y), (350, y), color=(0, 0, 0), width=.7)
    for x in (50, 200, 350):
        page.draw_line((x, 200), (x, 290), color=(0, 0, 0), width=.7)
    for row, values in enumerate([('Name', 'Value'), ('x', '2'), ('y', '3')]):
        for col, value in enumerate(values):
            page.insert_text((65+150*col, 220+30*row), value,
                             fontname='hebo' if row == 0 else 'helv', fontsize=13)
    page.insert_text((50, 335), "print('hello')", fontname='cour', fontsize=14)
    return doc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='numbered PNG directory')
    args = parser.parse_args()
    target = args.output/'0001.png'
    if target.exists():
        parser.error('Sample already exists; choose a new output directory')
    args.output.mkdir(parents=True, exist_ok=True)
    with make_page() as doc:
        doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(target)
    print(target)


if __name__ == '__main__':
    main()
