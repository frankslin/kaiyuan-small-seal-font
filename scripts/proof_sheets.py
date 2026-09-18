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
import json
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
.cell.inferred {{ border: 2px solid #e08a00; background: #fff6e5; }}
.cell.manual {{ border: 2px solid #2a7; }}
.pair {{ display: flex; align-items: center; justify-content: space-between; height: 110px; }}
.seal {{ font-family: "KSS"; font-size: 84px; line-height: 1; }}
.pair img {{ max-height: 104px; max-width: 84px; }}
.modern {{ font-size: 22px; margin-right: 6px; }}
</style>
<h1>Kaiyuan Small Seal 校樣</h1>
<p>每格：字型渲染｜原掃描切片；下方為對應楷書、碼位、流水號與出處，以及切片框 <code>x,y,w,h</code>（去斜後半葉座標，可直接填入 <code>data/corrections.csv</code>）。橘框（inferred）為形狀相似度偏低、請人工確認的字；頁尾列出沒切到的字、被剔除的偵測框，以及差一點過門檻的候選。</p>
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
    sections, rejected = defaultdict(list), defaultdict(list)
    for i, row in enumerate(rows):
        if row["codepoint"]:
            sections[(row["juan"], int(row["radical"]))].append((i, row))
        else:
            rejected[row["juan"]].append((i, row))
    body = []
    for (juan, radical), items in sections.items():
        doubtful = sum(1 for _, row in items if row["status"] != "aligned")
        body.append(f"<h2>{html.escape(juan)} 第 {radical} 部（{len(items)} 字，其中 {doubtful} 字待看）</h2><div class='grid'>")
        for i, row in items:
            cp = row["codepoint"]
            where = (f"p{row['page']} {row['side']} · {row['status']} {row['similarity']}<br>"
                     f"<code>{row['x']},{row['y']},{row['w']},{row['h']}</code>")
            src = crop_png(row, f"u{cp}")
            body.append(f"<div class='cell {row['status']}'><div class='pair'><span class='seal'>&#x{cp};</span>"
                        f"<img src='{src}' alt=''></div><span class='modern'>{html.escape(modern.get(cp, ''))}</span>"
                        f"U+{cp} {row['sequence']}<br>{where}</div>")
        body.append("</div>")
    missing_path = ROOT / "build" / "alignment" / f"{rows[0]['edition']}-missing.json" if rows else None
    if missing_path and missing_path.exists():
        missing = json.loads(missing_path.read_text(encoding="utf-8"))
        body.append(f"<h2>沒切到的字（{len(missing)}）</h2><p>到 <code>build/pages/…/pNNNN-side-overlay.jpg</code> 找到該字，"
                    "在 <code>data/corrections.csv</code> 加一行 <code>add</code>（可附碼位）。</p><div class='grid'>")
        for item in missing:
            after = item["after"]
            where = f"在 U+{after['codepoint']}（p{after['page']} {after['side']}）之後" if after else "在卷首"
            body.append(f"<div class='cell conflict' style='width:150px'><span class='modern'>"
                        f"{html.escape(modern.get(item['codepoint'], ''))}</span>U+{item['codepoint']} {item['sequence']}<br>"
                        f"{html.escape(item['juan'])} {where}</div>")
        body.append("</div>")
    for juan, items in rejected.items():
        body.append(f"<h2>{html.escape(juan)}：無對應字形、已剔除的偵測框（{len(items)}）</h2><div class='grid'>")
        for i, row in items:
            src = crop_png(row, f"rejected-{i:05d}")
            body.append(f"<div class='cell conflict'><img src='{src}' alt='' style='max-width:80px'><br>"
                        f"p{row['page']} {row['side']} · {row['kind']} {row['score']}<br>"
                        f"<code>{row['x']},{row['y']},{row['w']},{row['h']}</code></div>")
        body.append("</div>")
    # candidates that scored just under the threshold: where missed seals hide
    body.append("<h2>差一點過門檻的候選（漏判多半在這裡）</h2><div class='grid'>")
    near_count = 0
    for path in sorted((ROOT / "build" / "pages").glob("*/*/p*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for half in record["halves"]:
            for k, near in enumerate(half.get("near_misses", [])):
                x0, y0, x1, y1 = near["box"]
                row = {"edition": record["edition"], "commons_title": record["commons_title"], "page": record["page"],
                       "render_width": record["render_width"], "crop_x0": half["geometry"]["crop_x"][0],
                       "crop_x1": half["geometry"]["crop_x"][1], "rotation": half["geometry"]["rotation"],
                       "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
                src = crop_png(row, f"near-p{record['page']:04d}-{half['side']}-{k:02d}")
                body.append(f"<div class='cell conflict'><img src='{src}' alt='' style='max-width:80px'><br>"
                            f"p{record['page']} {half['side']} · {near['score']}<br>"
                            f"<code>{x0},{y0},{x1 - x0},{y1 - y0}</code></div>")
                near_count += 1
    body.append("</div>")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(PAGE.format(body="\n".join(body)), encoding="utf-8")
    print(f"wrote {(OUT / 'index.html').relative_to(ROOT)} ({len(rows)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
