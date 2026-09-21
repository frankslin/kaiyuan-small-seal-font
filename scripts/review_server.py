#!/usr/bin/env python3
"""Local web page for reviewing the aligned glyphs.

    python3 scripts/review_server.py        # then open http://127.0.0.1:8765/

Approve glyphs in batches: their provenance rows are copied verbatim into
data/approved.csv, and from then on align_sequence.py treats them as fixed
points and never changes them. For the rest, leave feedback per glyph: delete
(this crop is not that seal), adjust the box, make it another character, or a
free-text note. Structured feedback becomes a line of data/corrections.csv;
everything is also logged to data/review_feedback.csv. Seals nobody found can
be boxed by hand on the page image. "Rebuild" reruns align, trace, build and
proof. The server binds to localhost only and serves page crops from the
local cache; nothing scanned is written into the repository.
"""

import csv
import datetime
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2

from align_sequence import APPROVED, CORRECTIONS, FIELDS, PROVENANCE, SEAL_SOURCES
from fetch_pages import ROOT
from trace_glyphs import GLYPHS, half_leaf

FEEDBACK = ROOT / "data" / "review_feedback.csv"
FEEDBACK_FIELDS = ["time", "codepoint", "action", "page", "side", "x", "y", "w", "h", "new_codepoint", "note"]
CORRECTION_FIELDS = ["action", "edition", "commons_title", "page", "side", "x", "y", "w", "h", "codepoint", "reason"]
PAGE = Path(__file__).with_name("review_page.html")
PORT = 8765
lock = threading.Lock()
rebuild = {"running": False, "log": ""}
dirty = set()  # 卷 with feedback since their last re-alignment


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def append_csv(path, fields, rows):
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n", restval="", extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerows(rows)


def modern_characters():
    table = {}
    for line in SEAL_SOURCES.read_text(encoding="utf-8").splitlines():
        if line.startswith("U+") and "\tkSEAL_MCJK\t" in line:
            cp, _, value = line.split("\t")
            table[cp[2:]] = "".join(chr(int(v, 16)) for v in value.split())
    return table


MODERN = modern_characters()


def is_common(text):
    """Whether any of the modern characters is in everyday use: Big5 level 1
    (常用字, A440–C67E) or GB2312 level 1 (B0A1–D7F9). No external data needed."""
    for ch in text:
        for codec, low, high in (("big5", 0xA4, 0xC6), ("gb2312", 0xB0, 0xD7)):
            try:
                encoded = ch.encode(codec)
            except UnicodeEncodeError:
                continue
            if len(encoded) == 2 and low <= encoded[0] <= high:
                return True
    return False


