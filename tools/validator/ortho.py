"""Make the validator's north-up PNG backdrop from cached ortho GeoTIFFs (no network).

Run with the native pipeline Python; bounds are engine x0,z0,x1,z1 metres.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import rasterio
from rasterio.merge import merge
from turku3d import config


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--bounds", type=float, nargs=4, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--resolution", type=float, default=0.5)
    a = p.parse_args()
    x0, z0, x1, z1 = a.bounds
    if x1 <= x0 or z1 <= z0 or a.resolution <= 0:
        p.error("bounds and resolution must be positive")
    e0, n1, _ = config.local_to_en(x0, 0, z0)
    e1, n0, _ = config.local_to_en(x1, 0, z1)
    datasets = []
    try:
        for path in sorted(a.root.rglob("*.tif")):
            d = rasterio.open(path)
            if d.crs != config.CRS_HORIZONTAL:
                d.close()
                raise ValueError(f"{path}: unexpected CRS")
            b = d.bounds
            if b.right <= e0 or b.left >= e1 or b.top <= n0 or b.bottom >= n1:
                d.close()
            else:
                datasets.append(d)
        if not datasets:
            raise ValueError("no orthophoto coverage for these bounds")
        image, _ = merge(datasets, bounds=(e0, n0, e1, n1), res=a.resolution, indexes=[1, 2, 3])
        a.out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            a.out,
            "w",
            driver="PNG",
            width=image.shape[2],
            height=image.shape[1],
            count=3,
            dtype=image.dtype,
        ) as dst:
            dst.write(image)
        print(f"{a.out}: {image.shape[2]}x{image.shape[1]}, {len(datasets)} cached chunks")
    finally:
        for d in datasets:
            d.close()


if __name__ == "__main__":
    main()
