# Ground texture build speed

Measured 2026-10-26 in the `tool-groundtex-speed` sandbox with `TURKU3D_JOBS=3`.
The host was shared with another ground texture build and browser tests, so cold codec
times include substantial machine contention. Each command used the real `work-3x3`
orthos and an ortho-only preview (no ground collision layer).

| Run | Wall time | Scope |
| --- | ---: | --- |
| Previous sequential builder | 1,071.41 s | One cold tile, `t0_0_0` |
| Parallel builder | 93.30 s | One cold tile, `t0_0_0` |
| Parallel builder | 1,075.91 s | Four tile command; three tiles cold, one cached |
| Parallel builder | 0.61 s | Four cached tiles |

The earlier one tile build spent most of its wall time in sequential `toktx`
encodes. The second archive ortho map alone occupied about 16 minutes under host
contention. The new builder encodes independent maps and tiles concurrently, with
at most `TURKU3D_JOBS` active encodes and assigned codec threads. It hashes raw VRT
sources, caches their resampled per-tile rasters and the area calibration, and
skips complete tiles when source and ground input hashes match. Repeated source
hash checks use file size and modification time as a fast index.

All five source PNGs and the uncertainty PNG for `t0_0_0` were byte identical
between the previous and new builder. KTX2 byte counts changed when `toktx`
received three threads instead of four. The selected gate passed in 5.60 s:
policy, generated files, Python lint, and texture tests (101 passed, 1 skipped).

The observed three cold tile run did **not** meet the under three minute cold
target. A fresh four tile cold run without competing heavy jobs is still needed
to determine the isolated performance limit; the current measurements cannot
establish that target. The cached target is met.