def half_index():
    """{(Commons title, page, side): half-leaf parameters} from the segmenter's page JSON.

    Page numbers restart in every Commons file, so the title is part of the key."""
    index = {}
    for path in sorted((ROOT / "build" / "pages").glob("*/*/p*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for half in record["halves"]:
            geometry = half["geometry"]
            index[(record["commons_title"], record["page"], half["side"])] = {
                "edition": record["edition"], "commons_title": record["commons_title"],
                "render_width": record["render_width"], "crop_x0": geometry["crop_x"][0],
                "crop_x1": geometry["crop_x"][1], "rotation": geometry["rotation"],
                "top": geometry["top"], "bottom": geometry["bottom"], "juan": half["juan"],
                "spare": [s["box"] for s in half.get("near_misses", [])]}
    return index


def state():
    rows = read_csv(PROVENANCE)
    missing_path = ROOT / "build" / "alignment" / "ccz-missing.json"
    missing = json.loads(missing_path.read_text(encoding="utf-8")) if missing_path.exists() else []
    halves = half_index()
    order = list(halves)
    glyphs = []
    locked = {r["codepoint"] for r in read_csv(APPROVED)}
    for row in rows:
        if not row["codepoint"]:
            continue
        if row["codepoint"] in locked:
            row["status"] = "approved"  # effective at once; the rebuild only confirms it
        glyphs.append({k: row[k] for k in ("codepoint", "sequence", "commons_title", "page", "side", "x", "y", "w", "h", "kind",
                                           "similarity", "status", "juan", "radical")}
                      | {"modern": MODERN.get(row["codepoint"], ""), "common": is_common(MODERN.get(row["codepoint"], "")),
                         "has_glyph": (GLYPHS / f"u{row['codepoint']}.svg").exists()})
    rejected = [{k: row[k] for k in ("commons_title", "page", "side", "x", "y", "w", "h")} for row in rows if not row["codepoint"]]
    titles = {g["codepoint"]: g["commons_title"] for g in glyphs}
    for item in missing:
        item["modern"] = MODERN.get(item["codepoint"], "")
        item["common"] = is_common(item["modern"])
        if item.get("after"):
            item["after"]["commons_title"] = titles.get(item["after"]["codepoint"], "")
    return {"glyphs": glyphs, "missing": missing, "rejected": rejected,
            "halves": [{"commons_title": t, "page": p, "side": s, "top": halves[(t, p, s)]["top"],
                        "bottom": halves[(t, p, s)]["bottom"], "juan": halves[(t, p, s)]["juan"],
                        "spare": halves[(t, p, s)]["spare"]} for t, p, s in order],
            "rebuild": rebuild}


def crop_jpeg(query):
    page, side = int(query["page"][0]), query["side"][0]
    half = half_index()[(query["title"][0], page, side)]
    image = half_leaf(half["edition"], half["commons_title"], page, half["render_width"], half["crop_x0"],
                      half["crop_x1"], half["rotation"])
    x0, y0, x1, y1 = (int(float(query[k][0])) for k in ("x0", "y0", "x1", "y1"))
    x0, y0 = max(0, x0), max(0, y0)
    crop = image[y0:min(image.shape[0], y1), x0:min(image.shape[1], x1)]
    scale = float(query.get("scale", ["1"])[0])
    if scale != 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    return cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()


def approve(codepoints):
    wanted = {c.upper() for c in codepoints}
    already = {r["codepoint"] for r in read_csv(APPROVED)}
    rows = [r for r in read_csv(PROVENANCE) if r["codepoint"] in wanted - already
            and r["status"] in ("aligned", "manual", "approved")]
    append_csv(APPROVED, FIELDS, [{**r, "status": "approved"} for r in rows])
    return len(rows)


def unapprove(codepoints):
    wanted = {c.upper() for c in codepoints}
    rows = [r for r in read_csv(APPROVED) if r["codepoint"] not in wanted]
    with APPROVED.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n", restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve(text):
    """Code point (hex) for `U+3D016`, `3D016` or a modern character; or an error message."""
    text = text.strip().upper().removeprefix("U+")
    if len(text) == 5 and text.startswith("3") and text in MODERN:
        return text, None
    matches = [cp for cp, modern in MODERN.items() if text and text in modern.upper()]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"找不到「{text}」對應的篆書碼位"
    return None, "有多個碼位：" + "、".join(f"U+{cp}" for cp in matches[:8]) + "，請輸入其中一個"


def feedback(item):
    """Log the feedback; turn the structured kinds into corrections."""
    if item["action"] == "reassign":
        item["new_codepoint"], error = resolve(item.get("new_codepoint", ""))
        if error:
            return {"error": error}
    now = datetime.datetime.now().isoformat(timespec="seconds")
    append_csv(FEEDBACK, FEEDBACK_FIELDS, [{"time": now, **item}])
    half = half_index().get((item.get("commons_title", ""), int(item["page"]), item["side"])) if item.get("page") else None
    if half is None or item["action"] == "comment":
        return {"ok": True}
    base = {"edition": half["edition"], "commons_title": half["commons_title"], "page": item["page"],
            "side": item["side"], "x": item["x"], "y": item["y"], "w": item["w"], "h": item["h"],
            "reason": f"review {now}: {item.get('note', '')}".strip()}
    if item["action"] == "delete":  # "this crop is not that seal"; it may still be another one
        lines = [{**base, "action": "not", "codepoint": item["codepoint"]}]
    elif item["action"] in ("rebox", "locate"):  # the box is this code point
        lines = [{**base, "action": "assign", "codepoint": item["codepoint"]}]
    elif item["action"] == "reassign":
        lines = [{**base, "action": "assign", "codepoint": item["new_codepoint"]}]
    else:
        return {"error": "unknown action"}
    append_csv(CORRECTIONS, CORRECTION_FIELDS, lines)
    dirty.add(half["juan"])
    dirty.update(r["juan"] for r in read_csv(PROVENANCE) if r["codepoint"] in (item.get("codepoint"), item.get("new_codepoint")))
    return {"ok": True}


def run_rebuild():
    python = sys.executable
    steps = [["scripts/align_sequence.py", "--edition", "ccz", "--juan", rebuild["juan"]], ["scripts/trace_glyphs.py"],
             ["scripts/build_font.py", "--no-woff2"]  # web fonts take minutes; not needed while reviewing, ["scripts/proof_sheets.py"]]
    rebuild["log"] = ""
    for step in steps:
        result = subprocess.run([python, *step], cwd=ROOT, capture_output=True, text=True)
        rebuild["log"] += f"$ {' '.join(step)}\n{result.stdout[-3000:]}{result.stderr[-2000:]}\n"
        if result.returncode:
            break
    rebuild["running"] = False


class Handler(BaseHTTPRequestHandler):
    def send(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/":
                self.send(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                self.send(json.dumps(state(), ensure_ascii=False).encode(), "application/json")
            elif url.path == "/crop":
                with lock:
                    body = crop_jpeg(query)
                self.send(body, "image/jpeg")
            elif url.path.startswith("/glyph/"):
                path = GLYPHS / Path(url.path).name
                self.send(path.read_bytes(), "image/svg+xml") if path.exists() else self.send(b"", "text/plain", 404)
            else:
                self.send(b"not found", "text/plain", 404)
        except Exception as exc:  # show the reviewer what went wrong instead of a dead page
            self.send(str(exc).encode(), "text/plain", 500)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with lock:
            if self.path == "/api/approve":
                result = {"approved": approve(data["codepoints"])}
            elif self.path == "/api/unapprove":
                unapprove(data["codepoints"])
                result = {"ok": True}
            elif self.path == "/api/feedback":
                result = feedback(data)
            elif self.path == "/api/rebuild":
                if not rebuild["running"]:
                    # only the 卷 being reviewed: re-aligning the whole book takes hours
                    # plus every 卷 that received feedback since it was last aligned
                    juan = sorted(dirty | {data["juan"]}) if data.get("juan") else sorted({r["juan"] for r in read_csv(PROVENANCE)})
                    dirty.clear()
                    rebuild.update(running=True, juan=",".join(juan))
                    threading.Thread(target=run_rebuild, daemon=True).start()
                result = {"ok": True}
            else:
                return self.send(b"not found", "text/plain", 404)
        self.send(json.dumps(result).encode(), "application/json")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"review page: http://127.0.0.1:{PORT}/")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
