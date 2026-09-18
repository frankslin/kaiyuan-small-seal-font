#!/usr/bin/env python3
"""Align the seals found by segment_pages.py with code points.

The Nth seal of an edition is the code point whose source sequence number is
N, but counting drifts after a single false or missed detection. So the
detected sequence of a 卷 is aligned (Needleman–Wunsch) with the expected
sequence from SealSources.txt, scoring each pair by the similarity of the
scan crop to the edition's glyph in the Unicode code chart (prepared by
chart_reference.py; a check only, never a drawing source). A false detection
or a missed seal then shows up as a gap instead of shifting what follows.

Statuses in data/provenance/glyphs.csv: `aligned` (confident shape match),
`inferred` (paired by position, low similarity: look at it in the proof),
`manual` (a human assigned it), `rejected` (a detection with no counterpart),
`conflict` (count fallback only). Seals nobody detected are listed in
build/alignment/<edition>-missing.json.

Human decisions go in data/corrections.csv with the header
`action,edition,commons_title,page,side,x,y,w,h,codepoint,reason`; boxes are
in deskewed half-leaf coordinates, as printed in the proof. `reject` drops the
detection overlapping the box, `add` inserts a missed seal, `assign` (or `add`
with a code point) pins a box to a code point. (`keep-lines` rows are read by
trace_glyphs.py.)

Without the chart reference the script falls back to counting per 部 between
the tally columns (「文N 重M」); a matching count can hide one false detection
cancelling one miss, so that result is only ever `inferred`.

Usage:
    python3 scripts/align_sequence.py --edition ccz
    python3 scripts/align_sequence.py --edition ccz --juan 卷一上
"""

import argparse
import csv
import json
import sys
from collections import defaultdict

import numpy as np
import yaml

from chart_reference import SIZE, normalise, reference_path
from fetch_pages import MANIFEST, ROOT, slugify
from segment_pages import BUILD, binarize, blank_frame_rows, manifest_blocks
from trace_glyphs import frame_remnants, half_leaf

SEAL_SOURCES = ROOT / "third_party" / "unicode" / "ucd" / "SealSources.txt"
PROVENANCE = ROOT / "data" / "provenance" / "glyphs.csv"
CORRECTIONS = ROOT / "data" / "corrections.csv"
FIELDS = ["codepoint", "edition", "sequence", "commons_title", "page", "side", "rotation", "crop_x0", "crop_x1",
          "x", "y", "w", "h", "render_width", "frame_top", "pitch", "kind", "score", "juan", "radical", "similarity", "status"]
MATCH_FLOOR = 0.5   # similarity below which pairing two shapes costs more than it gains
GAP = -0.05         # two gaps beat a pair whose similarity is under MATCH_FLOOR - 0.1
CONFIDENT = 0.65    # similarity from which a pair is `aligned` rather than `inferred`
CONFIDENT_INLINE = 0.78  # the same for inline detections, which regular script can mimic
REFINE_BELOW = 0.8  # pairs matching worse than this get their crop box re-fitted
RECOVER_FROM = 0.85 # similarity a searched-for missing seal must reach to be added
TALLY_INDENT = 2.5  # slots; 說解 continuation is indented 1, 新附 seals 2


def load_expected(source_property):
    """{radical number: [(sequence, codepoint), ...]} for one edition."""
    props = defaultdict(dict)
    for line in SEAL_SOURCES.read_text(encoding="utf-8").splitlines():
        if line.startswith("U+"):
            cp, key, value = line.split("\t")
            props[int(cp[2:], 16)][key] = value
    by_radical = defaultdict(list)
    for cp, p in props.items():
        if source_property in p:
            radical = int(p["kSEAL_Rad"].split()[0].split(".")[0])
            by_radical[radical].append((int(p[source_property].split("-")[1]), cp))
    for entries in by_radical.values():
        entries.sort()
    return by_radical


