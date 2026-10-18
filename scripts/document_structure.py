"""Conservative chapter tokens shared by assembly, numbering and contents."""
import re

DIGITS = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))


def chapter_value(token):
    if token.isdigit():
        return int(token)
    if not token or any(c not in DIGITS and c not in '十百千' for c in token):
        return None
    if all(c in DIGITS for c in token):
        return int(''.join(str(DIGITS[c]) for c in token))
    total = digit = 0
    for char in token:
        if char in DIGITS:
            digit = DIGITS[char]
        else:
            total += (digit or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            digit = 0
    return total + digit


def chapter_prefix(text):
    match = re.match(r'第\s*([0-9零〇一二两三四五六七八九十百千]+)\s*章\s*', text)
    if match:
        return chapter_value(match[1]), match.end()
    match = re.match(r'chapter\s+(\d+)\b[.:：]?\s*', text, re.I)
    return (int(match[1]), match.end()) if match else (None, 0)
