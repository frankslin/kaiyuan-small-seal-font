# Kaiyuan Small Seal Font: Agent Guide

This file orients coding agents and contributors. Read it before changing
anything in the repository.

## What this project is

- An open-source Small Seal Script (小篆) font covering the Unicode 18.0 Seal
  block, U+3D000..U+3FC3F, 11,328 code points.
- Glyphs are traced from public-domain woodblock editions of the 說文解字,
  never drawn from modern fonts. Every glyph must be traceable to an edition,
  a Wikimedia Commons file, a page and a crop box.
- Output fonts are released under the SIL Open Font License 1.1. The
  pipeline scripts' license is still to be decided (MIT is the leaning); do
  not add a LICENSE file for them without the maintainer's decision. The
  OFL Reserved Font Name is also still to be decided.
- The sibling project is OpenCC. Its `t2seal` / `seal2t` configs and
  `SealCharacters.txt` dictionary are generated from the same
  `SealSources.txt`; keep code-point-level decisions (which seal form is the
  default for a modern character) consistent between the two projects.

## Repository layout

Existing:

- `third_party/unicode/ucd/`: vendored UCD `SealSources.txt` (Unicode 18.0.0),
  its `LICENSE.txt` (Unicode License v3), `SHA256SUMS` and a provenance
  `README.md`. Never edit `SealSources.txt`; replace it whole when updating
  and refresh `SHA256SUMS`.
- `scripts/verify_seal_sources.py`: integrity check for the vendored data.
  Run it after touching anything under `third_party/unicode/ucd/`.
- `THIRD_PARTY_NOTICES.md`: notices that release archives must carry.
- `CLAUDE.md`: points at this file.

Planned (create as the pipeline lands; keep these names):

- `sources/manifest.yaml`: the only place that names scan sources. One entry
  per Commons file: edition, Commons `File:` title, page range, which pages
  hold which 卷, the institution and its reuse terms, and a checksum of the
  original if known. `sources/cache/` holds downloaded page images and is
  git-ignored.
- `scripts/fetch_pages.py`, `segment_pages.py`, `align_sequence.py`,
  `trace_glyphs.py`, `build_font.py`, `proof_sheets.py`: one stage each,
  each re-runnable from the previous stage's committed output.
- `glyphs/uXXXXX.svg`: traced outlines, one file per code point, named by
  uppercase hex (`u3D000.svg`). Generated, but committed so reviewers can
  diff them.
- `data/provenance/glyphs.csv`: per code point: edition, Commons file, page,
  crop box, source sequence id, status. `data/overrides/uXXXXX.svg` and
  `data/corrections.csv`: human fixes that the build applies on top of the
  automatic result. Never edit files under `glyphs/` by hand; put the fix in
  `data/overrides/`.
- `build/`, `dist/`: generated, git-ignored. `fonts/` is populated by CI
  releases only.
- `docs/`: proof sheets and design notes.

## Source material rules

- Use Wikimedia Commons scans of public-domain editions only. Address a page
  as (Commons file title, 1-based page number). Fetch page renderings
  through the MediaWiki imageinfo API (as `scripts/fetch_pages.py` does)
  rather than downloading whole PDFs or DjVu files; record the width used.
  `Special:Redirect/file/<title>?page=N` ignores `page` for PDFs and always
  returns page 1, and only the standard thumbnail widths (… 1280, 1920,
  3840) are rendered; see `docs/technical-roadmap.md` §1.2.
- Preferred primary edition: 陳昌治本 (同治十二年, one seal headword per
  line). Fall back to other editions only for code points the primary lacks,
  as indicated by the absence of the corresponding source property in
  `SealSources.txt`. Record the fallback in provenance.
- Do not trace, copy or "correct toward" glyphs from 崇羲篆體 (CC BY-ND),
  全字庫說文解字體, the Unicode code chart images, or any other modern seal
  font. They may be looked at for sanity checks but never as a drawing
  source. The one automated sanity check is the shape comparison against
  the code chart in `align_sequence.py`, which decides code points only. Reviewers should be able to verify every outline against its
  recorded scan crop.
