#!/usr/bin/env python3
"""Set a line of text in the built font and save it as a PNG specimen.

The text goes through OpenCC's `s2seal` chain (simplified -> traditional ->
說文 transcription -> Seal block), read straight from the dictionaries of an
OpenCC checkout (`--opencc`, $OPENCC_DIR or ../OpenCC), so the specimen picks
the same seal as OpenCC does. Without a checkout it falls back to kSEAL_MCJK
alone (lowest code point first), which misses characters such as 朕 that
kSEAL_MCJK records under their 說文 transcription 𦩎. Characters the 說文 has
no seal for, or whose seal is not traced yet, are drawn as an empty dashed
box: the specimen never borrows a glyph from elsewhere.

Usage:
    python3 scripts/specimen.py
    python3 scripts/specimen.py --text 天地玄黃 --out build/specimen-tdxh.png
"""

import argparse
import os
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SEAL_SOURCES = ROOT / "third_party" / "unicode" / "ucd" / "SealSources.txt"
FONT = ROOT / "build" / "KaiyuanSmallSeal-Regular.ttf"
TEXT = "朕能吞下玻璃而不伤身体"
CAPTION_FONTS = ["/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/System/Library/Fonts/Supplemental/Songti.ttc", "/System/Library/Fonts/PingFang.ttc"]
CELL, SEAL, MARGIN, CAPTION = 220, 180, 60, 34
PAPER, INK, FAINT = (250, 248, 240), (30, 26, 22), (170, 160, 140)


def seal_of():
    """{modern character: seal code point} from kSEAL_MCJK, the fallback without OpenCC."""
    table = {}
    for line in SEAL_SOURCES.read_text(encoding="utf-8").splitlines():
        if line.startswith("U+") and "\tkSEAL_MCJK\t" in line:
            cp, _, value = line.split("\t")
            for v in value.split():
                table.setdefault(chr(int(v, 16)), int(cp[2:], 16))  # file order: lowest code point first
    return table


def read_dictionary(path, reverse=False):
    """An OpenCC text dictionary as {key: first value}; reversed, the first key
    in file order wins unless a `# @reverse-prefer: <value> <key>` line says otherwise."""
    table, prefer = {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# @reverse-prefer:"):
            value, key = line.split(":", 1)[1].split()
            prefer[value] = key
        elif line and not line.startswith("#") and "\t" in line:
            key, values = line.split("\t")
            if reverse:
                for value in values.split():
                    table.setdefault(value, key)
            else:
                table[key] = values.split()[0]
    if reverse:
        table.update(prefer)
    return table


def convert(text, table):
    """Greedy longest match, as OpenCC segments; unknown characters pass through."""
    longest = max(map(len, table), default=1)
    out, k = [], 0
    while k < len(text):
        for n in range(min(longest, len(text) - k), 0, -1):
            if text[k:k + n] in table:
                out.append(table[text[k:k + n]])
                k += n
                break
        else:
            out.append(text[k])
            k += 1
    return "".join(out)


def to_seal(text, opencc):
    """The text in Seal block characters where a seal exists; s2seal's chain."""
    folder = opencc / "data" / "dictionary" if opencc else None
    if not folder or not (folder / "SealCharacters.txt").exists():
        table = seal_of()
        return "".join(chr(table[ch]) if ch in table else ch for ch in text), "kSEAL_MCJK only"
    simplified = {**read_dictionary(folder / "STCharacters.txt"), **read_dictionary(folder / "STPhrases.txt")}
    text = convert(text, simplified)
    text = convert(text, read_dictionary(folder / "SealVariants.txt"))
    return convert(text, read_dictionary(folder / "SealCharacters.txt", reverse=True)), f"OpenCC s2seal ({opencc})"


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--text", default=TEXT)
    parser.add_argument("--font", type=Path, default=FONT)
    parser.add_argument("--opencc", type=Path, default=Path(os.environ.get("OPENCC_DIR", ROOT.parent / "OpenCC")),
                        help="OpenCC checkout whose seal dictionaries to use")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "specimen.png")
    args = parser.parse_args(argv)
    args.out = args.out.resolve()
    if not args.font.exists():
        print(f"missing {args.font}; run build_font.py", file=sys.stderr)
        return 1
    covered = set(TTFont(args.font).getBestCmap())
    seals, how = to_seal(args.text, args.opencc if args.opencc.exists() else None)
    if len(seals) != len(args.text):  # a phrase rule changed the length: captions would drift
        print("conversion changed the number of characters", file=sys.stderr)
        return 1
    seal_font = ImageFont.truetype(str(args.font), SEAL)
    caption_path = next((p for p in CAPTION_FONTS if Path(p).exists()), None)
    caption_font = ImageFont.truetype(caption_path, CAPTION) if caption_path else None

    image = Image.new("RGB", (MARGIN * 2 + CELL * len(args.text), MARGIN * 2 + CELL + CAPTION + 30), PAPER)
    draw = ImageDraw.Draw(image)
    missing = []
    for k, ch in enumerate(args.text):
        x, y = MARGIN + CELL * k, MARGIN
        cp = ord(seals[k]) if 0x3D000 <= ord(seals[k]) <= 0x3FC3F else None
        if cp in covered:
            draw.text((x + CELL / 2, y + CELL / 2), chr(cp), font=seal_font, fill=INK, anchor="mm")
        else:
            missing.append(f"{ch} ({'not traced yet: U+%05X' % cp if cp else 'no seal in 說文'})")
            box = (x + 30, y + 20, x + CELL - 30, y + CELL - 20)
            for a in range(box[0], box[2], 16):
                draw.line([(a, box[1]), (min(a + 8, box[2]), box[1])], fill=FAINT, width=2)
                draw.line([(a, box[3]), (min(a + 8, box[2]), box[3])], fill=FAINT, width=2)
            for b in range(box[1], box[3], 16):
                draw.line([(box[0], b), (box[0], min(b + 8, box[3]))], fill=FAINT, width=2)
                draw.line([(box[2], b), (box[2], min(b + 8, box[3]))], fill=FAINT, width=2)
        if caption_font:
            draw.text((x + CELL / 2, y + CELL + 28), ch, font=caption_font, fill=INK if cp in covered else FAINT, anchor="mm")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(f"wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out} ({len(args.text) - len(missing)} of {len(args.text)} characters set, {how})")
    for item in missing:
        print(f"  no glyph: {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
