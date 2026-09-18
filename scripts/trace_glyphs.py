#!/usr/bin/env python3
"""Trace the aligned seal crops into glyphs/uXXXXX.svg.

For every `aligned` row of data/provenance/glyphs.csv: cut the crop box out of
the deskewed half-leaf, enlarge it before thresholding (the scans give seals
only about 85 px tall, and tracing an enlarged grey crop is much smoother than
tracing the small binary one), drop specks and intruding neighbours, trace
with potrace and normalise into the em box.

SVG coordinates are font units with y pointing down: viewBox "0 -880 1000
1000", baseline at y=0. Never edit the output by hand; put fixes in
data/overrides/.

Usage:
    python3 scripts/trace_glyphs.py
    python3 scripts/trace_glyphs.py --only 3D000,3D001
"""

import argparse
import csv
import sys
from functools import lru_cache

import cv2
import numpy as np
import potrace

from fetch_pages import CACHE, ROOT, slugify
from segment_pages import rotate

PROVENANCE = ROOT / "data" / "provenance" / "glyphs.csv"
GLYPHS = ROOT / "glyphs"
TRACE_HEIGHT = 600          # pixels the crop is enlarged to before thresholding
MARGIN = 8                  # source pixels kept around the crop box
MAX_FACTOR = 8.0
TRACED = ("aligned", "inferred", "manual")  # statuses that carry a code point
ASCENDER, DESCENDER = 880, -120
GLYPH_HEIGHT, GLYPH_MAX_WIDTH = 760, 760
CENTRE_X, CENTRE_Y = 500, 380


@lru_cache(maxsize=4)
def half_leaf(edition, title, page, width, crop_x0, crop_x1, rotation):
    path = CACHE / edition / slugify(title) / f"p{page:04d}-w{width}.jpg"
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(f"{path.relative_to(ROOT)}; run fetch_pages.py")
    half = gray[:, crop_x0:crop_x1]
    return rotate(half, rotation) if abs(rotation) > 0.05 else half


def frame_remnants(ink, box_top, frame_top, pitch):
    """Boxes (x0, y0, x1, y1, in crop pixels) of top frame line remnants.

    A headword sits right under the top frame line, and where the line sags
    a sliver of it ends up inside the crop. Unlike the top stroke of a seal
    (王, 示), the sliver lies within a few pixels of the frame and spans the
    whole column.
    """
    found = []
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    for i in range(1, count):
        x, y, w, h = stats[i, :4]
        if box_top + y <= frame_top + 8 and h <= 10 and w >= pitch - 16:
            found.append((int(x), int(y), int(x + w), int(y + h)))
    return found


def crop_mask(row):
    """Binary mask of the seal, enlarged, and the enlargement factor."""
    half = half_leaf(row["edition"], row["commons_title"], int(row["page"]), int(row["render_width"]),
                     int(row["crop_x0"]), int(row["crop_x1"]), float(row["rotation"]))
    x, y, w, h = (int(row[k]) for k in "xywh")
    x0, y0 = max(0, x - MARGIN), max(0, y - MARGIN)
    crop = half[y0:y + h + MARGIN, x0:x + w + MARGIN]
    factor = min(TRACE_HEIGHT / crop.shape[0], MAX_FACTOR)  # flat seals such as 一 would explode
    big = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), factor * 0.35)
    _, mask = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if row.get("frame_top"):
        _, small = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        for fx0, fy0, fx1, fy1 in frame_remnants(small > 0, y0, int(row["frame_top"]), float(row["pitch"])):
            mask[int((fy0 - 1) * factor):int((fy1 + 1) * factor), int(fx0 * factor):int(fx1 * factor)] = 0
    # stroke width from the distance transform; specks are small against it
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    stroke = 2 * float(np.median(distance[distance > 0.5 * distance.max()])) if distance.max() > 0 else 1.0
    inner = np.array([x - x0, y - y0, x - x0 + w, y - y0 + h]) * factor
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, count):
        cx, cy = centroids[i]
        outside = not (inner[0] <= cx <= inner[2] and inner[1] <= cy <= inner[3])
        if stats[i, cv2.CC_STAT_AREA] < stroke * stroke * 0.3 or outside:
            mask[labels == i] = 0
    return mask, factor, stroke


def trace(mask, stroke):
    bitmap = potrace.Bitmap(mask == 0)  # potracer traces the False pixels
    return bitmap.trace(turdsize=int(stroke * stroke * 0.3), alphamax=1.0, opttolerance=0.2)


def svg_path(curves, to_units):
    parts = []
    fmt = lambda p: "%d %d" % tuple(round(v) for v in to_units(p.x, p.y))  # whole font units keep the files small
    for curve in curves:
        parts.append(f"M{fmt(curve.start_point)}")
        for segment in curve.segments:
            if segment.is_corner:
                parts.append(f"L{fmt(segment.c)}L{fmt(segment.end_point)}")
            else:
                parts.append(f"C{fmt(segment.c1)} {fmt(segment.c2)} {fmt(segment.end_point)}")
        parts.append("Z")
    return "".join(parts)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="comma-separated code points (hex)")
    args = parser.parse_args(argv)
    only = {c.strip().upper() for c in args.only.split(",")} if args.only else None

    with PROVENANCE.open(encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["status"] in TRACED and (not only or r["codepoint"] in only)]
    rows.sort(key=lambda r: (r["commons_title"], int(r["page"]), r["side"]))
    GLYPHS.mkdir(exist_ok=True)
    written = 0
    for row in rows:
        mask, factor, stroke = crop_mask(row)
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            print(f"U+{row['codepoint']}: empty crop", file=sys.stderr)
            continue
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        scale = min(GLYPH_HEIGHT / (y1 - y0), GLYPH_MAX_WIDTH / (x1 - x0))
        mid_x, mid_y = (x0 + x1) / 2, (y0 + y1) / 2
        to_units = lambda px, py: ((px - mid_x) * scale + CENTRE_X, (py - mid_y) * scale - CENTRE_Y)
        d = svg_path(trace(mask, stroke), to_units)
        name = f"u{row['codepoint']}"
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {-ASCENDER} 1000 {ASCENDER - DESCENDER}" '
               f'data-source="{row["edition"]} {row["sequence"]}" data-source-height="{(y1 - y0) / factor:.1f}" '
               f'data-units-per-source-pixel="{scale * factor:.3f}">\n<path d="{d}"/>\n</svg>\n')
        (GLYPHS / f"{name}.svg").write_text(svg, encoding="utf-8")
        written += 1
    print(f"traced {written} glyphs into {GLYPHS.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
