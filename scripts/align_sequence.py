#!/usr/bin/env python3
"""Align the seals found by segment_pages.py with code points.

The Nth seal of an edition is the code point whose source sequence number is
N. Counting alone drifts after a single miss, so the count is re-anchored at
every 部: each 部 ends with a tally column (「文N 重M」, set several slots
down, without a seal), and SealSources.txt says how many seals each 部 holds
in the edition. A 部 whose detected count equals the expected count is
`aligned`; otherwise every seal in it is a `conflict` and the 部 goes to the
review list.

陳昌治本 has no regular-script headword under the seal (the 說解 follows
directly), so there is nothing to OCR against kSEAL_MCJK in this edition.

Output: data/provenance/glyphs.csv (rows of the processed 卷 are replaced),
build/alignment/<edition>-report.txt, and build/alignment/<edition>-verified.json:
the columns of every 部 whose count matched, which segment_pages.py uses as
extra training labels on its next run.

Usage:
    python3 scripts/align_sequence.py --edition ccz
    python3 scripts/align_sequence.py --edition ccz --juan 卷一上
"""

import argparse
import csv
import json
import sys
from collections import defaultdict

import yaml

from fetch_pages import MANIFEST, ROOT, slugify
from segment_pages import BUILD, manifest_blocks

SEAL_SOURCES = ROOT / "third_party" / "unicode" / "ucd" / "SealSources.txt"
PROVENANCE = ROOT / "data" / "provenance" / "glyphs.csv"
FIELDS = ["codepoint", "edition", "sequence", "commons_title", "page", "side", "rotation", "crop_x0", "crop_x1",
          "x", "y", "w", "h", "render_width", "kind", "score", "juan", "radical", "status"]
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


def detected_stream(edition, entry, block):
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
            seals = defaultdict(list)
            for seal in half["seals"]:
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


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--edition", default="ccz")
    parser.add_argument("--juan")
    args = parser.parse_args(argv)

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    edition = manifest["editions"][args.edition]
    prefix = edition["sequence_prefix"]
    expected = load_expected(edition["source_property"])

    rows, report, done_juan, verified = [], [], set(), []
    for entry, block in manifest_blocks(manifest, args.edition, args.juan):
        stream = detected_stream(args.edition, entry, block)
        if not stream:
            print(f"{block['juan']}: no segmentation output yet, skipped", file=sys.stderr)
            continue
        done_juan.add(block["juan"])
        radicals = list(range(block["radicals"][0], block["radicals"][1] + 1))
        expected_counts = [len(expected[r]) for r in radicals]
        seals = [item for item in stream if item["type"] == "seal"]
        tally_counts, count = [], 0
        for item in stream:
            if item["type"] == "seal":
                count += 1
            elif item["type"] == "tally":
                tally_counts.append(count)
        closing = match_tallies(tally_counts, expected_counts, len(seals))
        report.append(f"{block['juan']}: {len(seals)} seals detected, {sum(expected_counts)} expected, "
                      f"{len(tally_counts)} tally columns for {len(radicals)} 部")
        # position in the stream of every seal and tally, to cut out verified columns
        seal_at = [i for i, item in enumerate(stream) if item["type"] == "seal"]
        tally_at = [i for i, item in enumerate(stream) if item["type"] == "tally"]
        start = 0
        for radical, want, t in zip(radicals, expected_counts, closing):
            end = tally_counts[t] if t is not None else len(seals)
            if radical == radicals[-1]:
                end = max(end, len(seals)) if t is None else end
            got = seals[start:end]
            ok = len(got) == want
            where = f"p{got[0]['page']:04d} {got[0]['side']} – p{got[-1]['page']:04d} {got[-1]['side']}" if got else "-"
            first_cp = expected[radical][0][1]
            report.append(f"  部 {radical:3d} U+{first_cp:05X}  expected {want:4d}  detected {len(got):4d}  "
                          f"{'ok' if ok else 'CONFLICT %+d' % (len(got) - want)}  {where}")
            if ok and got and t is not None:
                # the 部 runs from the column of its first seal to its tally column
                for item in stream[seal_at[start] - 1:tally_at[t] + 1]:
                    if item["type"] == "column":
                        verified.append({"slug": slugify(entry["commons_title"]), "page": item["page"],
                                         "side": item["side"], "column": item["column"], "seals": []})
                    elif item["type"] == "seal":
                        verified[-1]["seals"].append([item["box"][1], item["box"][3]])
            for k, seal in enumerate(got):
                sequence, cp = expected[radical][k] if ok else (None, None)
                x0, y0, x1, y1 = seal["box"]
                rows.append({
                    "codepoint": f"{cp:05X}" if ok else "", "edition": args.edition,
                    "sequence": f"{prefix}{sequence:05d}" if ok else "",
                    "commons_title": entry["commons_title"], "page": seal["page"], "side": seal["side"],
                    "rotation": seal["geometry"]["rotation"], "crop_x0": seal["geometry"]["crop_x"][0], "crop_x1": seal["geometry"]["crop_x"][1],
                    "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "render_width": seal["render_width"],
                    "kind": seal["kind"], "score": seal["score"], "juan": block["juan"], "radical": radical,
                    "status": "aligned" if ok else "conflict"})
            start = end
        if start < len(seals):
            report.append(f"  {len(seals) - start} seals after the last matched tally left unassigned")

    kept = []
    if PROVENANCE.exists():
        with PROVENANCE.open(encoding="utf-8", newline="") as fh:
            kept = [r for r in csv.DictReader(fh) if not (r["edition"] == args.edition and r["juan"] in done_juan)]
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    with PROVENANCE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(kept + rows)
    out = BUILD / "alignment"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.edition}-verified.json").write_text(json.dumps(verified, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / f"{args.edition}-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    aligned = sum(1 for r in rows if r["status"] == "aligned")
    print(f"{aligned} aligned, {len(rows) - aligned} in conflict")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