def load_corrections(edition):
    """{(commons_title, page, side): [row, ...]} of reject/add corrections.

    data/corrections.csv holds the human fixes to the segmentation, in the
    coordinates of the deskewed half-leaf (as in the page JSON and proofs):
    `reject` drops the detected seal overlapping the box, `add` inserts a seal
    the segmenter missed.
    """
    table = defaultdict(list)
    if CORRECTIONS.exists():
        with CORRECTIONS.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row["edition"] == edition and row["action"] in ("reject", "add", "assign"):
                    row["box"] = [int(row["x"]), int(row["y"]), int(row["x"]) + int(row["w"]), int(row["y"]) + int(row["h"])]
                    table[(row["commons_title"], int(row["page"]), row["side"])].append(row)
    return table


def box_iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(0, w) * max(0, h)
    return inter / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def apply_corrections(half, fixes, used):
    seals = list(half["seals"])
    rules = half["geometry"]["rules"]
    for fix in fixes:
        if fix["action"] == "assign":
            for seal in seals:
                if box_iou(seal["box"], fix["box"]) > 0.5:
                    seal["assign"] = int(fix["codepoint"], 16)
                    used.add(id(fix))
        elif fix["action"] == "reject":
            kept = [s for s in seals if box_iou(s["box"], fix["box"]) <= 0.5]
            if len(kept) < len(seals):
                used.add(id(fix))
            seals = kept
        else:
            centre = (fix["box"][0] + fix["box"][2]) / 2
            for c in range(len(rules) - 1):
                if rules[c] <= centre < rules[c + 1] and all(box_iou(s["box"], fix["box"]) <= 0.5 for s in seals):
                    seals.append({"column": len(rules) - 1 - c, "box": fix["box"], "kind": "manual", "score": 1.0,
                                  **({"assign": int(fix["codepoint"], 16)} if fix.get("codepoint") else {})})
                    used.add(id(fix))
    return seals


def detected_stream(edition, entry, block, corrections, used, halves=None):
    """Seals and tally markers of one 卷 in reading order."""
    slug = slugify(entry["commons_title"])
    stream = []
    for page in range(block["first"], block["last"] + 1):
        path = BUILD / "pages" / edition / slug / f"p{page:04d}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        for half in record["halves"]:
            if half["juan"] != block["juan"]:
                continue
            geometry = half["geometry"]
            if halves is not None:
                halves[(page, half["side"])] = {**half, "render_width": record["render_width"]}
            seals = defaultdict(list)
            fixes = corrections.get((entry["commons_title"], page, half["side"]), [])
            for seal in apply_corrections(half, fixes, used):
                seals[seal["column"]].append(seal)
            for column in half["columns"]:
                found = sorted(seals[column["column"]], key=lambda s: s["box"][1])
                stream.append({"type": "column", "page": page, "side": half["side"], "column": column["column"]})
                for seal in found:
                    stream.append({"type": "seal", "page": page, "side": half["side"], "geometry": geometry,
                                   "render_width": record["render_width"], **seal})
                runs = column["runs"]
                if not found and len(runs) >= 2 and runs[0][0] - geometry["top"] >= TALLY_INDENT * geometry["slot"]:
                    stream.append({"type": "tally", "page": page, "side": half["side"], "column": column["column"]})
    return stream


def match_tallies(tally_counts, expected_counts, total):
    """Pick the tally closing each 部: monotonic, minimising count mismatch.

    tally_counts[t] is the number of seals seen before tally t. Extra tallies
    (新附 subtotals, stray columns) are skipped. Returns one tally index per
    部, or None where no tally is left.
    """
    n, m = len(expected_counts), len(tally_counts)
    INF = float("inf")
    # best[k][t]: cost of closing 部 k with tally t
    best = [[INF] * m for _ in range(n)]
    back = [[None] * m for _ in range(n)]
    for k in range(n):
        for t in range(m):
            if k == 0:
                best[k][t] = abs(tally_counts[t] - expected_counts[0])
                continue
            for u in range(t):
                if best[k - 1][u] == INF:
                    continue
                cost = best[k - 1][u] + abs((tally_counts[t] - tally_counts[u]) - expected_counts[k])
                if cost < best[k][t]:
                    best[k][t], back[k][t] = cost, u
    if n == 0 or m == 0 or min(best[n - 1]) == INF:
        return [None] * n
    # no seals may follow the tally that closes the last 部
    t = min(range(m), key=lambda i: best[n - 1][i] + abs(total - tally_counts[i]))
    chosen = []
    for k in range(n - 1, -1, -1):
        chosen.append(t)
        t = back[k][t]
    return chosen[::-1]


