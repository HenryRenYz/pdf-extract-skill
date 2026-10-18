# Template selection and body interfaces

Read this when the user supplies a template or asks for another publication style. Follow [template preflight](book-format-preflight.md) for actual source-family admission. The ElegantBook, KOMA and memoir book adapters are bundled in assets/latex-templates/. kaobook and IEEEtran remain research candidates rather than bundled adapters. Every adapter still needs real source-family admission for the current document.

## Choose a document family before a visual style

| Candidate / authoritative source | Useful style and source families | Body interface and adaptation |
|---|---|---|
| [ElegantBook](https://github.com/ElegantLaTeX/ElegantBook) | Colored Chinese/English teaching books, highlighted theorems, technical course notes | Ordinary chapter/section/list/math commands remain usable. Fancy theorem boxes use tcolorbox; simple mode uses amsthm. Inspect the chosen version's theorem arguments, proof endings, fonts, list labels, and bibliography dependencies. Maintenance resumed in 2026; older abandonment notices remain in its documentation. |
| [KOMA-Script](https://ctan.org/pkg/koma-script), especially scrbook | Restrained technical books with dense mathematics, code, tables, and illustrations; scrartcl is a separate article option | Standard body structure; type area, heading fonts, and page styles live in class/preamble APIs. Add Chinese support through ctex when needed. Do not stack the old ctexbook styling package onto it. |
| [memoir](https://ctan.org/pkg/memoir) | Book-oriented monographs and mathematical books, with a choice of chapter and page styles | Native body commands; chapterstyle and layout settings belong in the adapter. Much functionality is integrated in the class, so do not blindly copy the old package list. Chinese ctex integration needs its own sample. |
| [kaobook](https://github.com/fmarotta/kaobook) | Margin notes, small side figures, captions and explanatory reading material | Based on KOMA; ordinary body commands coexist with sidenote/marginfigure/margintoc features. Wide margins reduce main-text width. Dense wide formulas, tables, and annotated code need explicit fit checks; specialized margin placement may need additional compilation passes. |
| [IEEEtran](https://ctan.org/pkg/ieeetran) | Two-column technical papers when that family/style is requested | Use section/subsection, article title/abstract, and class float rules. It is not a drop-in replacement for a chapter-based book. Do not claim publisher compliance solely from selecting this class. |

These styles are choices for re-typesetting, not evidence of the source's actual class. A scanned source says nothing about which TeX class produced it. An attractive margin template is not automatically suitable for a dense textbook, and a book template does not automatically cover two-column papers.

## Keep the body portable

Use core LaTeX first, then established packages, then a documented extension only for a remaining requirement. Package interfaces are not core LaTeX, but are preferable to private wrappers that repeat them.

| Content | Preferred body representation | Appearance belongs in |
|---|---|---|
| Headings | chapter/section/subsection and starred variants according to reviewed hierarchy and document family | Class/ctex/KOMA/memoir settings; do not introduce a private heading command per level |
| Emphasis and font semantics | textbf/emph/textit/textup/textrm/textsf; ctex kaishu for confirmed Kai | fontspec/xeCJK/ctex declarations; do not equate italic, Kai, bold, and Heiti without source evidence |
| Mathematics | equation, align, cases, array, standard symbol commands | AMS and other justified math packages, numbering and display-spacing settings |
| Lists | itemize/enumerate/description and item | enumitem setlist defaults; reviewed local geometry may use its explicit keys |
| Figures and tables | figure/table, includegraphics, caption, label/ref, tabular/tabularx/longtable as appropriate | graphicx/booktabs/array etc.; native dimensions such as linewidth |
| Theorems and proofs | A small declared set such as theorem/definition/lemma and proof; conventional optional title and label | newtheorem/amsthm or compatible box styling; environment names are declared, not all built into LaTeX |
| Code | Verbatim/VerbatimInput or another justified package interface | fvextra/listings configuration; preserve immutable source assets |

For a bullet followed by an emphasized term, the ordinary body can be:

```tex
\begin{itemize}
  \item \textbf{局部性}\quad 相邻数据尽量相邻访问。
  \item \textbf{同步}\quad 在需要的位置交换信息。
\end{itemize}
```

Set the bullet, label width/gap, hanging indent, item spacing, and paragraph spacing in the template using enumitem. The emphasized term is body text, not the bullet label. A description list without a source bullet must not acquire one. Preserve a paragraph within an item as a paragraph, rather than inserting an empty item or forced line breaks.

Do not force an empty private-command API into existence. Ordinary body content can require zero private commands. For a genuinely unsupported feature, the coordinator selects a small, explicit extension set for the run. Record names, arguments, semantics, dependencies, and the reason a standard/package interface is inadequate; keep that interface consistent across workers and templates. A local variable or internal style helper is not automatically an author-facing extension. Renaming every existing command is not simplification.

## User template projects

Set the source-bound profile's `template_dir` to a project directory containing `template.json`:

```json
{
  "schema_version": 1,
  "entry": "main.tex",
  "resources": ["main.tex", "style/layout.sty", "mybook.cls", "LICENSE.txt"],
  "engine": "xelatex",
  "family": "book",
  "body_extensions": []
}
```

The entry has one `@@BODY@@` placeholder and one `\begin{document}`. The orchestrator adapts the user's real example entry: insert the placeholder where native `\input{chapters/...}` statements belong, preserve the class and style resources, and inventory all required relative files. Users do not need to implement a private heading/list protocol or rename their class/style files. The exporter writes a root main.tex and copies the listed resources, preserving relative paths. Generated paths such as chapters/, images/, book-config.tex and export-support.tex are reserved. Legacy book-template.tex/book-style.sty projects remain supported.

The engine is one of xelatex/lualatex/pdflatex. It controls the actual compilation, editor magic comments, .latexmkrc and BUILD.txt together; a contradictory profile override fails. This does not guarantee every content feature works with every engine: confirmed Kai spans currently require the XeLaTeX/ctex adapter; font-glyph fallback requires a fontspec-capable engine; decorated code uses Unicode command characters. An English template can use pdfLaTeX without being silently relabeled XeLaTeX. `render_configuration.language` is zh by default or en for English chapter/caption labels and XeCJK quotation behavior.

The exporter injects package support for the native structures it generates. It does not replace the new project's heading style, fonts, margins or page style. The bundled adapters read generated book-config.tex for run-specific fonts and dimensions; arbitrary user projects may configure these natively in their own preamble. The profile's geometry and fonts must describe the actual template; verify them in the rendered PDF rather than treating a JSON declaration as proof.

Ordinary content needs no private body extension. Select only required extensions in `body_extensions`:

| Versioned extension | Interface and reason |
|---|---|
| text-glyph-fallback-v1 | ExtractTextGlyph{glyph}: preserves text glyph identity when the selected Latin font lacks circled/Roman numerals or text primes; the fallback declaration stays in export-support.tex. |
| annotated-code-v1 | BookCodeUnderline{text} keeps code-column widths, and BookCodeAnchor{id} anchors reviewed bracket annotations. These two retained command names form the shared compatibility interface. Kai and bold-italic use native grouped font commands instead of extra wrappers. |

The sidecar records the extensions actually used and internal scratch commands separately. Book-prefixed page/font configuration and temporary lengths are implementation state, not an author-facing body syntax. List vertical defaults and the bullet live in enumitem/template settings; body term emphasis uses textbf. Local font changes use grouped native commands, figures use linewidth, and code font defaults use fvset. The measured-label scratch value and table-column area still perform necessary local calculations. Do not remove the verified display-spacing hooks merely to lower the macro count.

`template_files` in a native project's ready record pins the complete listed resources plus template.json. Production admission additionally requires `template_dependencies` copied from the actual compile recorder's dependency inventory; the gate checks those files and hashes. This records packages/classes/fonts actually read, while source fidelity and visual checks remain independent. Changing any resource, engine, actual dependency or tool invalidates admission. The archive includes the project resources and the engine instructions; existing opt-in font packaging rules still apply.

Keep source semantics independent of the visual template. Changing within the same document family should normally reuse chapter body files; changing book to article requires mapping semantic heading levels to native commands and handling matter/title/abstract/numbering policies. The exporter maps heading commands and counters by family; an article has no chapter command. The bundled visual adapters are book-oriented. Publisher-specific title/abstract/front-matter policies still require a thin project adapter and real samples; do not claim that changing documentclass alone covers them.

Admit a selected adapter on real source regions covering the families actually present: formula/paragraph boundaries, list markers and continuation paragraphs, fonts, theorems/proof endings, code/grammar/annotations, tables and figures, and narrow columns when present. Synthetic common-body samples demonstrate interface feasibility and appearance only; they do not satisfy source-fidelity acceptance or prove compatibility of the production exporter. Preserve the existing source-font packaging choice and engine metadata in the resulting delivery.

Bundled reading adapters keep chapter-opening page numbers visible and use native widow/club penalties to retain meaningful paragraph tails. These are template settings, not inferred source characters or global requirements on user templates. Excerpts with missing source pages retain explicit boundaries; a complete short final paragraph may still leave a sparse page. Adjust typography within the selected content rather than filling gaps from unselected pages.