- Do not commit scans, page images or crops. Only outlines, metadata and
  small review images explicitly placed under `docs/`.

## Alignment invariants

The `kSEAL_*Src` values are running sequence numbers of seal characters in
each edition, in reading order. The pipeline relies on this:

- Segment pages in strict reading order (right-to-left columns, top-to-bottom
  within a column, 半葉 by 半葉) and number the seal characters found.
- The Nth seal of edition X maps to the code point whose `kSEAL_XSrc` is N.
  Every page must report how many seals it contributed, and the running
  total must match the expected count at chapter boundaries; a single missed
  or extra seal shifts every later code point.
- 重文 (variant forms) are also numbered in the source sequences but are set
  inline in the running text rather than on their own line. The segmenter
  must find them too; treat seal-vs-regular-script classification as a
  first-class component with its own tests.
- Never assign code points by bare counting. `align_sequence.py` aligns the
  detected sequence with the expected one by shape, scoring each crop against
  the edition's glyph in the Unicode code chart (`chart_reference.py`), so a
  false or missed detection becomes a local gap instead of shifting what
  follows. The chart images are a check only and stay in the git-ignored
  cache; see the source material rules. 陳昌治本 has no regular-script
  headword under the seal, so OCR against `kSEAL_MCJK` does not apply to it.
- `data/approved.csv` holds the glyphs a reviewer approved in
  `scripts/review_server.py`. They are locked: `align_sequence.py` pins them
  and writes their rows back verbatim. Never edit or regenerate those rows;
  only the reviewer unlocks them. Read `data/review_feedback.csv` for the
  reviewer's free-text notes before changing the pipeline.
- What the machine is unsure about goes to a human, quickly: low-similarity
  pairs (`inferred`), rejected detections and missing seals are listed in the
  proof, and the decision is one line in `data/corrections.csv`
  (`reject` / `add` / `assign` / `not`; `not` means "this box is not that
  code point", the box stays available for another seal). Never silently
  accept or guess.
- Page numbers restart in every Commons file. Key anything per half-leaf by
  (Commons title, page, side), never by page and side alone.

## Font build conventions

- Units per em 1000. Glyph names `uXXXXX` (uppercase hex, no `uni` prefix
  for supplementary-plane code points). cmap must include a format 12
  subtable; everything in the Seal block is in Plane 3.
- Two fonts from one glyph set: the primary font on Seal block code points,
  and a compatibility font on modern CJK code points. For the compatibility
  mapping use the same default-form rule as OpenCC's `SealCharactersRev`
  (lowest code point, i.e. the 說文 headword, unless an `@reverse-prefer`
  exception says otherwise).
- Build fonts with fontTools / fontmake in a virtual environment; do not
  depend on FontForge or system binaries being present. Pure-Python
  dependencies are preferred so the pipeline runs in CI and in sandboxes.
- Every build must pass: all 11,328 code points present, no empty glyph
  without an explicit `data/corrections.csv` reason, bounding boxes within
  the em box, `verify_seal_sources.py` green.

## Working in restricted environments

- `unicode.org`, `commons.wikimedia.org` and `shuge.org` may be unreachable
  from some agent sandboxes. The vendored `SealSources.txt` exists so that
  nothing at build time needs `unicode.org`. When Commons is unreachable,
  do not substitute other scan sources; leave the manifest entries as TODO
  and say so.
- Use the session scratchpad for downloads and intermediate files, never the
  repository tree, except for outputs that are meant to be committed.

## Communication language

Respond in Traditional Chinese (繁體中文) preferred; Simplified Chinese is
acceptable. Quote code identifiers, file names, Commons titles and dictionary
keys verbatim, without converting their script.

## Commit message style

- First line: action verb plus a concise description, no conventional-commit
  prefix. Example: `Add page segmenter for the 陳昌治本 column grid`.
- Body: one or two sentences on motivation and scope, separated from the
  title by a blank line.
- Multi-area changes: add a `Detailed Changes:` section with bold headers and
  sub-bullets, as in OpenCC.
- A `Claude-Session:` trailer is fine; do not add `Co-Authored-By` lines or
  model identifiers.
- Never put scans, page images or large binaries in a commit.