def shape_vectors(edition, entry, seals):
    """Comparison vectors of the detected seals, cut from the cached pages."""
    vectors = []
    masks = {}
    for seal in seals:
        geometry = seal["geometry"]
        key = (seal["page"], seal["side"])
        if key not in masks:
            masks.clear()  # seals come page by page
            half = half_leaf(edition, entry["commons_title"], seal["page"], seal["render_width"],
                             geometry["crop_x"][0], geometry["crop_x"][1], geometry["rotation"])
            masks[key] = binarize(half, strict=True)
        x0, y0, x1, y1 = seal["box"]
        ink = masks[key][y0:y1, x0:x1] > 0
        for fx0, fy0, fx1, fy1 in frame_remnants(ink, y0, geometry["top"], geometry["pitch"]):
            ink[fy0:fy1, fx0:fx1] = False
        if y0 <= geometry["top"] + 3:  # the box touches the frame: a sagging piece of it is inside
            strip = ink.astype(np.uint8)
            blank_frame_rows(strip, reach=20)
            ink = strip > 0
        vector = normalise(ink)
        vectors.append(vector if vector is not None else np.zeros(SIZE[0] * SIZE[1], np.float32))
    return np.array(vectors)


def refine_box(edition, entry, seal, target):
    """Slide and resize a poorly matching box vertically for the best match.

    A seal that touches the small print above it is found by a sliding
    window, which may sit a fraction of a slot off and take in part of the
    neighbour. The chart glyph only picks where to cut; the outline still
    comes from the scan. Returns (box, similarity).
    """
    geometry = seal["geometry"]
    half = half_leaf(edition, entry["commons_title"], seal["page"], seal["render_width"],
                     geometry["crop_x"][0], geometry["crop_x"][1], geometry["rotation"])
    x0, y0, x1, y1 = seal["box"]
    reach = int(geometry["slot"] * 0.6)
    top, bottom = max(0, y0 - reach), min(half.shape[0], y1 + reach)
    mask = binarize(half, strict=True)[top:bottom, x0:x1] > 0
    best = (None, -1.0)
    for a in range(0, mask.shape[0] - 20, 3):
        for b in range(a + int(geometry["slot"] * 1.2), min(mask.shape[0], a + int(geometry["slot"] * 2.3)) + 1, 3):
            window = mask[a:b]
            rows = np.where(window.any(1))[0]
            vector = normalise(window) if len(rows) else None
            if vector is None:
                continue
            similarity = float(shifted_similarity(vector[None], target[None], reach=1)[0, 0])
            if similarity > best[1]:
                cols = np.where(window.any(0))[0]
                best = ([x0 + int(cols[0]), top + a + int(rows[0]), x0 + int(cols[-1]) + 1, top + a + int(rows[-1]) + 1], similarity)
    return best


def best_window(mask, slot, target, step=3):
    """Best matching two-slot-ish window of a column strip: (y0, y1, similarity)."""
    best = (0, 0, -1.0)
    for a in range(0, max(1, mask.shape[0] - int(slot)), step):
        for b in range(a + int(slot * 1.2), min(mask.shape[0], a + int(slot * 2.3)) + 1, step):
            window = mask[a:b]
            rows = np.where(window.any(1))[0]
            # a speck scales up to anything; a seal fills most of its two slots
            if len(rows) == 0 or rows[-1] - rows[0] < slot * 0.9 or window.mean() < 0.05:
                continue
            vector = normalise(window)
            if vector is None:
                continue
            similarity = float(shifted_similarity(vector[None], target[None], reach=1)[0, 0])
            if similarity > best[2]:
                best = (a + int(rows[0]), a + int(rows[-1]) + 1, similarity)
    return best


