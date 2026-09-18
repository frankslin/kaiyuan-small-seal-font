#!/usr/bin/env python3
"""Fetch page renderings of the scans listed in sources/manifest.yaml.

Pages are requested from the Wikimedia Commons imageinfo API as JPEG
thumbnails of a given width and stored under sources/cache/, which is not
committed. An index of everything fetched is kept in sources/cache/index.json.

Usage:
    python3 scripts/fetch_pages.py                  # fetch every listed page
    python3 scripts/fetch_pages.py --edition ccz    # one edition only
    python3 scripts/fetch_pages.py --pages 7-20     # restrict page numbers
    python3 scripts/fetch_pages.py --dry-run        # print URLs, fetch nothing
    python3 scripts/fetch_pages.py --info           # print page counts only
"""

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "sources" / "manifest.yaml"
CACHE = ROOT / "sources" / "cache"
API = "https://commons.wikimedia.org/w/api.php"
MIN_INTERVAL = 1.0  # seconds between requests, per Commons bot policy


def slugify(title):
    name = title.split(":", 1)[-1]
    name = re.sub(r"\.(pdf|djvu)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^\w一-鿿㐀-䶿-]+", "_", name)
    return name.strip("_")[:80]


def parse_page_arg(text):
    if not text:
        return None
    pages = set()
    for part in text.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            pages.update(range(int(lo), int(hi) + 1))
        else:
            pages.add(int(part))
    return pages


def manifest_pages(entry):
    wanted = []
    for block in entry.get("pages", []):
        wanted.extend(range(int(block["first"]), int(block["last"]) + 1))
    skip = set(entry.get("skip_pages", []))
    return [p for p in wanted if p not in skip]


class Commons:
    def __init__(self, user_agent):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.last_request = 0.0

    def _throttle(self):
        wait = MIN_INTERVAL - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()

    def _get(self, url, **kwargs):
        for attempt in range(5):
            self._throttle()
            try:
                response = self.session.get(url, timeout=60, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                delay = 2 ** attempt
                print(f"  retry in {delay}s after {exc}", file=sys.stderr)
                time.sleep(delay)
        raise RuntimeError(f"giving up on {url}")

    def imageinfo(self, title, page=None, width=None):
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "imageinfo",
            "iiprop": "url|size|sha1|mime",
            "titles": title,
        }
        if page is not None and width is not None:
            params["iiurlwidth"] = str(width)
            params["iiurlparam"] = f"page{page}-{width}px"
        data = self._get(API, params=params).json()
        pages = data.get("query", {}).get("pages", [])
        if not pages or "imageinfo" not in pages[0]:
            raise RuntimeError(f"{title}: no imageinfo (missing file or bad title)")
        return pages[0]["imageinfo"][0]

    def download(self, url, destination):
        response = self._get(url, stream=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in response.iter_content(1 << 16):
                fh.write(chunk)
        tmp.replace(destination)


def load_index():
    path = CACHE / "index.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_index(index):
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--edition", help="only this edition key")
    parser.add_argument("--pages", help="only these PDF page numbers, e.g. 7-20,45")
    parser.add_argument("--width", type=int, help="override render width")
    parser.add_argument("--dry-run", action="store_true", help="print thumbnail URLs only")
    parser.add_argument("--info", action="store_true", help="print page counts only")
    parser.add_argument("--force", action="store_true", help="re-download cached pages")
    args = parser.parse_args(argv)

    manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    commons = Commons(manifest.get("user_agent", "kaiyuan-small-seal-font/0.1"))
    only_pages = parse_page_arg(args.pages)
    index = load_index()
    fetched = skipped = 0
    listed = sum(len(e.get("files") or []) for e in manifest.get("editions", {}).values())
    if not listed:
        print("manifest lists no files yet; fill in `files:` under an edition in sources/manifest.yaml", file=sys.stderr)
        return 1

    for key, edition in manifest.get("editions", {}).items():
        if args.edition and key != args.edition:
            continue
        for entry in edition.get("files", []) or []:
            title = entry["commons_title"]
            width = args.width or entry.get("render_width") or manifest.get("default_render_width", 2400)
            slug = slugify(title)
            if args.info:
                info = commons.imageinfo(title)
                print(f"{key}\t{title}\tpages={info.get('pagecount')}\tsize={info.get('size')}\tmime={info.get('mime')}")
                continue
            pages = manifest_pages(entry)
            if only_pages is not None:
                pages = [p for p in pages if p in only_pages]
            if not pages:
                print(f"{key}\t{title}\tno pages selected (fill in `pages:` in the manifest)")
                continue
            for page in pages:
                cache_key = f"{key}/{slug}/p{page:04d}-w{width}"
                destination = CACHE / key / slug / f"p{page:04d}-w{width}.jpg"
                if destination.exists() and not args.force:
                    skipped += 1
                    continue
                if args.dry_run:
                    print(f"{cache_key}\t{API}?action=query&prop=imageinfo&iiurlwidth={width}&iiurlparam=page{page}-{width}px&titles={title}")
                    continue
                info = commons.imageinfo(title, page=page, width=width)
                url = info.get("thumburl")
                if not url:
                    print(f"  {cache_key}: no thumburl in response", file=sys.stderr)
                    continue
                commons.download(url, destination)
                index[cache_key] = {
                    "edition": key,
                    "commons_title": title,
                    "page": page,
                    "width": width,
                    "thumburl": url,
                    "original_sha1": info.get("sha1"),
                    "original_url": info.get("url"),
                    "fetched": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                }
                fetched += 1
                print(f"  {cache_key}")
                if fetched % 20 == 0:
                    save_index(index)

    if not args.dry_run and not args.info:
        save_index(index)
        print(f"fetched {fetched}, already cached {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
