"""Portable build hints and an honest account of external font dependencies.

These files describe how to rebuild the selected source. They do not install
fonts, redistribute system fonts, or certify compilation on another platform.
"""
import re


MAGIC_COMMENTS = '% !TeX program = xelatex\n% !TeX encoding = UTF-8\n'
LATEXMKRC = """# XeLaTeX is required by this source package. No absolute local paths.
$pdf_mode = 5;
$xelatex = 'xelatex -no-shell-escape %O %S';
$max_repeat = 5;
@default_files = ('main.tex');
"""


def compiler_name(engine):
    name = engine.rsplit('/', 1)[-1]
    if name not in ('xelatex', 'lualatex', 'pdflatex'):
        raise ValueError('Unsupported delivery engine: ' + engine)
    return name


def latexmkrc(engine='xelatex'):
    engine = compiler_name(engine)
    mode, variable = {'xelatex':(5,'xelatex'), 'lualatex':(4,'lualatex'), 'pdflatex':(1,'pdflatex')}[engine]
    return (f"# {engine} selected by this template project.\n$pdf_mode = {mode};\n"
            + f"${variable} = '{engine} -no-shell-escape %O %S';\n"
            + "$max_repeat = 5;\n@default_files = ('main.tex');\n")


def annotate_main(text, engine='xelatex'):
    """Keep one unambiguous editor hint, including for user-supplied templates."""
    text = re.sub(r'(?im)^[ \t]*%[ \t]*![ \t]*TeX[ \t]+(?:program|encoding)[ \t]*=[^\r\n]*(?:\r?\n|$)',
                  '', text)
    return f'% !TeX program = {compiler_name(engine)}\n% !TeX encoding = UTF-8\n' + text.lstrip('\n')


def font_dependencies(config, *, kai_used=False, code_family=None, code_used=False):
    fonts = [dict(role=role, name=config[key], required=True) for role, key in (
        ('Chinese body', 'cjk_font'), ('Chinese emphasis', 'cjk_emphasis_font'),
        ('Latin body', 'latin_font'), ('Latin emphasis/sans', 'latin_sans_font'))]
    fonts.append(dict(role='Chinese Kai', name=config['cjk_kai_font'], required=kai_used))
    if code_family:
        fonts.append(dict(role='Custom code', name=code_family, required=code_used))
    return dict(fonts_bundled=False, configured_fonts=fonts,
                scope='Configured render fonts; custom templates may declare additional fonts. '
                      'Drawing font bundles are independent of body-text fonts.')


def build_instructions(dependencies):
    engine = compiler_name(dependencies.get('engine', 'xelatex'))
    if dependencies.get('template_project'):
        return f"""LaTeX source package / LaTeX 源码编译说明

Entry point / 主文件: main.tex
Engine / 编译器: {engine}
Encoding / 编码: UTF-8

在本目录运行 latexmk（自动读取 .latexmkrc），或至少两次运行:
  {engine} -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex

main.tex 中的 % !TeX program 是编辑器提示，不会自行切换引擎。
在云平台中应同时选中 main.tex 和 {engine}。模板资源清单见 export-template.json。
模板使用的类、宏包和字体必须可用；编译依赖记录在导出报告的 compile.dependencies 中。
字体未默认随包附带；仅明确选择附带的字体及其许可证才会在 body-fonts/ 中提供。
所选模板可能直接声明字体，其覆盖范围需结合模板与实际 PDF 检查。
不得仅因编译成功就认定内容、字体、断行、公式和图表已通过验收。
"""
    required = '\n'.join('  - ' + f['role'] + ': ' + f['name']
                         for f in dependencies['configured_fonts'] if f['required'])
    optional = '\n'.join('  - ' + f['role'] + ': ' + f['name']
                         for f in dependencies['configured_fonts'] if not f['required'])
    font_status = ('本 ZIP 已附带选定的字体文件和许可证，见 body-fonts/README.txt。\n'
                   '请先安装所需字体，再按原字体族名称编译；文件覆盖范围由打包者核对。'
                   if dependencies.get('fonts_bundled') else
                   '这些字体文件未随本包提供，目标环境必须能找到它们。')
    return f"""LaTeX source package / LaTeX 源码编译说明
==========================================

Entry point / 主文件: main.tex
Engine / 编译器: XeLaTeX (xelatex)
Encoding / 编码: UTF-8

Build from this package's root directory / 在源码包根目录运行:
  latexmk -xelatex -no-shell-escape main.tex

The included .latexmkrc also selects XeLaTeX when running plain latexmk.
If latexmk is unavailable, run the following command at least twice; continue
until references and the table of contents have stabilized:
  xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex

Use a current TeX Live or MiKTeX installation with ctex, xeCJK, fontspec,
amsmath, amssymb, amsthm, bm, mathrsfs, cases, geometry, graphicx, array,
cellspace, arydshln, booktabs, multirow, tabularx, adjustbox, float, caption,
needspace, pdfpages, multicol, titletoc, hyperref, enumitem, and ulem.
Documents with code also need fvextra and TikZ. Python is needed only when
rebuilding drawing scripts, not when compiling the existing LaTeX and images.

Editor/cloud setup / 编辑器与在线平台
------------------------------------
The first lines of main.tex contain % !TeX program and encoding hints.
They are editor hints, not executable TeX compiler switches. If your platform
does not honor them, explicitly select XeLaTeX and main.tex in its project
settings (including Overleaf). Do not use pdfLaTeX for this source.

Fonts / 字体依赖
-----------------
正文使用以下配置字体。{font_status}
{required}

以下配置字体在当前内容中没有被启用，无需仅因其出现在配置中就安装:
{optional or '  (none)'}

字体名称写在 book-config.tex 中，默认模板在 book-style.sty 中声明字体。
字体族名并不保证在 Windows、macOS、Linux 或在线平台上都已安装。
如遇 font-not-found，应安装准确的字体，或在模板验收前统一改用目标 TeX
发行版提供的字体。更换字体可能改变分页、断行、字形覆盖和列表几何，需要复查。
若需完整可迁移包，应另外提供获准分发的字体和许可证，并将所有字体声明
改为包内相对路径；不能仅复制系统绝对路径，也不能把绘图字体包当作正文字体包。
自定义模板还可能硬编码额外字体；上述清单只描述导出配置，不能代替模板审查。

Output / 输出
-------------
The result is main.pdf. A delivered PDF may embed font subsets for viewing;
those embedded subsets do not install the fonts needed to rebuild the source.
Build logs/auxiliary files are generated locally. The source ZIP normally
excludes the separately delivered compiled PDF.
"""