def search_missing(edition, entry, halves, previous, following, target):
    """Look for a seal nobody detected, between its aligned neighbours.

    Walks the columns from the seal before to the seal after in reading
    order and returns the best matching window as a seal dict, or None.
    """
    order = [(key, column) for key, half in halves.items() for column in half["columns"]]
    position = {(key, column["column"]): n for n, (key, column) in enumerate(order)}
    start = position.get(((previous["page"], previous["side"]), previous["column"]), 0) if previous else 0
    stop = position.get(((following["page"], following["side"]), following["column"]), len(order) - 1) if following else len(order) - 1
    if stop - start > 12:
        return None  # too far apart to search blindly
    best = None
    for key, column in order[start:stop + 1]:
        half = halves[key]
        geometry = half["geometry"]
        image = half_leaf(edition, entry["commons_title"], key[0], half["render_width"],
                          geometry["crop_x"][0], geometry["crop_x"][1], geometry["rotation"])
        y_lo, y_hi = geometry["top"], geometry["bottom"]
        if previous and (key, column["column"]) == ((previous["page"], previous["side"]), previous["column"]):
            y_lo = previous["box"][3]
        if following and (key, column["column"]) == ((following["page"], following["side"]), following["column"]):
            y_hi = following["box"][1]
        if y_hi - y_lo < geometry["slot"]:
            continue
        strip = (binarize(image, strict=True)[geometry["top"]:geometry["bottom"], column["x0"]:column["x1"]] > 0).astype(np.uint8)
        blank_frame_rows(strip)
        mask = strip[y_lo - geometry["top"]:y_hi - geometry["top"]] > 0
        y0, y1, similarity = best_window(mask, geometry["slot"], target)
        if similarity > (best["similarity"] if best else RECOVER_FROM):
            cols = np.where(mask[y0:y1].any(0))[0]
            best = {"type": "seal", "page": key[0], "side": key[1], "geometry": geometry,
                    "render_width": half["render_width"], "column": column["column"], "kind": "recovered",
                    "score": 0.0, "similarity": similarity,
                    "box": [column["x0"] + int(cols[0]), y_lo + y0, column["x0"] + int(cols[-1]) + 1, y_lo + y1]}
    return best


def shifted_similarity(detected, wanted, reach=2):
    """Best correlation over small shifts: thin strokes decorrelate quickly
    when the two crops are framed a pixel or two apart."""
    best = np.full((len(detected), len(wanted)), -1.0, np.float32)
    images = detected.reshape(-1, SIZE[1], SIZE[0])
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            moved = np.zeros_like(images)
            ys = slice(max(0, dy), SIZE[1] + min(0, dy)), slice(max(0, -dy), SIZE[1] + min(0, -dy))
            xs = slice(max(0, dx), SIZE[0] + min(0, dx)), slice(max(0, -dx), SIZE[0] + min(0, -dx))
            moved[:, ys[0], xs[0]] = images[:, ys[1], xs[1]]
            flat = moved.reshape(len(detected), -1)
            flat = flat - flat.mean(1, keepdims=True)
            flat /= np.maximum(np.linalg.norm(flat, axis=1, keepdims=True), 1e-6)
            best = np.maximum(best, flat @ wanted.T)
    return best


def align_shapes(similarity, forced):
    """Needleman–Wunsch over (detected, expected). Returns (pairs, extra, missing).

    A pair scores its similarity minus MATCH_FLOOR, so pairing two unlike
    shapes costs more than leaving a false detection and a missed seal as
    gaps. `forced` maps a detected index to the expected index a human chose.
    """
    n, m = similarity.shape
    gain = similarity - MATCH_FLOOR
    for i, j in forced.items():
        gain[i, :] = -5.0
        gain[:, j] = -5.0
        gain[i, j] = 5.0
    score = np.zeros((n + 1, m + 1))
    score[:, 0] = GAP * np.arange(n + 1)
    score[0, :] = GAP * np.arange(m + 1)
    move = np.zeros((n + 1, m + 1), np.int8)
    for i in range(1, n + 1):
        diagonal = score[i - 1, :-1] + gain[i - 1]
        up = score[i - 1, 1:] + GAP
        for j in range(1, m + 1):
            options = (diagonal[j - 1], up[j - 1], score[i, j - 1] + GAP)
            k = int(np.argmax(options))
            score[i, j], move[i, j] = options[k], k
    i, j, pairs, extra, missing = n, m, [], [], []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and move[i, j] == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and (j == 0 or move[i, j] == 1):
            extra.append(i - 1)
            i -= 1
        else:
            missing.append(j - 1)
            j -= 1
    return pairs[::-1], extra[::-1], missing[::-1]


