"""Coarse tiles: latency-sensitive first tier of the two-tier tile model."""

import os

# LAZ decoders and tiny terrain/roof matrix operations do not benefit from a
# thread pool per tile. Set these before the CLI imports native libraries; the
# service also supplies them explicitly when launching persistent workers.
for _name in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "RAYON_NUM_THREADS",
    "ARROW_IO_THREADS",
    "VIPS_CONCURRENCY",
):
    os.environ[_name] = "1"
