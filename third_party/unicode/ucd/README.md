# Unicode Character Database: `SealSources.txt` (Unicode 18.0.0)

This directory vendors one data file from the Unicode Character Database
(UCD), unmodified.

| Item | Value |
| --- | --- |
| File | `SealSources.txt` |
| Unicode version | 18.0.0 (file header: `SealSources-18.0.0.txt`, `Date 2026-06-03`) |
| Canonical URL | <https://www.unicode.org/Public/18.0.0/ucd/SealSources.txt> |
| Specification | [UAX #60: Unicode Small Seal Script](https://www.unicode.org/reports/tr60/) |
| Coverage | 11,328 Small Seal characters, U+3D000..U+3FC3F |
| SHA-256 | see `SHA256SUMS` |
| License | [Unicode License v3](https://www.unicode.org/license.txt) (SPDX `Unicode-3.0`), copy in `LICENSE.txt` |

## What the file provides

Each line is `code point <TAB> property <TAB> value`. The properties are:

- `kSEAL_THXSrc`, `kSEAL_CCZSrc`, `kSEAL_QJZSrc`, `kSEAL_DYCSrc`: the
  sequence number of the character in each of the four source editions of
  《說文解字》 used for the encoding (`TH-ddddd` / `TH-Xddd` / `TH-Yddd`,
  `C-ddddd`, `K-ddddd`, `D-ddddd`). These are running indices of seal
  headwords in reading order, not page or column references. They are what
  lets the glyph pipeline align seal glyphs cut from a scanned edition with
  Unicode code points.
- `kSEAL_MCJK`: the modern CJK unified ideograph equivalent (hex).
- `kSEAL_Rad`: the 說文 radical, as `number.codepoint`.

## Provenance of this copy

`www.unicode.org` was not reachable from the environment in which this copy
was made, so the file was downloaded on 2026-09-18 from the Unicode
Consortium's own `unicodetools` repository, which mirrors the UCD data:

<https://raw.githubusercontent.com/unicode-org/unicodetools/main/unicodetools/data/ucd/dev/SealSources.txt>

The copy was verified against the officially published file indirectly:
OpenCC's `data/scripts/generate_seal_characters.py` was run on it and the
result is byte-identical (apart from the access date in the header) to
`data/dictionary/SealCharacters.txt` in OpenCC, which had been generated from
the file at the canonical URL. That check covers every `kSEAL_MCJK` value and
the four source attestations of all 11,328 code points.

Note that this copy carries the short header of the mirror. Published UCD
files normally also carry a `© Unicode, Inc.` line pointing at
`terms_of_use.html`. When the canonical URL is reachable, replace the file
with the published copy, refresh `SHA256SUMS`, and re-run
`scripts/verify_seal_sources.py`.

## License and how it relates to this project

The Unicode License v3 is a permissive, MIT-style license. Its only
condition is that the copyright and permission notice accompany copies of
the Data Files, or appear in the associated documentation. This directory
satisfies that for the source tree by shipping `LICENSE.txt` next to the
data file; the top-level `THIRD_PARTY_NOTICES.md` repeats the notice for the
project as a whole.

The fonts produced by this project are intended to be released under the
SIL Open Font License 1.1. The OFL applies to the font software (glyph
outlines and font tables). `SealSources.txt` itself is not embedded in the
fonts; what the fonts take from it is the mapping between code points and
the 說文 headwords whose glyphs are traced, together with the radical
grouping. Keeping the Unicode notice in the release archives and in the
documentation is sufficient to meet the Unicode License's condition, and
nothing in that license restricts or conflicts with releasing the fonts
under the OFL.

Do not modify `SealSources.txt` in place. Corrections and project-specific
decisions (for example, which seal form to prefer when several map to the
same modern character) belong in this project's own data files, which can
reference the code points here.

## Updating

1. Download the new `SealSources.txt` from
   `https://www.unicode.org/Public/<version>/ucd/SealSources.txt`.
2. Replace the file, update the version and date in this README, and
   regenerate `SHA256SUMS` with `sha256sum SealSources.txt > SHA256SUMS`.
3. Run `python3 scripts/verify_seal_sources.py`.
4. Check <https://www.unicode.org/license.txt> for changes and refresh
   `LICENSE.txt` if the text changed.