def provenance_row(edition, entry, block, seal, status, prefix="", sequence=None, cp=None, radical="", similarity=""):
    x0, y0, x1, y1 = seal["box"]
    return {"codepoint": f"{cp:05X}" if cp else "", "edition": edition,
            "sequence": f"{prefix}{sequence:05d}" if sequence else "",
            "commons_title": entry["commons_title"], "page": seal["page"], "side": seal["side"],
            "rotation": seal["geometry"]["rotation"], "crop_x0": seal["geometry"]["crop_x"][0],
            "crop_x1": seal["geometry"]["crop_x"][1], "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
            "render_width": seal["render_width"], "frame_top": seal["geometry"]["top"],
            "pitch": seal["geometry"]["pitch"], "kind": seal["kind"], "score": seal["score"],
            "juan": block["juan"], "radical": radical, "similarity": similarity, "status": status}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--edition", default="ccz")
    parser.add_argument("--juan", help="only these 卷 (comma-separated)")
    args = parser.parse_args(argv)

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    edition = manifest["editions"][args.edition]
    prefix = edition["sequence_prefix"]
    expected = load_expected(edition["source_property"])
    reference = None
    if reference_path(args.edition).exists():
        data = np.load(reference_path(args.edition))
        reference = dict(zip(data["sequences"].tolist(), data["vectors"]))
    else:
        print("no chart reference (run chart_reference.py); falling back to per-部 counts", file=sys.stderr)

    rows, report, done_juan, verified, review = [], [], set(), [], []
    corrections, used = load_corrections(args.edition), set()
    for entry, block in manifest_blocks(manifest, args.edition, args.juan):
        halves = {}
        stream = detected_stream(args.edition, entry, block, corrections, used, halves)
        if not stream:
            print(f"{block['juan']}: no segmentation output yet, skipped", file=sys.stderr)
            continue
        done_juan.add(block["juan"])
        radicals = list(range(block["radicals"][0], block["radicals"][1] + 1))
        seals = [item for item in stream if item["type"] == "seal"]
        wanted = [(sequence, cp, r) for r in radicals for sequence, cp in expected[r]]
        report.append(f"{block['juan']}: {len(seals)} seals detected, {len(wanted)} expected")

        if reference is not None:
            blank = np.zeros(SIZE[0] * SIZE[1], np.float32)
            similarity = shifted_similarity(shape_vectors(args.edition, entry, seals),
                                            np.array([reference.get(w[0], blank) for w in wanted]))
            index_of = {cp: j for j, (_, cp, _) in enumerate(wanted)}
            forced = {i: index_of[seal["assign"]] for i, seal in enumerate(seals) if seal.get("assign") in index_of}
            pairs, extra, missing = align_shapes(similarity.copy(), forced)
            counts = defaultdict(int)
            paired = {j: i for i, j in pairs}
            for i, j in pairs:
                sequence, cp, radical = wanted[j]
                sim = float(similarity[i, j])
                if sim < REFINE_BELOW and sequence in reference and seals[i]["kind"] != "manual":
                    box, better = refine_box(args.edition, entry, seals[i], reference[sequence])
                    if box is not None and better > sim + 0.05:
                        seals[i] = {**seals[i], "box": box}
                        sim = better
                        counts["refined"] += 1
                # a headword is backed by the layout; an inline detection is not
                confident = CONFIDENT if seals[i]["kind"] == "headword" else CONFIDENT_INLINE
                if sim < confident and i not in forced and sequence in reference:
                    # probably paired with a false detection: look for the real seal nearby
                    previous = max((k for k in paired if k < j), default=None)
                    later = min((k for k in paired if k > j), default=None)
                    found = search_missing(args.edition, entry, halves,
                                           seals[paired[previous]] if previous is not None else None,
                                           seals[paired[later]] if later is not None else None, reference[sequence])
                    if found and found["similarity"] > sim:
                        rows.append(provenance_row(args.edition, entry, block, seals[i], "rejected"))
                        seals[i], sim, confident = found, found["similarity"], RECOVER_FROM
                        counts["replaced"] += 1
                status = "manual" if i in forced else "aligned" if sim >= confident else "inferred"
                counts[status] += 1
                rows.append(provenance_row(args.edition, entry, block, seals[i], status, prefix, sequence, cp, radical, round(sim, 3)))
            # A headword sandwiched between confidently aligned neighbours is
            # vouched for by its position; dense glyphs often match only so-so.
            mine = [r for r in rows if r["juan"] == block["juan"] and r["sequence"]]
            mine.sort(key=lambda r: r["sequence"])
            for before_row, row, after_row in zip(mine, mine[1:], mine[2:]):
                if (row["status"] == "inferred" and row["kind"] == "headword" and float(row["similarity"]) >= 0.5
                        and before_row["status"] in ("aligned", "manual") and after_row["status"] in ("aligned", "manual")
                        and before_row.get("promoted") is None and after_row.get("promoted") is None):
                    row["status"], row["promoted"] = "aligned", True
                    counts["promoted"] += 1
            for row in mine:
                row.pop("promoted", None)
            counts["inferred"] -= counts["promoted"]
            counts["aligned"] += counts["promoted"]
            for i in extra:
                rows.append(provenance_row(args.edition, entry, block, seals[i], "rejected"))
            before = {j: i for i, j in pairs}
            for j in missing:
                sequence, cp, radical = wanted[j]
                previous = max((k for k in before if k < j), default=None)
                later = min((k for k in before if k > j), default=None)
                near = seals[before[previous]] if previous is not None else None
                if sequence in reference:
                    found = search_missing(args.edition, entry, halves, near,
                                           seals[before[later]] if later is not None else None, reference[sequence])
                    if found:
                        counts["recovered"] += 1
                        rows.append(provenance_row(args.edition, entry, block, found, "aligned", prefix, sequence, cp,
                                                   radical, round(found["similarity"], 3)))
                        continue
                review.append({"juan": block["juan"], "codepoint": f"{cp:05X}", "sequence": f"{prefix}{sequence:05d}",
                               "after": {"page": near["page"], "side": near["side"], "box": near["box"],
                                         "codepoint": f"{wanted[previous][1]:05X}"} if near else None})
            report.append(f"  shape alignment: {counts['aligned']} aligned, {counts['inferred']} inferred (low similarity, "
                          f"check in proof), {counts['manual']} manual, {counts['refined']} boxes re-fitted, {len(extra)} detections rejected, "
                          f"{counts['replaced']} false detections replaced and {counts['recovered']} missed seals recovered by search, {len(missing) - counts['recovered']} still missing")
            for item in review:
                if item["juan"] == block["juan"]:
                    after = item["after"]
                    where = f"after U+{after['codepoint']} on p{after['page']} {after['side']}" if after else "at the start"
                    report.append(f"    missing U+{item['codepoint']} {item['sequence']}  ({where})")
            continue

        # fallback without the chart: anchor the count at the tally column closing each 部
        expected_counts = [len(expected[r]) for r in radicals]
        tally_counts, count = [], 0
        for item in stream:
            if item["type"] == "seal":
                count += 1
            elif item["type"] == "tally":
                tally_counts.append(count)
        closing = match_tallies(tally_counts, expected_counts, len(seals))
        start = 0
        for radical, want, t in zip(radicals, expected_counts, closing):
            end = tally_counts[t] if t is not None else len(seals)
            got = seals[start:end]
            ok = len(got) == want
            report.append(f"  部 {radical:3d}  expected {want:4d}  detected {len(got):4d}  {'ok' if ok else 'CONFLICT'}")
            for k, seal in enumerate(got):
                sequence, cp = expected[radical][k] if ok else (None, None)
                rows.append(provenance_row(args.edition, entry, block, seal, "inferred" if ok else "conflict",
                                           prefix, sequence, cp, radical))
            start = end

    for fixes in corrections.values():
        for fix in fixes:
            if id(fix) not in used:
                report.append(f"  correction had no effect: {fix['action']} p{fix['page']} {fix['side']} {fix['box']}")
    kept = []
    if PROVENANCE.exists():
        with PROVENANCE.open(encoding="utf-8", newline="") as fh:
            kept = [r for r in csv.DictReader(fh) if not (r["edition"] == args.edition and r["juan"] in done_juan)]
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    with PROVENANCE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n", restval="")
        writer.writeheader()
        writer.writerows(kept + rows)
    out = BUILD / "alignment"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.edition}-missing.json").write_text(json.dumps(review, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / f"{args.edition}-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
