"""Explicit, ZIP-only inclusion of font files; never rewrite accepted TeX."""
import hashlib
import json
from pathlib import Path


def prepare_body_fonts(args):
    fonts = getattr(args, 'body_font', [])
    licenses = getattr(args, 'body_font_license', [])
    include = getattr(args, 'include_body_fonts', False)
    if not include:
        if fonts or licenses:
            raise ValueError('Body-font files require explicit --include-body-fonts')
        return {}, None
    if not fonts or not licenses:
        raise ValueError('--include-body-fonts requires --body-font and --body-font-license files')
    files, records = {}, {'fonts': [], 'licenses': []}
    for kind, paths in [('fonts', fonts), ('licenses', licenses)]:
        for item in paths:
            path = Path(item).expanduser().resolve()
            if not path.is_file():
                raise ValueError('Missing body-font dependency: ' + str(path))
            if kind == 'fonts':
                with path.open('rb') as stream:
                    signature = stream.read(4)
                if (path.suffix.lower() not in {'.ttf', '.otf', '.ttc', '.otc'}
                        or signature not in {b'\x00\x01\x00\x00', b'OTTO', b'ttcf', b'true'}):
                    raise ValueError('Expected an OpenType/TrueType font file: ' + str(path))
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            relative = ('body-fonts/' + ('licenses/' if kind == 'licenses' else '')
                        + checksum[:12] + '-' + path.name)
            if relative in files:
                continue
            files[relative] = path
            records[kind].append({'path': relative, 'sha256': checksum})
    manifest = dict(schema_version=1, **records, tex_modified=False,
        loading_mode='Install the supplied fonts; TeX retains its configured family names.',
        coverage='Files selected by the caller; font-role/style coverage is not automatically certified.')
    files['body-fonts/manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    files['body-fonts/README.txt'] = (
        'Bundled body fonts / 随包正文字体\n'
        '================================\n'
        '字体文件和附带许可证见 manifest.json；文件名的哈希前缀用于避免重名。\n'
        '编译前请将所需字体安装到当前用户的字体目录，或上传后按平台文档配置。\n'
        '当前源码仍使用原字体族名称；仅附带文件不会让 XeLaTeX 自动加载本目录。\n'
        '此包没有修改已验收的 TeX、字体选择或系统字体配置。\n'
        '打包者应核对实际使用的字体族及字重/斜体/字体集合是否齐全。\n'
        '若改成 fontspec 包内相对文件加载，请使用副本并重新检查编译和排版。\n'
    ).encode()
    return files, manifest
