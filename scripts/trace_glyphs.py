#!/usr/bin/env python3
"""Trace glyph outlines from scan crops using potrace and normalize to em box.

Reads the segmented page crops (from segment_pages.py), applies potrace to
convert raster to vector, and normalizes outlines to the em box (1000 UPM,
ascender 880, descender −120, centered horizontally).

Detailed tracing strategy:
1. Read grayscale crop, upscale to 800px height (double-cubic interpolation).
2. Re-binarize (Sauvola, then morphological smoothing to remove noise).
3. Potrace with smooth parameters: alphamax 1.5 (vs 1.0 for sharp), 
   opttolerance 0.2.
4. Normalize outline to em box: scale by height, center horizontally and
   vertically.
5. Write SVG, one file per code point (u3D000.svg, …).

Usage:
    python3 scripts/trace_glyphs.py [--input-dir build/segmented]
                                    [--output-dir glyphs]
                                    [--width 800]
                                    [--alphamax 1.5]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    import cv2
except ImportError:
    print("ERROR: opencv-python-headless not installed. pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "build" / "segmented"
DEFAULT_OUTPUT = ROOT / "glyphs"

# EM box defaults
UPM = 1000
ASCENDER = 880
DESCENDER = -120
BODY_HEIGHT = ASCENDER - DESCENDER  # 1000


def upscale_and_binarize(gray_crop, target_height=800):
    """Upscale crop to target height and apply Sauvola + morphological smoothing.
    
    Args:
        gray_crop: grayscale numpy array (from segment_pages.py)
        target_height: target pixel height after upscaling
    
    Returns:
        binary: binary image (0/255)
    """
    h, w = gray_crop.shape
    scale = target_height / h
    
    # Double-cubic upscaling
    enlarged = cv2.resize(gray_crop, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC)
    
    # Sauvola binarization (adaptive threshold)
    binary = cv2.adaptiveThreshold(
        enlarged, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,  # window size (must be odd)
        C=-2           # constant subtracted (negative → lower threshold)
    )
    
    # Morphological smoothing: close then open to remove noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    
    return binary


def estimate_stroke_width(binary):
    """Estimate typical stroke width for turdsize parameter.
    
    Args:
        binary: binary image
    
    Returns:
        turdsize: area threshold in pixels
    """
    # Distance transform: distance from each 0 pixel to nearest 255 pixel
    dist = cv2.distanceTransform(255 - binary, cv2.DIST_L2, cv2.DIST_PRECISE)
    
    # Median distance = half-width of typical strokes
    if dist.max() > 0:
        median_dist = sorted(dist.flatten())[len(dist.flatten()) // 2]
        stroke_width = int(median_dist * 2)
    else:
        stroke_width = 4
    
    # turdsize: area of small components to remove (< stroke_width²)
    turdsize = max(stroke_width * stroke_width // 4, 2)
    return turdsize


def potrace(input_png, output_svg, alphamax=1.5, opttolerance=0.2, turdsize=10):
    """Call potrace with smoothing parameters.
    
    Args:
        input_png: path to input PNG (binary)
        output_svg: path to output SVG
        alphamax: angle smoothing (1.0 sharp → 3.0 smooth); 1.5 recommended
        opttolerance: path optimization tolerance (0.1–0.2)
        turdsize: noise threshold in pixels
    """
    cmd = [
        'potrace',
        str(input_png),
        '-s',  # SVG output
        '--alphamax', str(alphamax),
        '--opttolerance', str(opttolerance),
        '--turdsize', str(turdsize),
        '-o', str(output_svg)
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def normalize_svg(svg_content, original_height_px, original_width_px):
    """Normalize SVG path to 1000 UPM em box.
    
    Current limitation: this is a placeholder. Full implementation would:
    1. Parse SVG path data
    2. Extract bounding box
    3. Scale to BODY_HEIGHT, center horizontally and vertically
    4. Re-emit SVG with viewBox="0 -880 1000 1000"
    
    For now, return SVG as-is (potrace output is already in pixel coordinates,
    which will be scaled when imported into a UFO).
    
    Args:
        svg_content: SVG file content (string or bytes)
        original_height_px: height of the crop in pixels
        original_width_px: width of the crop in pixels
    
    Returns:
        normalized: SVG string with em-box normalization applied
    """
    # TODO: implement full SVG path parsing and scaling
    # For now, return as-is
    if isinstance(svg_content, bytes):
        svg_content = svg_content.decode('utf-8', errors='replace')
    return svg_content


def trace_glyph(crop_json, output_svg, **kwargs):
    """Trace a single glyph crop.
    
    Args:
        crop_json: path to crop metadata JSON (from segment_pages.py)
        output_svg: path to write SVG glyph
        **kwargs: passed to potrace (alphamax, opttolerance, etc.)
    """
    with open(crop_json, 'r') as f:
        metadata = json.load(f)
    
    crop_png = Path(crop_json).parent / metadata['crop_file']
    
    # Read crop
    gray = cv2.imread(str(crop_png), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(f"Crop image not found: {crop_png}")
    
    # Upscale and binarize
    binary = upscale_and_binarize(gray, target_height=800)
    
    # Estimate stroke width for noise removal
    turdsize = kwargs.get('turdsize') or estimate_stroke_width(binary)
    
    # Save binary for potrace
    temp_png = Path(output_svg).parent / (Path(output_svg).stem + '_binary.png')
    cv2.imwrite(str(temp_png), binary)
    
    # Trace
    potrace(
        temp_png, output_svg,
        alphamax=kwargs.get('alphamax', 1.5),
        opttolerance=kwargs.get('opttolerance', 0.2),
        turdsize=turdsize
    )
    
    # Clean up
    temp_png.unlink(missing_ok=True)
    
    # Normalize (placeholder for now)
    with open(output_svg, 'r') as f:
        svg = f.read()
    normalized = normalize_svg(svg, gray.shape[0], gray.shape[1])
    with open(output_svg, 'w') as f:
        f.write(normalized)
    
    return metadata


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input-dir', type=Path, default=DEFAULT_INPUT,
                       help='directory with crop JSONs from segment_pages.py')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT,
                       help='directory to write traced SVGs')
    parser.add_argument('--width', type=int, default=800,
                       help='target height after upscaling (default 800px)')
    parser.add_argument('--alphamax', type=float, default=1.5,
                       help='potrace angle smoothing (1.0 sharp → 3.0 smooth; default 1.5)')
    parser.add_argument('--opttolerance', type=float, default=0.2,
                       help='potrace path optimization tolerance (default 0.2)')
    parser.add_argument('--dry-run', action='store_true',
                       help='list crops, do not trace')
    args = parser.parse_args(argv)
    
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 1
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    crops = sorted(input_dir.glob('*.json'))
    if not crops:
        print(f"No crop JSONs found in {input_dir}", file=sys.stderr)
        return 1
    
    if args.dry_run:
        print(f"Found {len(crops)} crops; sample:")
        for crop in crops[:5]:
            print(f"  {crop.name}")
        return 0
    
    # Trace each crop
    traced = 0
    for crop_json in crops:
        try:
            output_svg = output_dir / crop_json.stem.replace('_crop', '') + '.svg'
            trace_glyph(crop_json, output_svg,
                       alphamax=args.alphamax,
                       opttolerance=args.opttolerance)
            traced += 1
            if traced % 100 == 0:
                print(f"  traced {traced}…", file=sys.stderr)
        except Exception as e:
            print(f"ERROR {crop_json.name}: {e}", file=sys.stderr)
    
    print(f"Traced {traced} glyphs to {output_dir}", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
