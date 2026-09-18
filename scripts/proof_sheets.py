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
code.line {{ font-size: 9px; word-break: break-all; user-select: all; }}
summary {{ font-size: 18px; margin: 18px 0 8px; cursor: pointer; }}
h3 {{ margin: 14px 0 6px; font-weight: normal; }}
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
    sections, rejected = defaultdict(list), []
    for i, row in enumerate(rows):
        if row["codepoint"]:
            sections[(row["juan"], int(row["radical"]))].append((i, row))
        else:
            rejected.append((i, row))

    def cell(i, row):
        cp = row["codepoint"]
        where = (f"p{row['page']} {row['side']} · {row['status']} {row['similarity']}<br>"
                 f"<code>{row['x']},{row['y']},{row['w']},{row['h']}</code>")
        src = crop_png(row, f"u{cp}")
        return (f"<div class='cell {row['status']}'><div class='pair'><span class='seal'>&#x{cp};</span>"
                f"<img src='{src}' alt=''></div><span class='modern'>{html.escape(modern.get(cp, ''))}</span>"
                f"U+{cp} {row['sequence']}<br>{where}</div>")

    # every box the pipeline saw but did not use, as candidates for missing seals
    spare = defaultdict(list)
    for i, row in rejected:
        spare[(int(row["page"]), row["side"])].append((dict(row), f"rejected-{i:05d}", f"{row['kind']} {row['score']}"))
    for path in sorted((ROOT / "build" / "pages").glob("*/*/p*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for half in record["halves"]:
            for k, near in enumerate(half.get("near_misses", [])):
                x0, y0, x1, y1 = near["box"]
                row = {"edition": record["edition"], "commons_title": record["commons_title"], "page": record["page"],
                       "side": half["side"], "render_width": record["render_width"],
                       "crop_x0": half["geometry"]["crop_x"][0], "crop_x1": half["geometry"]["crop_x"][1],
                       "rotation": half["geometry"]["rotation"], "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
                spare[(record["page"], half["side"])].append(
                    (row, f"near-p{record['page']:04d}-{half['side']}-{k:02d}", f"候選 {near['score']}"))

    def spare_cell(row, name, note, codepoint=""):
        line = (f"add,{row['edition']},\"{row['commons_title']}\",{row['page']},{row['side']},"
                f"{row['x']},{row['y']},{row['w']},{row['h']},{codepoint},")
        return (f"<div class='cell conflict'><img src='{crop_png(row, name)}' alt='' style='max-width:80px'><br>"
                f"p{row['page']} {row['side']} · {html.escape(note)}<br><code class='line'>{html.escape(line)}</code></div>")

    body = []
    doubtful = [(i, row) for items in sections.values() for i, row in items if row["status"] == "inferred"]
    body.append(f"<h2>待確認（{len(doubtful)}）</h2><p>位置配上了但形狀相似度偏低，未進字型。切片正確就在 "
                "<code>data/corrections.csv</code> 加一行 <code>assign</code>；不正確就用下方候選的 <code>add</code> 行。</p><div class='grid'>")
    body.extend(cell(i, row) for i, row in doubtful)
    body.append("</div>")
    missing_path = ROOT / "build" / "alignment" / f"{rows[0]['edition']}-missing.json" if rows else None
    missing = json.loads(missing_path.read_text(encoding="utf-8")) if missing_path and missing_path.exists() else []
    body.append(f"<h2>沒找到的字（{len(missing)}）</h2><p>每個字後面列出同一半葉上沒被採用的框；對的那個，把它下方的整行貼進 "
                "<code>data/corrections.csv</code>。都不對就到 <code>build/pages/…-overlay.jpg</code> 量座標。</p>")
    for item in missing:
        after = item["after"]
        where = f"在 U+{after['codepoint']}（p{after['page']} {after['side']}）之後" if after else "在卷首"
        body.append(f"<h3><span class='modern'>{html.escape(modern.get(item['codepoint'], ''))}</span> U+{item['codepoint']} "
                    f"{item['sequence']} · {html.escape(item['juan'])} {where}</h3><div class='grid'>")
        if after:
            for row, name, note in spare.get((after["page"], after["side"]), [])[:8]:
                body.append(spare_cell(row, name, note, item["codepoint"]))
        body.append("</div>")

    body.append("<details open><summary>全部已對位的字（依部）</summary>")
    for (juan, radical), items in sections.items():
        body.append(f"<h2>{html.escape(juan)} 第 {radical} 部（{len(items)} 字）</h2><div class='grid'>")
        body.extend(cell(i, row) for i, row in items if row["status"] != "inferred")
        body.append("</div>")
    body.append("</details>")
    body.append(f"<details><summary>被剔除的偵測框與低分候選（{sum(len(v) for v in spare.values())}，多為雜訊）</summary><div class='grid'>")
    for key in sorted(spare):
        body.extend(spare_cell(row, name, note) for row, name, note in spare[key])
    body.append("</div></details>")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(PAGE.format(body="\n".join(body)), encoding="utf-8")
    print(f"wrote {(OUT / 'index.html').relative_to(ROOT)} ({len(rows)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
