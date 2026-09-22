#!/usr/bin/env python3
"""Segment cached page images into seal characters in reading order.

Stage 1 (geometry): deskew, find the printed frame of each 半葉, fit the column
grid, and cut every column into ink runs separated by blank rows.
Stage 2 (seals): classify runs of ink as seal or regular script. Seals take
two character slots of the 22-slot column grid, regular script one. Columns
that start flush with the top frame begin with a headword seal (continuation
columns are indented by one slot), which gives free training labels for a
HOG + logistic regression classifier; that classifier then finds the inline
重文.

Output: build/pages/<edition>/<slug>/pNNNN.json with the geometry and the
seals found, in reading order, and optional overlays for review.

Usage:
    python3 scripts/segment_pages.py --edition ccz --juan 卷一上
    python3 scripts/segment_pages.py --edition ccz --juan 卷一上 --overlay
"""

import argparse
import json
import pickle
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml
from skimage.feature import hog
from skimage.morphology import skeletonize
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from fetch_pages import CACHE, MANIFEST, ROOT, parse_page_arg, slugify

BUILD = ROOT / "build"
SLOTS_PER_COLUMN = 22
PATCH = (48, 64)  # width, height of the classifier input


# ---------------------------------------------------------------- geometry

def binarize(gray, strict=False):
    """Ink mask. The local threshold follows uneven paper tone and keeps the
    faint column rules; `strict` adds an absolute threshold that drops them
    along with the library's pale watermark and the paper texture."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    mask = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 51, 18)
    if strict:
        paper = float(np.median(blur))
        ink = float(np.percentile(blur, 2))
        mask[blur > ink + (paper - ink) * 0.62] = 0
    return mask


def groups(profile, threshold, gap=6):
    """Runs of indices where profile > threshold, as (start, end, peak)."""
    out = []
    for i in np.where(profile > threshold)[0]:
        if out and i - out[-1][1] <= gap:
            out[-1][1] = i
            out[-1][2] = max(out[-1][2], profile[i])
        else:
            out.append([i, i, profile[i]])
    return [(int(a), int(b), float(p)) for a, b, p in out]


def long_lines(bw, horizontal, length):
    size = (length, 1) if horizontal else (1, length)
    return cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size))


def frame_rows(bw):
    """Top and bottom frame lines as (start, end) row ranges."""
    h, w = bw.shape
    profile = long_lines(bw, True, w // 6).sum(1) / 255
    found = [g for g in groups(profile, w * 0.25) if h * 0.03 < g[0] and g[1] < h * 0.97]
    # the library watermark has faint horizontal edges; frame lines are far stronger
    strongest = max((g[2] for g in found), default=0)
    found = [g for g in found if g[2] >= strongest * 0.45]
    tops = [g for g in found if g[0] < h * 0.55]
    bottoms = [g for g in found if g[0] > h * 0.6]
    if not tops or not bottoms:
        raise ValueError("frame top/bottom not found")
    return tops[-1][:2], bottoms[0][:2]


def skew_angle(bw):
    """Angle in degrees of the top frame line, measured on its upper edge."""
    h, w = bw.shape
    (t0, t1), _ = frame_rows(bw)
    band = long_lines(bw, True, w // 8)[max(0, t0 - 25):t1 + 25]
    xs, ys = [], []
    for x in range(0, w, 8):
        rows = np.where(band[:, x] > 0)[0]
        if len(rows):
            xs.append(x)
            ys.append(rows[0])
    if len(xs) < 40:
        return 0.0
    xs, ys = np.array(xs), np.array(ys)
    slope = np.polyfit(xs, ys, 1)[0]
    for _ in range(2):  # drop outliers (gutter, stains) and refit
        keep = np.abs(ys - np.polyval(np.polyfit(xs, ys, 1), xs)) < 4
        if keep.sum() < 40:
            break
        xs, ys = xs[keep], ys[keep]
        slope = np.polyfit(xs, ys, 1)[0]
    return float(np.degrees(np.arctan(slope)))


def rotate(gray, angle):
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


COLUMN_PER_HEIGHT = 0.0699  # column pitch over frame height, median of all 1,226 half-leaves of 陳昌治本


def text_ratio(ink, origin, pitch, columns):
    """Ink in the middle half of the columns over ink in the half around the rules.

    About 2 on a well-fitted grid and about 1 when the grid sits half a column
    off, which happens where the rules are faint and the vertical strokes of
    the text are taken for rules.
    """
    inside = around = 0.0
    for k in range(columns):
        a, b = origin + pitch * k, origin + pitch * (k + 1)
        q = (b - a) / 4
        inside += ink[int(a + q):int(b - q)].sum()
        around += ink[int(a):int(a + q)].sum() + ink[int(b - q):int(b)].sum()
    return inside / max(1.0, around)


def fit_grid(profile, columns, inner_at_start, slack=70, ink=None, pitches=None):
    """Best (origin, pitch) of columns+1 equally spaced rules.

    The inner frame line lies within `slack` pixels of the gutter edge of the
    half image; the 版心 and the book edge lie beyond the outer frame line.
    With `ink` (ink per pixel column), a fit whose text straddles the rules
    gives way to the best fit about half a column away if the text sits
    clearly better in that one.
    """
    if ink is not None:
        origin, pitch = fit_grid(profile, columns, inner_at_start, slack, pitches=pitches)
        ratio = text_ratio(ink, origin, pitch, columns)
        if ratio < 1.4:
            best = (-1.0, origin, pitch)
            smooth = np.minimum(np.convolve(profile, np.ones(5), mode="same"), profile.max() * 0.25)
            for shift in (-0.5, 0.5):
                for p2 in np.arange(pitch - 1.5, pitch + 1.5, 0.1):
                    for o2 in np.arange(origin + shift * pitch - 10, origin + shift * pitch + 10, 1.0):
                        if o2 < 0 or o2 + p2 * columns > len(profile) - 1:
                            continue
                        pos = np.round(o2 + p2 * np.arange(columns + 1)).astype(int)
                        score = smooth[pos].sum() + smooth[pos[0]] + smooth[pos[-1]]
                        if score > best[0]:
                            best = (float(score), float(o2), float(p2))
            if best[0] >= 0 and text_ratio(ink, best[1], best[2], columns) > max(1.5, ratio * 1.3):
                return best[1], best[2]
        return origin, pitch
    w = len(profile)
    # Count rules hit rather than summing their strength, otherwise the heavy
    # frame lines, the 版心 and the book edge outweigh the faint column rules.
    smooth = np.minimum(np.convolve(profile, np.ones(5), mode="same"), profile.max() * 0.25)
    best = (-1.0, 0.0, 0.0)
    nominal = w / (columns + 1.2)
    for pitch in (np.arange(pitches[0], pitches[1], 0.1) if pitches else np.arange(nominal * 0.9, nominal * 1.12, 0.1)):
        span = pitch * columns
        origins = np.arange(0, slack, 1.0) if inner_at_start else np.arange(w - 1 - span - slack, w - 1 - span, 1.0)
        for origin in origins:
            if origin < 0 or origin + span > w - 1:
                continue
            pos = np.round(origin + pitch * np.arange(columns + 1)).astype(int)
            score = smooth[pos].sum() + smooth[pos[0]] + smooth[pos[-1]]  # frame lines are heavy
            if score > best[0]:
                best = (float(score), float(origin), float(pitch))
    if best[0] < 0:
        raise ValueError("column grid not found")
    return best[1], best[2]


def find_gutter(bw):
    """x of the gutter between the two half-leaves of a spread."""
    h, w = bw.shape
    profile = long_lines(bw, False, h // 10).sum(0) / 255
    zone = [g for g in groups(profile, profile.max() * 0.4, gap=3) if w * 0.4 < g[0] < w * 0.6]
    pairs = [(zone[i + 1][0] - zone[i][1], zone[i], zone[i + 1]) for i in range(len(zone) - 1)]
    pairs = [q for q in pairs if q[0] < w * 0.03]
    if pairs:
        _, left, right = min(pairs, key=lambda q: abs((q[1][1] + q[2][0]) / 2 - w / 2))
        return (left[1] + right[0]) // 2
    wide = [g for g in zone if g[1] - g[0] >= 15]
    if wide:
        g = min(wide, key=lambda g: abs((g[0] + g[1]) / 2 - w / 2))
        return (g[0] + g[1]) // 2
    return w // 2


def half_geometry(gray, columns, side):
    """Deskew one half-leaf and locate its frame and column rules."""
    angle = skew_angle(binarize(gray))
    if abs(angle) > 0.05:
        gray = rotate(gray, angle)
    bw = binarize(gray)
    (t0, t1), (b0, b1) = frame_rows(bw)
    top, bottom = t1 + 1, b0 - 1
    profile = long_lines(bw[top:bottom], False, (bottom - top) // 5).sum(0) / 255
    strict = binarize(gray, strict=True)[top + 20:bottom - 20]
    # The block is cut to one proportion: a column is 0.0699 of the frame height.
    # Without this bound, faint rules let the fit stretch to 92–94 px (true: 83–86).
    expect = (bottom - top) * COLUMN_PER_HEIGHT
    origin, pitch = fit_grid(profile, columns, inner_at_start=(side == "right"), ink=(strict > 0).sum(0).astype(float),
                             pitches=(expect * 0.97, expect * 1.03))
    rules = []
    for k in range(columns + 1):
        x = int(round(origin + pitch * k))
        lo, hi = max(0, x - 4), min(len(profile), x + 5)
        local = profile[lo:hi]
        rules.append(int(lo + local.argmax()) if local.max() > (bottom - top) * 0.15 else x)
    geometry = {"rotation": round(angle, 3), "top": int(top), "bottom": int(bottom), "rules": rules,
                "pitch": round(pitch, 2), "slot": round((bottom - top) / SLOTS_PER_COLUMN, 2)}
    return geometry, gray, binarize(gray, strict=True)


def column_runs(ink, min_rows=3):
    """Ink runs of a column as [y0, y1) pairs, split at blank rows."""
    rows = ink.sum(1) > 0
    runs, start = [], None
    for y, on in enumerate(rows):
        if on and start is None:
            start = y
        elif not on and start is not None:
            runs.append([start, y])
            start = None
    if start is not None:
        runs.append([start, len(rows)])
    merged = []
    for run in runs:
        if merged and run[0] - merged[-1][1] <= 2:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    return [r for r in merged if r[1] - r[0] >= min_rows]


def blank_frame_rows(ink, reach=30):
    """Zero, in place, the rows of a column strip taken up by the frame lines.

    A sagging frame line enters the strip at its very edge and fills whole
    rows from there; the wide top stroke of a seal (王, 示) is separated from
    the edge by blank rows and must stay.
    """
    fill = ink.mean(1)
    k = 0
    while k < reach and (fill[k] >= 0.7 or (k < 3 and fill[k + 1:k + 4].max() >= 0.7)):
        k += 1
    if k:
        ink[:k + 1] = 0
    k = 0
    while k < reach and (fill[-1 - k] >= 0.7 or (k < 3 and fill[-4 - k:-1 - k].max() >= 0.7)):
        k += 1
    if k:
        ink[len(ink) - k - 1:] = 0


def clean_column(bw, x0, x1, top, bottom):
    """Column ink without rule remnants and specks."""
    inset = 5
    ink = (bw[top:bottom, x0 + inset:x1 - inset + 1] > 0).astype(np.uint8)
    # The frame line sags where the page curves, so part of it may lie below
    # `top` in this column: blank everything down to the last row it fills.
    blank_frame_rows(ink)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    for i in range(1, count):
        x, y, cw, ch, area = stats[i]
        speck = area < 8
        rule = cw <= 8 and ch > 120 and (x <= 12 or x + cw >= ink.shape[1] - 12)
        if speck or rule:
            ink[labels == i] = 0
    return ink, x0 + inset


def segment_half(gray, columns, side):
    geometry, gray, bw = half_geometry(gray, columns, side)
    top, bottom, rules = geometry["top"], geometry["bottom"], geometry["rules"]
    cols = []
    for c in range(columns - 1, -1, -1):  # right to left
        ink, x_off = clean_column(bw, rules[c] + 1, rules[c + 1] - 1, top, bottom)
        cols.append({"side": side, "column": columns - c, "x0": x_off, "x1": x_off + ink.shape[1],
                     "runs": [[top + a, top + b] for a, b in column_runs(ink)]})
    return geometry, cols, gray, bw


# ------------------------------------------------------------- candidates

def ink_box(bw, x0, x1, y0, y1):
    ys, xs = np.where(bw[y0:y1, x0:x1] > 0)
    if len(xs) == 0:
        return None
    return [int(x0 + xs.min()), int(y0 + ys.min()), int(x0 + xs.max() + 1), int(y0 + ys.max() + 1)]


def candidates(col, top, bottom, slot, bw):
    """y-intervals that could hold one two-slot seal.

    Groups of consecutive ink runs are exact when the seal is set off by
    blank rows. A seal touching its neighbours shares their run; such long
    runs are cut at the valleys of their row profile, and also swept with a
    sliding window in case the join has no valley.
    """
    runs = col["runs"]
    out = []
    for i in range(len(runs)):
        for j in range(i, len(runs)):
            height = runs[j][1] - runs[i][0]
            if height > slot * 2.25:
                break
            if j > i and runs[j][0] - runs[j - 1][1] > slot * 0.9:
                break
            if height >= slot * 1.2:
                out.append((runs[i][0], runs[j][1]))
    for y0, y1 in runs:
        if y1 - y0 <= slot * 2.25:
            continue
        profile = (bw[y0:y1, col["x0"]:col["x1"]] > 0).sum(1)
        low = profile <= max(2, np.median(profile) * 0.3)
        cuts = [y0] + [y0 + (a + b) // 2 for a, b in column_runs(low[:, None], min_rows=1)] + [y1]
        for a in range(len(cuts)):
            for b in range(a + 1, len(cuts)):
                if slot * 1.2 <= cuts[b] - cuts[a] <= slot * 2.25:
                    out.append((int(cuts[a]), int(cuts[b])))
        for start in np.arange(y0, y1 - slot * 1.5, slot / 4):
            out.append((int(start), int(min(start + slot * 2, y1))))
    return sorted(set(out))


def headword_interval(col, top, slot):
    """y-interval of the headword seal, or None for a continuation column."""
    runs = col["runs"]
    if not runs or runs[0][0] - top > slot * 0.45:
        return None  # indented by one slot
    j = 0
    while j + 1 < len(runs) and runs[j + 1][1] - top <= slot * 2.12:
        j += 1
    if runs[j][1] - top > slot * 2.25:
        return (runs[0][0], int(top + slot * 2))  # seal touches the text below it
    if runs[j][1] - top < slot * 1.2:
        return None
    return (runs[0][0], runs[j][1])


def flat_headword(col, top, slot):
    """Box interval of a headword that is a single flat stroke (the seal 一).

    It sits in the middle of its two slots, so the column looks indented like
    a continuation column; but the first regular character of a continuation
    column is centred half a slot lower.
    """
    runs = col["runs"]
    if len(runs) < 2:
        return None
    y0, y1 = runs[0]
    centre = ((y0 + y1) / 2 - top) / slot
    if y1 - y0 < slot * 0.35 and 0.6 <= centre <= 1.25 and runs[1][0] - top >= slot * 1.8:
        return (y0, y1)
    return None


def overlap(a, b):
    """Intersection over the shorter of two intervals."""
    inter = min(a[1], b[1]) - max(a[0], b[0])
    return max(0, inter) / max(1, min(a[1] - a[0], b[1] - b[0]))


def stroke_stats(patch):
    """Stroke width statistics; seal strokes are of even width, 楷書 is not."""
    big = cv2.resize(patch, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, ink = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = ink > 0
    if ink.sum() < 50:
        return [0.0, 0.0, 0.0, 0.0]
    widths = 2 * cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 5)[skeletonize(ink)] / 3
    if len(widths) == 0:
        return [0.0, 0.0, 0.0, float(ink.mean())]
    return [float(np.median(widths)), float(np.percentile(widths, 90)), float(widths.std()), float(ink.mean())]


N_EXTRA = 13


def is_small_print(mask, slot):
    """Double-row small print (反切, 校語): a blank strip runs down the middle
    of the box and each side holds several short components. Seals with an
    open middle (八, 門, left-right compounds) have tall ones."""
    h, w = mask.shape
    strip = mask[:, w // 2 - 4:w // 2 + 5]
    best = max(range(strip.shape[1]), key=lambda k: -strip[:, k].sum())
    if strip[:, best].mean() > 0.04:
        return False
    sides, tallest = [], 0
    for part in (mask[:, :w // 2], mask[:, w // 2:]):
        count, _, stats, _ = cv2.connectedComponentsWithStats(part.astype(np.uint8), connectivity=8)
        real = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 6]
        sides.append(len(real))
        tallest = max([tallest] + [stats[i, cv2.CC_STAT_HEIGHT] for i in real])
    return min(sides) >= 2 and sum(sides) >= 6 and tallest < slot * 0.65  # a small character is half a slot


def features(gray, bw, col, slot, y0, y1, is_head=False):
    """(HOG + structural features, tight ink box) of a window, or (None, None).

    The last N_EXTRA values are structural: size, centring, ink density,
    stroke widths and how the window divides into ink runs. Two stacked
    regular characters are two runs of one slot each; a seal is mostly one
    run of nearly two slots.
    """
    box = ink_box(bw, col["x0"], col["x1"], y0, y1)
    if box is None:
        return None, None
    bx0, by0, bx1, by1 = box
    width, height = bx1 - bx0, by1 - by0
    column_width = col["x1"] - col["x0"]
    mask = bw[by0:by1, bx0:bx1] > 0
    # narrow boxes are rule remnants, except for a headword such as 丨
    if height < slot * 0.9 or (width < column_width * 0.3 and not is_head) or mask.mean() < 0.05:
        return None, None
    if not is_head and is_small_print(mask, slot):
        return None, None
    patch = gray[by0:by1, bx0:bx1]
    # pad to the 3:4 classifier aspect so that shapes are not distorted
    target = max(width / PATCH[0], height / PATCH[1])
    pad_x, pad_y = int(round(target * PATCH[0] - width)) // 2, int(round(target * PATCH[1] - height)) // 2
    paper = int(np.percentile(patch, 90))
    canvas = cv2.copyMakeBorder(patch, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_CONSTANT, value=paper)
    canvas = cv2.normalize(cv2.resize(canvas, PATCH, interpolation=cv2.INTER_AREA), None, 0, 255, cv2.NORM_MINMAX)
    shape = hog(canvas, orientations=9, pixels_per_cell=(8, 8), cells_per_block=(2, 2), feature_vector=True)
    offset = ((bx0 + bx1) / 2 - (col["x0"] + col["x1"]) / 2) / column_width
    rows = mask.sum(1)
    pieces = column_runs(mask, min_rows=1)
    tallest = max(b - a for a, b in pieces)
    gaps = [(pieces[k + 1][0] - pieces[k][1], (pieces[k + 1][0] + pieces[k][1]) / 2 / height) for k in range(len(pieces) - 1)]
    gap, gap_at = max(gaps) if gaps else (0, 0.5)
    middle = rows[int(height * 0.4):int(height * 0.6) + 1]
    extra = [width / column_width, height / slot, offset, abs(offset), float(mask.mean()),
             len(pieces), tallest / height, gap / slot, abs(gap_at - 0.5), float(middle.min()) / width]
    extra += stroke_stats(patch)[:3]
    assert len(extra) == N_EXTRA
    return np.concatenate([shape, extra]).astype(np.float32), box


class SealClassifier:
    """Logistic regression on HOG, stacked under gradient boosting that also
    sees the structural features."""

    def fit(self, X, y):
        self.shape_model = make_pipeline(StandardScaler(), LogisticRegression(C=0.02, max_iter=3000, class_weight="balanced"))
        held_out = cross_val_predict(self.shape_model, X[:, :-N_EXTRA], y, cv=5, method="decision_function")
        self.shape_model.fit(X[:, :-N_EXTRA], y)
        self.top_model = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.1, class_weight="balanced")
        self.top_model.fit(np.column_stack([held_out, X[:, -N_EXTRA:]]), y)
        self.cv_accuracy = float(((held_out > 0) == (y == 1)).mean())
        return self

    def fit_clean(self, X, y):
        """Fit, drop bootstrap positives the model itself rejects when held
        out (flush-top regular script such as 卷 titles), and fit again."""
        stacked = lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=150, class_weight="balanced")
        self.fit(X, y)
        shape = cross_val_predict(self.shape_model, X[:, :-N_EXTRA], y, cv=5, method="decision_function")
        proba = cross_val_predict(stacked(), np.column_stack([shape, X[:, -N_EXTRA:]]), y, cv=5, method="predict_proba")[:, 1]
        keep = ~((y == 1) & (proba < 0.3))
        self.dropped = int((~keep).sum())
        return self.fit(X[keep], y[keep])

    def score(self, X):
        shape = self.shape_model.decision_function(X[:, :-N_EXTRA])
        return self.top_model.predict_proba(np.column_stack([shape, X[:, -N_EXTRA:]]))[:, 1]


# ------------------------------------------------------------------ driver

def manifest_blocks(manifest, edition, juan):
    for entry in manifest["editions"][edition].get("files") or []:
        for block in entry.get("pages") or []:
            if juan and block.get("juan") not in juan.split(","):
                continue
            yield entry, block


def block_halves(entry, block):
    """(page, side) pairs of a 卷 in reading order."""
    skip = set(entry.get("skip_pages") or [])
    for page in range(block["first"], block["last"] + 1):
        if page in skip:
            continue
        for side in ("right", "left"):
            if page == block["first"] and block.get("first_side", "right") == "left" and side == "right":
                continue
            if page == block["last"] and block.get("last_side", "left") == "right" and side == "left":
                continue
            yield page, side


def iou(a, b):
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(1, (a[1] - a[0]) + (b[1] - b[0]) - inter)


def training_set(halves, verified):
    """Labels from the layout (headwords, and the text below them) and from
    the columns of every 部 whose seal count the aligner confirmed."""
    X, y = [], []
    for (slug, page, side), st in halves.items():
        geometry = st["geometry"]
        top, bottom, slot = geometry["top"], geometry["bottom"], geometry["slot"]
        for col in st["cols"]:
            known = verified.get((slug, page, side, col["column"]))
            if known is None:
                continue
            for window in candidates(col, top, bottom, slot, st["bw"]):
                vec, box = features(st["gray"], st["bw"], col, slot, *window, is_head=True)
                if vec is None:
                    continue
                best = max((iou((box[1], box[3]), tuple(k)) for k in known), default=0.0)
                if best >= 0.8 or best <= 0.3:
                    X.append(vec)
                    y.append(int(best >= 0.8))
    n_verified = len(y)
    for (slug, page, side), st in halves.items():
        geometry = st["geometry"]
        top, bottom, slot = geometry["top"], geometry["bottom"], geometry["slot"]
        for col in st["cols"]:
            head = col["head"] = headword_interval(col, top, slot)
            if head is None or (slug, page, side, col["column"]) in verified:
                continue
            vec, _ = features(st["gray"], st["bw"], col, slot, *head, is_head=True)
            if vec is not None:
                X.append(vec)
                y.append(1)
            # Slots 2 to 5 of a headword column always hold the start of the
            # 說解 in regular script, so windows there are negatives, as are
            # windows straddling the headword and the first character.
            for window in candidates(col, top, bottom, slot, st["bw"]):
                if window[1] > top + slot * 6.2 or overlap(window, head) > 0.5:
                    continue
                vec, _ = features(st["gray"], st["bw"], col, slot, *window)
                if vec is not None:
                    X.append(vec)
                    y.append(0)
    # Slots 2 to 5 hold large characters only, so the layout labels above lack
    # double-row small print (反切, 校語). Windows that a strict rule is sure
    # about supply it; the model then handles the touching, messier cases.
    n_layout = len(y)
    for st in halves.values():
        geometry = st["geometry"]
        top, bottom, slot = geometry["top"], geometry["bottom"], geometry["slot"]
        for col in st["cols"]:
            for window in candidates(col, top, bottom, slot, st["bw"]):
                box = ink_box(st["bw"], col["x0"], col["x1"], *window)
                if box is None or box[3] - box[1] < slot * 0.9:
                    continue
                if is_small_print(st["bw"][box[1]:box[3], box[0]:box[2]] > 0, slot):
                    vec, _ = features(st["gray"], st["bw"], col, slot, *window, is_head=True)
                    if vec is not None:
                        X.append(vec)
                        y.append(0)
    print(f"{len(y) - n_layout} small-print windows added as negatives")
    print(f"{n_verified} training windows from verified 部, {n_layout - n_verified} from the layout")
    return np.array(X), np.array(y)


def detect(st, model, threshold):
    geometry = st["geometry"]
    top, bottom, slot = geometry["top"], geometry["bottom"], geometry["slot"]
    seals, maybes = [], []
    for col in st["cols"]:
        scored = []
        windows = candidates(col, top, bottom, slot, st["bw"])
        if col["head"] and col["head"] not in windows:
            windows.append(col["head"])
        for window in windows:
            is_head = col["head"] == window
            if col["head"] and not is_head and overlap(window, col["head"]) > 0.25:
                continue
            vec, box = features(st["gray"], st["bw"], col, slot, *window, is_head=is_head)
            if vec is None:
                continue
            score = float(model.score(vec[None])[0])
            # The layout already suggests a headword, so accept it on weaker
            # evidence; flush-top regular script (卷 titles) still scores ~0.
            if score >= (0.1 if is_head else threshold):
                scored.append((score, window, box, is_head))
            elif score >= 0.15:
                maybes.append((score, window, box, col["column"]))
        flat = flat_headword(col, top, slot)
        if flat:
            box = ink_box(st["bw"], col["x0"], col["x1"], *flat)
            if box and box[2] - box[0] > (col["x1"] - col["x0"]) * 0.45:
                scored.append((0.5, flat, box, True))
        chosen = []
        for item in sorted(scored, key=lambda t: -t[0]):
            if all(overlap(item[1], other[1]) <= 0.25 for other in chosen):
                chosen.append(item)
        for score, window, box, is_head in sorted(chosen, key=lambda t: t[1]):
            seals.append({"column": col["column"], "box": box, "kind": "headword" if is_head else "inline",
                          "score": round(score, 3)})
        if col["head"] and not any(item[3] for item in chosen):
            st.setdefault("rejected_heads", []).append(col["column"])
    for k, seal in enumerate(seals, 1):
        seal["order"] = k
    # near misses, for the review queue: best-scoring first, without duplicates
    taken = [(s["column"], (s["box"][1], s["box"][3])) for s in seals]
    near = []
    for score, window, box, column in sorted(maybes, key=lambda t: -t[0]):
        span = (box[1], box[3])
        if all(c != column or overlap(span, other) <= 0.25 for c, other in taken):
            taken.append((column, span))
            near.append({"column": column, "box": box, "score": round(score, 3)})
    st["near_misses"] = near
    return seals


def draw_overlay(st, seals, path):
    vis = cv2.cvtColor(st["gray"], cv2.COLOR_GRAY2BGR)
    geometry = st["geometry"]
    for x in geometry["rules"]:
        cv2.line(vis, (x, geometry["top"]), (x, geometry["bottom"]), (0, 160, 0), 1)
    for seal in seals:
        x0, y0, x1, y1 = seal["box"]
        colour = (0, 0, 255) if seal["kind"] == "headword" else (255, 0, 0)
        cv2.rectangle(vis, (x0 - 2, y0 - 2), (x1 + 2, y1 + 2), colour, 2)
        cv2.putText(vis, f"{seal['order']} {seal['score']:.2f}", (x0 - 2, y0 - 5), cv2.FONT_HERSHEY_PLAIN, 0.9, colour, 1)
    cv2.imwrite(str(path), vis[max(0, geometry["top"] - 40):geometry["bottom"] + 40])


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--edition", default="ccz")
    parser.add_argument("--juan", help="only these 卷 (as named in the manifest, comma-separated)")
    parser.add_argument("--pages", help="only these PDF page numbers, e.g. 25,47,50")
    parser.add_argument("--overlay", action="store_true", help="write review overlays next to the JSON")
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--no-feedback", action="store_true",
                        help="ignore build/alignment/<edition>-verified.json from align_sequence.py")
    args = parser.parse_args(argv)

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    only_pages = parse_page_arg(args.pages)
    halves = {}  # (slug, page, side) -> state, in reading order
    gutters = {}
    for entry, block in manifest_blocks(manifest, args.edition, args.juan):
        if only_pages is not None and not any(
                p in only_pages for p in range(block["first"], block["last"] + 1)):
            continue  # this 卷 block holds none of the requested pages
        slug = slugify(entry["commons_title"])
        width = entry.get("render_width") or manifest.get("default_render_width")
        for page, side in block_halves(entry, block):
            if only_pages is not None and page not in only_pages:
                continue
            path = CACHE / args.edition / slug / f"p{page:04d}-w{width}.jpg"
            if not path.exists():
                if only_pages is not None:
                    # --pages is a flat page list across volumes; a page that is
                    # not cached for this entry was never fetched for it.
                    print(f"skip {path.relative_to(ROOT)}: not fetched", file=sys.stderr)
                    continue
                print(f"missing {path.relative_to(ROOT)}; run fetch_pages.py", file=sys.stderr)
                return 1
            gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if (slug, page) not in gutters:
                gutters[(slug, page)] = int(find_gutter(binarize(gray)))
            gutter = gutters[(slug, page)]
            crop = [gutter, gray.shape[1]] if side == "right" else [0, gutter]
            try:
                geometry, cols, half, bw = segment_half(gray[:, crop[0]:crop[1]], entry.get("columns") or 10, side)
            except ValueError as exc:
                print(f"p{page:04d} {side}: {exc}", file=sys.stderr)
                return 1
            geometry["crop_x"] = crop
            halves[(slug, page, side)] = {"entry": entry, "juan": block["juan"], "width": width, "gray": half,
                                          "bw": bw, "geometry": geometry, "cols": cols}
    if not halves:
        print("no pages selected", file=sys.stderr)
        return 1
    # A wrong frame line (watermark edge, stain) halves the slot size and ruins
    # the whole half-leaf without any other symptom, so check it outright.
    heights = {key: st["geometry"]["bottom"] - st["geometry"]["top"] for key, st in halves.items()}
    typical = float(np.median(list(heights.values())))
    odd = [f"p{page:04d} {side} ({height} px)" for (_, page, side), height in heights.items()
           if abs(height - typical) > typical * 0.08]  # some spreads were photographed about 5% smaller
    if odd:
        print(f"frame height differs from the typical {typical:.0f} px: {', '.join(odd)}", file=sys.stderr)
        return 1

    verified = {}
    verified_path = BUILD / "alignment" / f"{args.edition}-verified.json"
    if verified_path.exists() and not args.no_feedback:
        for item in json.loads(verified_path.read_text(encoding="utf-8")):
            verified[(item["slug"], item["page"], item["side"], item["column"])] = item["seals"]
    X, y = training_set(halves, verified)
    print(f"training on {int(y.sum())} headwords and {int((1 - y).sum())} regular-script windows")
    model = SealClassifier().fit_clean(X, y)
    print(f"dropped {model.dropped} doubtful headword labels")
    print(f"HOG-only cross-validated accuracy {model.cv_accuracy:.3f}")
    model_dir = BUILD / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / f"seal_classifier_{args.edition}.pkl").write_bytes(pickle.dumps(model))

    # Boxes are in the coordinates of the deskewed half-leaf: crop the page
    # image to crop_x, rotate by `rotation` degrees about the centre, then cut.
    records, running, juan_totals = {}, 0, {}
    for (slug, page, side), st in halves.items():
        seals = detect(st, model, args.threshold)
        record = records.setdefault((slug, page), {
            "edition": args.edition, "commons_title": st["entry"]["commons_title"], "page": page,
            "render_width": st["width"], "halves": []})
        record["halves"].append({"side": side, "juan": st["juan"], "geometry": st["geometry"],
                                 "columns": [{k: v for k, v in c.items() if k not in ("head", "side")} for c in st["cols"]],
                                 "seals": seals, "seal_count": len(seals), "near_misses": st["near_misses"]})
        out_dir = BUILD / "pages" / args.edition / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        if args.overlay:
            draw_overlay(st, seals, out_dir / f"p{page:04d}-{side}-overlay.jpg")
        running += len(seals)
        juan_totals[st["juan"]] = juan_totals.get(st["juan"], 0) + len(seals)
        if st.get("rejected_heads"):
            print(f"  p{page:04d} {side}: flush-top columns not taken as headwords: {st['rejected_heads']}")
        print(f"{st['juan']}\tp{page:04d} {side:5s}\trot {st['geometry']['rotation']:+.2f}\tpitch {st['geometry']['pitch']}\t{len(seals):3d}\trunning {running}")
    for (slug, page), record in records.items():
        path = BUILD / "pages" / args.edition / slug / f"p{page:04d}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for juan, n in juan_totals.items():
        print(f"{juan}: {n} seals detected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
