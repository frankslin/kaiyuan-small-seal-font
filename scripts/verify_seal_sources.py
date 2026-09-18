#!/usr/bin/env python3
"""Sanity-check the vendored UCD SealSources.txt.

Checks the SHA-256 recorded in SHA256SUMS, the Unicode 18.0 Seal block range,
that every code point in the block is present exactly once with a single
kSEAL_MCJK value and a kSEAL_Rad value, and that source sequence numbers are
well formed and unique per source.

Usage: python3 scripts/verify_seal_sources.py [path/to/SealSources.txt]
"""

import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "third_party" / "unicode" / "ucd" / "SealSources.txt"

SEAL_FIRST = 0x3D000
SEAL_LAST = 0x3FC3F
SEAL_COUNT = SEAL_LAST - SEAL_FIRST + 1  # 11328

SOURCE_PATTERNS = {
    "kSEAL_THXSrc": re.compile(r"^TH-(\d{5}|[XY]\d{3})$"),
    "kSEAL_CCZSrc": re.compile(r"^C-\d{5}$"),
    "kSEAL_QJZSrc": re.compile(r"^K-\d{5}$"),
    "kSEAL_DYCSrc": re.compile(r"^D-\d{5}$"),
}
KNOWN_PROPERTIES = set(SOURCE_PATTERNS) | {"kSEAL_MCJK", "kSEAL_Rad"}


def fail(message):
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def check_sha256(path):
    sums = path.parent / "SHA256SUMS"
    if not sums.exists():
        return fail(f"{sums} is missing")
    recorded = None
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == path.name:
            recorded = parts[0].lower()
    if recorded is None:
        return fail(f"{sums} has no entry for {path.name}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != recorded:
        return fail(f"SHA-256 mismatch: recorded {recorded}, actual {actual}")
    return 0


def check_contents(path):
    errors = 0
    props = defaultdict(dict)
    seen_source_values = defaultdict(set)
    with path.open(encoding="utf-8") as fh:
        for number, raw in enumerate(fh, start=1):
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) != 3:
                errors += fail(f"line {number}: expected 3 tab-separated fields")
                continue
            cp_text, prop, value = fields
            if not re.fullmatch(r"U\+[0-9A-F]{4,6}", cp_text):
                errors += fail(f"line {number}: bad code point {cp_text}")
                continue
            cp = int(cp_text[2:], 16)
            if not SEAL_FIRST <= cp <= SEAL_LAST:
                errors += fail(f"line {number}: {cp_text} outside the Seal block")
            if prop not in KNOWN_PROPERTIES:
                errors += fail(f"line {number}: unknown property {prop}")
                continue
            if prop in props[cp]:
                errors += fail(f"line {number}: duplicate {prop} for {cp_text}")
            props[cp][prop] = value
            if prop in SOURCE_PATTERNS:
                if not SOURCE_PATTERNS[prop].match(value):
                    errors += fail(f"line {number}: malformed {prop} value {value}")
                if value in seen_source_values[prop]:
                    errors += fail(f"line {number}: {prop} value {value} used twice")
                seen_source_values[prop].add(value)
            elif prop == "kSEAL_MCJK":
                if not re.fullmatch(r"[0-9A-F]{4,5}", value):
                    errors += fail(f"line {number}: kSEAL_MCJK {value} is not a single hex code point")
            elif prop == "kSEAL_Rad":
                for rad in value.split():
                    if not re.fullmatch(r"\d{1,3}\.[0-9A-F]{5}", rad):
                        errors += fail(f"line {number}: malformed kSEAL_Rad value {rad}")

    if len(props) != SEAL_COUNT:
        errors += fail(f"expected {SEAL_COUNT} code points, found {len(props)}")
    missing = [cp for cp in range(SEAL_FIRST, SEAL_LAST + 1) if cp not in props]
    if missing:
        errors += fail(f"{len(missing)} code points missing, first U+{missing[0]:04X}")
    for cp, values in props.items():
        for required in ("kSEAL_MCJK", "kSEAL_Rad", "kSEAL_THXSrc"):
            if required not in values:
                errors += fail(f"U+{cp:04X} has no {required}")

    if not errors:
        counts = {prop: len(values) for prop, values in seen_source_values.items()}
        print(f"OK: {len(props)} Small Seal code points; attestations {counts}")
    return errors


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    if not path.exists():
        return fail(f"{path} does not exist")
    return 1 if (check_sha256(path) + check_contents(path)) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
