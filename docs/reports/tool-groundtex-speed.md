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

## Three cold tile profile

The user-requested follow-up profiled `t0_0_1 t0_1_0 t0_1_1` in fresh sandbox
work roots with `TURKU3D_JOBS=3`. A thin wrapper timed the builder's existing
functions without changing their inputs. The two runs used the same source
rasters and codec settings; all 18 PNGs and 15 KTX2 files match byte for byte.

| Phase | Before releasing arrays | After releasing arrays |
| --- | ---: | ---: |
| Whole command | 322.61 s | 361.30 s |
| Source hash index | 3.19 s | 3.60 s |
| Ortho reads, summed across threads | 31.08 s | 37.45 s |
| Fusion, summed across threads | 36.97 s | 35.42 s |
| Fifteen encodes, summed across threads | 743.76 s | 828.90 s |

The three encoders run concurrently, so their summed times exceed command wall
time. The first encode wave began about 70 seconds into the build; the remaining
time was predominantly `toktx`. There is no long source hash, VRT reread, cache
key collision, or shared lock. During the first run the host showed load about
23, 99% CPU use, 63/64 GB physical memory in use, and 13 GB compressed. Codec
processes initially received only about one CPU core each despite being assigned
three threads. In the second run they briefly reached 2.2–2.3 cores each but
later slowed as host load rose. This accounts for the large difference from the
earlier 93-second single tile measurement and the 1,076-second contended run.

The builder now drops each tile's large raster arrays before starting its
encodes, reducing its live array set during compression. The second measured
run was slower because the shared host became busier; the change has no byte
effect. The cold under-three-minute target remains machine-bound under the
observed load. The four-tile cached path remains below one second.
