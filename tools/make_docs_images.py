"""Render README images straight from the measured fields.

The interferogram mockup is an HTML page, so screenshotting it needs a browser
and gives whatever resolution the window happened to be. This renders the same
phase image directly to PNG instead: deterministic, high resolution, and
reproducible from the data already embedded in the page — no second copy of the
measurements to drift out of sync.

Pure pixels, no text, so the absence of fonts offscreen does not matter.

    "<OSGeo4W>/bin/python-qgis.bat" tools/make_docs_images.py
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "mockups" / "interferogram.html"
OUT = ROOT / "docs"

#: (region name, which comparison, metres per fringe, output file)
PLATES = [
    ("Europe", 1, 20.0, "interferogram-europe-ballpark.png"),
    ("Europe", 0, 0.5, "interferogram-europe-nearest.png"),
    ("Australia", 1, 20.0, "interferogram-australia-ballpark.png"),
]


def phase_rgb(t: float):
    """Cyclic map for a cyclic quantity — one full sweep per fringe."""
    h = (t % 1.0) * 6.0
    i, f = int(h), h - int(h)
    q = 1.0 - f
    table = [
        (1.0, f, 0.0), (q, 1.0, 0.0), (0.0, 1.0, f),
        (0.0, q, 1.0), (f, 0.0, 1.0), (1.0, 0.0, q),
    ]
    r, g, b = table[i % 6]
    return int(r * 255), int(g * 255), int(b * 255)


def load_fields():
    html = PAGE.read_text(encoding="utf-8")
    match = re.search(r'<script id="payload"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise SystemExit("no payload found in {}".format(PAGE))
    return json.loads(match.group(1))


def render(region, field_cm, nx, ny, lam, width=900):
    from qgis.PyQt.QtGui import QImage, qRgba

    w, s, e, n = region["bounds"]
    mid = math.radians((s + n) / 2.0)
    aspect = ((e - w) * math.cos(mid)) / (n - s)
    height = max(1, int(round(width / aspect)))

    image = QImage(width, height, QImage.Format.Format_ARGB32
                   if hasattr(QImage, "Format") else QImage.Format_ARGB32)
    image.fill(0)

    for y in range(height):
        gy = (1.0 - y / (height - 1)) * (ny - 1)        # field row 0 is the south edge
        y0 = min(ny - 2, int(gy))
        fy = gy - y0
        for x in range(width):
            gx = x / (width - 1) * (nx - 1)
            x0 = min(nx - 2, int(gx))
            fx = gx - x0
            a = field_cm[y0 * nx + x0]
            b = field_cm[y0 * nx + x0 + 1]
            c = field_cm[(y0 + 1) * nx + x0]
            d = field_cm[(y0 + 1) * nx + x0 + 1]
            if a < 0 or b < 0 or c < 0 or d < 0:
                continue
            m = ((a * (1 - fx) + b * fx) * (1 - fy) +
                 (c * (1 - fx) + d * fx) * fy) / 100.0
            r, g, bl = phase_rgb((m % lam) / lam)
            image.setPixel(x, y, qRgba(r, g, bl, 255))
    return image


def main() -> int:
    from qgis.PyQt.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    data = load_fields()
    nx, ny = data["grid"]
    by_name = {r["name"]: r for r in data["regions"]}
    OUT.mkdir(exist_ok=True)

    for name, pair_index, lam, filename in PLATES:
        region = by_name.get(name)
        if region is None:
            print("skip {} — not in the payload".format(name))
            continue
        pair = region["pairs"][pair_index]
        image = render(region, pair["field_cm"], nx, ny, lam)
        path = OUT / filename
        if not image.save(str(path), "PNG"):
            print("failed to write {}".format(path))
            return 1
        fringes = (pair["max_m"] - pair["min_m"]) / lam
        print("{:<44} {:>4}x{:<4} {:>6.1f}–{:<7.1f} m  {:>5.1f} fringes @ {:g} m".format(
            filename, image.width(), image.height(),
            pair["min_m"], pair["max_m"], fringes, lam))

    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
