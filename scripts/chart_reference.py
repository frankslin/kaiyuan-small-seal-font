#!/usr/bin/env python3
"""Cut reference images out of the Unicode Seal code chart, for checking only.

The chart prints, for every code point, the representative glyph of each
source edition above its sequence number (「C-00038」). align_sequence.py
compares scan crops with these images to verify which code point a crop
belongs to. They are never a drawing source: outlines come from the scans
alone (see AGENTS.md), and neither the chart nor the images are committed;
both live under sources/cache/unicode/.

Usage:
    python3 scripts/chart_reference.py --edition ccz
"""

import argparse
import re
import sys

import cv2
import numpy as np
import requests
import yaml

from fetch_pages import CACHE, MANIFEST, ROOT

CHART_URL = "https://www.unicode.org/charts/PDF/Unicode-18.0/U180-3D000.pdf"
CHART = CACHE / "unicode" / "U180-3D000.pdf"
SIZE = (32, 48)  # width, height of a normalised comparison image


def normalise(ink):
    """Tight-crop a boolean ink mask and fit it, undistorted, into SIZE."""
    ys, xs = np.where(ink)
    if len(xs) == 0:
        return None
    crop = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    h, w = crop.shape
    scale = min(SIZE[0] / w, SIZE[1] / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    small = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((SIZE[1], SIZE[0]), np.float32)
    x0, y0 = (SIZE[0] - new_w) // 2, (SIZE[1] - new_h) // 2
    canvas[y0:y0 + new_h, x0:x0 + new_w] = small
    canvas = cv2.GaussianBlur(canvas, (0, 0), 1.8)  # tolerate small shifts of strokes
    canvas -= canvas.mean()
    norm = np.linalg.norm(canvas)
    return (canvas / norm).ravel() if norm > 0 else None


def reference_path(edition):
    return CACHE / "unicode" / f"reference-{edition}.npz"


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--edition", default="ccz")
    args = parser.parse_args(argv)
    import pymupdf  # only this helper needs it

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    prefix = manifest["editions"][args.edition]["sequence_prefix"]
    if not CHART.exists():
        CHART.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(CHART_URL, headers={"User-Agent": manifest["user_agent"]}, timeout=600)
        response.raise_for_status()
        CHART.write_bytes(response.content)
    label = re.compile(re.escape(prefix) + r"(\d{5})$")
    any_label = re.compile(r"[A-Z]{1,2}-[0-9XY]\d{3,4}$")
    sequences, vectors = [], []
    document = pymupdf.open(CHART)
    for page in document:
        words = page.get_text("words")
        for k, word in enumerate(words):
            match = label.match(word[4])
            if not match or k == 0:
                continue
            x0, y0, x1, y1 = words[k - 1][:4]  # the glyph is the word before its label
            if not (abs((x0 + x1) / 2 - (word[0] + word[2]) / 2) < 8 and 0 <= word[1] - y1 < 12):
                continue
            # the label of the row above reaches into the glyph's line box
            above = [w[3] for w in words if any_label.match(w[4]) and w[3] < (y0 + y1) / 2
                     and w[3] > y0 - 10 and w[0] < x1 and w[2] > x0]
            clip = pymupdf.Rect(x0 - 3, max([y0 - 3] + [a + 0.4 for a in above]), x1 + 3, word[1] + 0.2)
            pixmap = page.get_pixmap(dpi=300, clip=clip, colorspace=pymupdf.csGRAY)
            image = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width)
            ink = (image < 128).astype(np.uint8)
            count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
            for i in range(1, count):  # table rules run through the clip from side to side; glyphs do not
                if stats[i, cv2.CC_STAT_LEFT] <= 1 and stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] >= ink.shape[1] - 1:
                    ink[labels == i] = 0
            vector = normalise(ink > 0)
            if vector is not None:
                sequences.append(int(match.group(1)))
                vectors.append(vector)
    np.savez_compressed(reference_path(args.edition), sequences=np.array(sequences), vectors=np.array(vectors))
    print(f"{len(sequences)} reference images for {args.edition} -> {reference_path(args.edition).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
