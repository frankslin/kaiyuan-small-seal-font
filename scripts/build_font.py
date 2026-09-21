#!/usr/bin/env python3
"""Build the fonts from glyphs/ (with data/overrides/ applied).

Two fonts from one glyph set: the primary font maps the Seal block code
points, the compatibility font maps the modern characters of kSEAL_MCJK. Where
several seals share a modern character the lowest code point (the 說文
headword) wins, unless data/compat_prefer.txt says otherwise (lines of
`<modern hex> <seal hex>`, kept consistent with OpenCC's @reverse-prefer).

Pure fontTools; no FontForge. While the glyph set is incomplete the build
reports coverage and succeeds; pass --require-complete for release builds.

Each TTF also gets a WOFF2 web font (needs the `brotli` package; --no-woff2 skips it).

Usage:
    python3 scripts/build_font.py
    python3 scripts/build_font.py --require-complete
"""

import argparse
import json
import re
import sys
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import parse_path
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent  # no import from the scan stages: CI builds with fontTools alone

GLYPHS = ROOT / "glyphs"
OVERRIDES = ROOT / "data" / "overrides"
COMPAT_PREFER = ROOT / "data" / "compat_prefer.txt"
SEAL_SOURCES = ROOT / "third_party" / "unicode" / "ucd" / "SealSources.txt"
BUILD = ROOT / "build"
UPM, ASCENDER, DESCENDER = 1000, 880, -120
FIRST, LAST = 0x3D000, 0x3FC3F
FAMILY = "Kaiyuan Small Seal"
FAMILY_LOCAL = {"zh-TW": "開元小篆", "zh-HK": "開元小篆", "zh": "开元小篆"}  # 開元, not 開源
COMPAT_LOCAL = {"zh-TW": "開元小篆 相容版", "zh-HK": "開元小篆 相容版", "zh": "开元小篆 兼容版"}
COPYRIGHT = "Copyright (c) 2026 Kaiyuan Small Seal Font Contributors (https://github.com/frankslin/kaiyuan-small-seal-font)"  # as in OFL.txt
URL = "https://github.com/frankslin/kaiyuan-small-seal-font"
DESCRIPTION = ("Small Seal Script (小篆) for the Unicode 18.0 Seal block, traced from public-domain scans of the "
               "陳昌治 edition (1873) of the 說文解字 on Wikimedia Commons. Every glyph records its source page and crop box.")
COMPAT_NOTE = " This compatibility font maps the modern characters of kSEAL_MCJK to the same glyphs."
VENDOR = "NONE"  # no registered OpenType vendor ID


def read_outline(path):
    """SVG path (y down) recorded as a font outline (y up)."""
    d = re.search(r'<path[^>]*\sd="([^"]*)"', path.read_text(encoding="utf-8")).group(1)
    recording = RecordingPen()
    parse_path(d, TransformPen(recording, (1, 0, 0, -1, 0, 0)))
    return recording


def load_glyphs():
    outlines = {}
    for path in sorted(GLYPHS.glob("u*.svg")):
        override = OVERRIDES / path.name
        outlines[path.stem] = read_outline(override if override.exists() else path)
    return outlines


def modern_map(available):
    """{modern code point: seal glyph name} for the compatibility font."""
    candidates = {}
    for line in SEAL_SOURCES.read_text(encoding="utf-8").splitlines():
        if line.startswith("U+") and "\tkSEAL_MCJK\t" in line:
            cp, _, value = line.split("\t")
            for modern in value.split():
                candidates.setdefault(int(modern, 16), []).append(int(cp[2:], 16))
    prefer = {}
    if COMPAT_PREFER.exists():
        for line in COMPAT_PREFER.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                modern, seal = line.split()[:2]
                prefer[int(modern, 16)] = int(seal, 16)
    mapping = {}
    for modern in candidates.keys() | prefer.keys():  # a preference may name a modern character kSEAL_MCJK lacks (玉)
        chosen = prefer.get(modern) or min(candidates[modern])  # lowest code point is the headword
        if f"u{chosen:05X}" in available:
            mapping[modern] = f"u{chosen:05X}"
    return mapping


def name_strings(family, local, version):
    """Name table records; family and full names also in Chinese for Windows and macOS menus."""
    localised = lambda english, suffix="": {"en": english + suffix, **{tag: name for tag, name in local.items()}}
    return {"copyright": COPYRIGHT,
            "familyName": localised(family), "styleName": "Regular",
            "uniqueFontIdentifier": f"{version};{VENDOR};{family.replace(' ', '')}-Regular",
            "fullName": localised(family, " Regular"), "version": f"Version {version}",
            "psName": family.replace(" ", "") + "-Regular",
            "manufacturer": "Kaiyuan Small Seal Font Contributors",
            "description": DESCRIPTION + (COMPAT_NOTE if "Compat" in family else ""),
            "vendorURL": URL,
            "licenseDescription": ("This Font Software is licensed under the SIL Open Font License, Version 1.1. "
                                   "This license is available with a FAQ at: https://openfontlicense.org"),
            "licenseInfoURL": "https://openfontlicense.org"}


