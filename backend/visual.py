"""Visual regression: compare a screenshot against a stored baseline.

The point is not pixel purity — antialiasing, cursors and the odd animation
frame differ between runs on the same page — but catching the change a human
would call a regression: a layout that collapsed, a panel that vanished, text
that turned the same colour as its background.

So two knobs, the same pair every mature visual tool exposes:
  * `threshold`  — how different two pixels must be before they count at all,
                   which absorbs antialiasing noise;
  * `tolerance`  — what fraction of the image may differ before the comparison
                   fails, which absorbs a blinking caret.
"""

import io
import os
import re
from typing import Any, Dict, List, Optional

from PIL import Image, ImageChops, ImageDraw

from config import BASELINE_DIR

# A pixel whose channels differ by less than this is treated as identical.
DEFAULT_THRESHOLD = 12
# Fraction of differing pixels tolerated before the check fails.
DEFAULT_TOLERANCE = 0.002


def baseline_path(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", (name or "").strip())[:80]
    if not safe:
        raise ValueError("A baseline needs a name")
    return os.path.join(BASELINE_DIR, f"{safe}.png")


def list_baselines() -> List[Dict[str, Any]]:
    if not os.path.isdir(BASELINE_DIR):
        return []
    entries = []
    for filename in sorted(os.listdir(BASELINE_DIR)):
        if not filename.endswith(".png") or filename.endswith(".diff.png"):
            continue
        full = os.path.join(BASELINE_DIR, filename)
        with Image.open(full) as image:
            size = image.size
        entries.append({
            "name": filename[:-4],
            "width": size[0],
            "height": size[1],
            "savedAt": os.path.getmtime(full),
            "sizeBytes": os.path.getsize(full),
        })
    return entries


def delete_baseline(name: str) -> bool:
    path = baseline_path(name)
    if not os.path.exists(path):
        return False
    os.remove(path)
    diff = path[:-4] + ".diff.png"
    if os.path.exists(diff):
        os.remove(diff)
    return True


def save_baseline(name: str, png_bytes: bytes) -> Dict[str, Any]:
    path = baseline_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    image.save(path, format="PNG")
    return {"name": name, "path": path, "width": image.width, "height": image.height}


def compare(
    name: str,
    png_bytes: bytes,
    threshold: int = DEFAULT_THRESHOLD,
    tolerance: float = DEFAULT_TOLERANCE,
    write_diff: bool = True,
) -> Dict[str, Any]:
    """Compare a screenshot against the stored baseline.

    When no baseline exists the screenshot becomes the baseline and the result
    is reported as `created` — the first run of a new check should not fail.
    """
    path = baseline_path(name)
    candidate = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    if not os.path.exists(path):
        saved = save_baseline(name, png_bytes)
        return {
            "name": name, "status": "created", "passed": True,
            "message": f"Baseline '{name}' created at {saved['width']}x{saved['height']}.",
            "diffRatio": 0.0, "diffPixels": 0,
            "width": candidate.width, "height": candidate.height,
        }

    with Image.open(path) as opened:
        baseline = opened.convert("RGB")

    if baseline.size != candidate.size:
        # Resizing to compare would report a difference on every pixel and
        # bury the actual finding, which is that the viewport changed.
        return {
            "name": name, "status": "size-mismatch", "passed": False,
            "message": (
                f"Size changed: baseline is {baseline.width}x{baseline.height}, "
                f"this run is {candidate.width}x{candidate.height}."
            ),
            "diffRatio": 1.0, "diffPixels": candidate.width * candidate.height,
            "width": candidate.width, "height": candidate.height,
        }

    difference = ImageChops.difference(baseline, candidate).convert("L")
    # Everything at or below the threshold collapses to black, everything above
    # to white, so counting differing pixels is a single histogram lookup
    # rather than a per-pixel loop over a two-megapixel image.
    mask = difference.point(lambda value: 255 if value > threshold else 0)
    diff_pixels = mask.histogram()[255]
    total = candidate.width * candidate.height
    ratio = diff_pixels / total if total else 0.0
    passed = ratio <= tolerance

    diff_path = None
    if write_diff and not passed:
        diff_path = _write_diff_image(path, candidate, mask)

    return {
        "name": name,
        "status": "passed" if passed else "changed",
        "passed": passed,
        "message": (
            f"{ratio:.2%} of pixels differ (allowed {tolerance:.2%})."
            if not passed else f"Matches baseline ({ratio:.3%} differ)."
        ),
        "diffRatio": round(ratio, 6),
        "diffPixels": diff_pixels,
        "tolerance": tolerance,
        "threshold": threshold,
        "diffPath": diff_path,
        "width": candidate.width,
        "height": candidate.height,
    }


def _write_diff_image(baseline_file: str, candidate: Image.Image, mask: Image.Image) -> str:
    """Tint what changed red over a faded copy of the new screenshot.

    A raw difference image is hard to read — a faded original with the changed
    regions picked out shows *where* on the page the regression is.
    """
    faded = Image.blend(candidate, Image.new("RGB", candidate.size, (255, 255, 255)), 0.65)
    overlay = Image.new("RGB", candidate.size, (220, 38, 38))
    composed = Image.composite(overlay, faded, mask)

    # Outline the changed region so a one-pixel change is still findable.
    box = mask.getbbox()
    if box:
        draw = ImageDraw.Draw(composed)
        draw.rectangle(box, outline=(220, 38, 38), width=3)

    diff_path = baseline_file[:-4] + ".diff.png"
    composed.save(diff_path, format="PNG")
    return diff_path


def read_png(path: Optional[str]) -> Optional[bytes]:
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()
