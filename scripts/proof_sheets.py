#!/usr/bin/env python3
"""Write an HTML proof: built glyph, source crop and modern character side by side.

Crops are cut from the local page cache into build/proof/crops/ and are not
committed. Conflicts (部 whose seal count did not match) are listed with their
crops but without glyphs, as the review queue.

Usage:
    python3 scripts/proof_sheets.py
"""

import csv
import html
import sys
from collections import defaultdict

import cv2

from fetch_pages import ROOT
from trace_glyphs import MARGIN, PROVENANCE, half_leaf

SEAL_SOURCES = ROOT / "third_party" / "unicode" / "ucd" / "SealSources.txt"
OUT = ROOT / "build" / "proof"

PAGE = """<!doctype html>
<html lang="zh-Hant"><meta charset="utf-8"><title>Kaiyuan Small Seal 校樣</title>
<style>
@font-face {{ font-family: "KSS"; src: url("../KaiyuanSmallSeal-Regular.ttf"); }}
body {{ font-family: system-ui, sans-serif; margin: 16px; background: #fafaf7; color: #222; }}
h2 {{ border-bottom: 1px solid #bbb; padding-bottom: 4px; }}
.grid {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.cell {{ border: 1px solid #ccc; background: #fff; padding: 6px; width: 190px; font-size: 12px; }}
.cell.conflict {{ border-color: #c33; width: 90px; }}
.pair {{ display: flex; align-items: center; justify-content: space-between; height: 110px; }}
.seal {{ font-family: "KSS"; font-size: 84px; line-height: 1; }}
.pair img {{ max-height: 104px; max-width: 84px; }}
.modern {{ font-size: 22px; margin-right: 6px; }}
</style>
<h1>Kaiyuan Small Seal 校樣</h1>
<p>每格：字型渲染｜原掃描切片；下方為對應楷書、碼位、流水號與出處。紅框為計數不符、待審的部。</p>
{body}
</html>
"""


def crop_png(row, name):
    half = half_leaf(row["edition"], row["commons_title"], int(row["page"]), int(row["render_width"]),
                     int(row["crop_x0"]), int(row["crop_x1"]), float(row["rotation"]))
    x, y, w, h = (int(row[k]) for k in "xywh")
    crop = half[max(0, y - MARGIN):y + h + MARGIN, max(0, x - MARGIN):x + w + MARGIN]
    (OUT / "crops").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / "crops" / f"{name}.png"), crop)
    return f"crops/{name}.png"


def main(argv):
    modern = {}
    for line in SEAL_SOURCES.read_text(encoding="utf-8").splitlines():
        if line.startswith("U+") and "\tkSEAL_MCJK\t" in line:
            cp, _, value = line.split("\t")
            modern[cp[2:]] = "".join(chr(int(v, 16)) for v in value.split())
    with PROVENANCE.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    sections = defaultdict(list)
    for i, row in enumerate(rows):
        sections[(row["juan"], int(row["radical"]))].append((i, row))
    body = []
    for (juan, radical), items in sections.items():
        status = items[0][1]["status"]
        body.append(f"<h2>{html.escape(juan)} 第 {radical} 部（{len(items)} 字，{status}）</h2><div class='grid'>")
        for i, row in items:
            where = f"p{row['page']} {row['side']} · {row['kind']} {row['score']}"
            if row["status"] == "aligned":
                cp = row["codepoint"]
                src = crop_png(row, f"u{cp}")
                body.append(f"<div class='cell'><div class='pair'><span class='seal'>&#x{cp};</span>"
                            f"<img src='{src}' alt=''></div><span class='modern'>{html.escape(modern.get(cp, ''))}</span>"
                            f"U+{cp} {row['sequence']}<br>{where}</div>")
            else:
                src = crop_png(row, f"conflict-{i:05d}")
                body.append(f"<div class='cell conflict'><img src='{src}' alt='' style='max-width:80px'><br>{where}</div>")
        body.append("</div>")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(PAGE.format(body="\n".join(body)), encoding="utf-8")
    print(f"wrote {(OUT / 'index.html').relative_to(ROOT)} ({len(rows)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
