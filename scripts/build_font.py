#!/usr/bin/env python3
"""Build the fonts from glyphs/ (with data/overrides/ applied).

Two fonts from one glyph set: the primary font maps the Seal block code
points, the compatibility font maps the modern characters of kSEAL_MCJK. Where
several seals share a modern character the lowest code point (the 說文
headword) wins, unless data/compat_prefer.txt says otherwise (lines of
`<modern hex> <seal hex>`, kept consistent with OpenCC's @reverse-prefer).

Pure fontTools; no FontForge. While the glyph set is incomplete the build
reports coverage and succeeds; pass --require-complete for release builds.

Usage:
    python3 scripts/build_font.py
    python3 scripts/build_font.py --require-complete
"""

import argparse
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

ROOT = Path(__file__).resolve().parent.parent  # no import from the scan stages: CI builds with fontTools alone

GLYPHS = ROOT / "glyphs"
OVERRIDES = ROOT / "data" / "overrides"
COMPAT_PREFER = ROOT / "data" / "compat_prefer.txt"
SEAL_SOURCES = ROOT / "third_party" / "unicode" / "ucd" / "SealSources.txt"
BUILD = ROOT / "build"
UPM, ASCENDER, DESCENDER = 1000, 880, -120
FIRST, LAST = 0x3D000, 0x3FC3F
FAMILY = "Kaiyuan Small Seal"


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


def build(outlines, cmap, family, version, stem):
    order = [".notdef"] + sorted(outlines)
    metrics = {name: (UPM, 0) for name in order}
    names = {"familyName": family, "styleName": "Regular", "uniqueFontIdentifier": f"{family} {version}",
             "fullName": f"{family} Regular", "version": f"Version {version}",
             "psName": family.replace(" ", "") + "-Regular",
             "licenseDescription": "This Font Software is licensed under the SIL Open Font License, Version 1.1.",
             "licenseInfoURL": "https://openfontlicense.org"}
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
            fb.setupCFF(names["psName"], {"FullName": names["fullName"]}, drawn, {})
        fb.setupHorizontalMetrics(metrics)
        fb.setupHorizontalHeader(ascent=ASCENDER, descent=DESCENDER)
        fb.setupNameTable(names)
        fb.setupOS2(sTypoAscender=ASCENDER, sTypoDescender=DESCENDER, usWinAscent=ASCENDER, usWinDescent=-DESCENDER)
        fb.setupPost()
        path = BUILD / f"{stem}.{'ttf' if is_ttf else 'otf'}"
        fb.save(path)
        print(f"wrote {path.relative_to(ROOT)}  ({len(order) - 1} glyphs, {len(cmap)} mapped code points)")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="0.001")
    parser.add_argument("--require-complete", action="store_true")
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
    build(outlines, primary, FAMILY, args.version, "KaiyuanSmallSeal-Regular")
    build(outlines, modern_map(set(outlines)), FAMILY + " Compat", args.version, "KaiyuanSmallSealCompat-Regular")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