def build(outlines, cmap, family, local, version, stem, modern=False, woff2=True):
    order = [".notdef"] + sorted(outlines)
    metrics = {name: (UPM, 0) for name in order}
    names = name_strings(family, local, version)
    for is_ttf in (True, False):
        fb = FontBuilder(UPM, isTTF=is_ttf)
        fb.setupGlyphOrder(order)
        fb.setupCharacterMap(cmap)  # adds a format 12 subtable for Plane 3
        drawn = {}
        for name in order:
            if is_ttf:
                pen = TTGlyphPen(None)
                target = Cu2QuPen(pen, max_err=1.0, reverse_direction=False)
            else:
                pen = target = T2CharStringPen(UPM, None)
            if name != ".notdef":
                outlines[name].replay(target)
            drawn[name] = pen.glyph() if is_ttf else pen.getCharString()
        if is_ttf:
            fb.setupGlyf(drawn)
        else:
            fb.setupCFF(names["psName"], {"FullName": names["fullName"]["en"], "FamilyName": family, "Weight": "Regular",
                                          "Notice": COPYRIGHT, "version": version}, drawn, {})
        fb.setupHorizontalMetrics(metrics)
        fb.setupHorizontalHeader(ascent=ASCENDER, descent=DESCENDER)
        fb.setupNameTable(names)
        # fsType 0: the OFL allows embedding without restriction. fsSelection: REGULAR | USE_TYPO_METRICS.
        # Unicode range bit 57 (non-plane-0) for the Seal block; the compatibility font adds CJK Unified
        # Ideographs (59) and the Chinese code pages (18 simplified, 20 traditional).
        fb.setupOS2(sTypoAscender=ASCENDER, sTypoDescender=DESCENDER, usWinAscent=ASCENDER, usWinDescent=-DESCENDER,
                    sTypoLineGap=0, fsType=0, fsSelection=0x40 | 0x80, version=4, achVendID=VENDOR,
                    ulUnicodeRange2=(1 << 25) | ((1 << 27) if modern else 0),
                    ulCodePageRange1=((1 << 18) | (1 << 20)) if modern else 0)
        fb.setupPost()
        fb.font["head"].fontRevision = float(re.match(r"\d+(?:\.\d+)?", version).group(0))
        path = BUILD / f"{stem}.{'ttf' if is_ttf else 'otf'}"
        fb.save(path)
        print(f"wrote {path.relative_to(ROOT)}  ({len(order) - 1} glyphs, {len(cmap)} mapped code points)")
        if is_ttf and woff2:  # the web font: the TrueType flavour compresses better than CFF (about 10 MB against 23)
            web = TTFont(path)
            web.flavor = "woff2"
            web.save(path.with_suffix(".woff2"))
            print(f"wrote {path.with_suffix('.woff2').relative_to(ROOT)}  ({path.with_suffix('.woff2').stat().st_size / 1e6:.1f} MB)")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="0.001")
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--no-woff2", action="store_true", help="skip the web fonts (they take about a minute each)")
    args = parser.parse_args(argv)

    outlines = load_glyphs()
    if not outlines:
        print("no glyphs; run trace_glyphs.py", file=sys.stderr)
        return 1
    problems = []
    for name, outline in outlines.items():
        bounds = BoundsPen(None)
        outline.replay(bounds)
        if bounds.bounds is None:
            problems.append(f"{name}: empty outline")
            continue
        x0, y0, x1, y1 = bounds.bounds
        if x0 < 0 or x1 > UPM or y0 < DESCENDER or y1 > ASCENDER:
            problems.append(f"{name}: bounds {bounds.bounds} outside the em box")
        area = AreaPen(None)
        outline.replay(area)
        if area.value > 0:  # TrueType wants clockwise outer contours
            reversed_outline = RecordingPen()
            from fontTools.pens.reverseContourPen import ReverseContourPen
            outline.replay(ReverseContourPen(reversed_outline))
            outlines[name] = reversed_outline
    missing = [cp for cp in range(FIRST, LAST + 1) if f"u{cp:05X}" not in outlines]
    print(f"coverage: {len(outlines)} of {LAST - FIRST + 1} code points")
    for problem in problems:
        print("  " + problem, file=sys.stderr)
    if problems or (args.require_complete and missing):
        print(f"build checks failed ({len(problems)} problems, {len(missing)} missing)", file=sys.stderr)
        return 1

    BUILD.mkdir(exist_ok=True)
    primary = {int(name[1:], 16): name for name in outlines}
    # which code points have a glyph so far, for app/ and other tools that must not show a blank
    (BUILD / "coverage.json").write_text(json.dumps(sorted(f"{cp:05X}" for cp in primary)), encoding="utf-8")
    woff2 = not args.no_woff2
    if woff2:
        try:
            import brotli  # noqa: F401  (fontTools needs it to write WOFF2)
        except ImportError:
            print("no WOFF2: pip install brotli", file=sys.stderr)
            woff2 = False
    build(outlines, primary, FAMILY, FAMILY_LOCAL, args.version, "KaiyuanSmallSeal-Regular", woff2=woff2)
    build(outlines, modern_map(set(outlines)), FAMILY + " Compat", COMPAT_LOCAL, args.version,
          "KaiyuanSmallSealCompat-Regular", modern=True, woff2=woff2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
