# Private draft reference measurement

```sh
make draft-reference-score BEFORE=main AFTER=worktree
make draft-reference-score BEFORE=<commit> AFTER=<commit>
# Optional measured-tile subset (default: all five agreed tiles):
make draft-reference-score BEFORE=main AFTER=worktree T='t0_1_0 t0_2_-1'
# Extra own neighboring outputs for streamed-scene / bridge duplicate support:
make draft-reference-score BEFORE=main AFTER=worktree T=t0_1_0 CONTEXT_TILES='t0_0_0 t0_0_1'
```

The command snapshots refs or a dirty worktree directory, builds both versions concurrently
through host build slots, then calls the optional private `gmaps diff` for matched metrics
against one frozen reference. Raw inputs are read-only; missing inputs fail the build without
fetching. Each side builds tiles serially to protect its own prepared cache and texture library.
The table includes before/after/delta/verdict and per-tile draft build seconds (from the builder,
excluding slot waits). Cold independent preparation is included on both sides; repeat timings
before claiming a small speed regression. Null or unmatched support means **no evidence**.
`--noise` sets an absolute metric tolerance (default 0.00001, the scorer's rounding unit).

Everything is retained under `$TURKU3D_WORK/reports/draft-reference-score/`, including immutable
own builds, source snapshots, build logs, private scorer output and `table.md`/`summary.json`.
These outputs must never be copied to the repository, release records or Pages. `GMAPS` can
select a local executable; the default is `~/turku3d/private/gmaps-extract/gmaps`. Without that
optional private installation the command prints a clear skip, builds nothing and exits 0.
A failed private measurement is retained as unscored and exits 2; it never implies clean geometry.


### Private data location

The private reference data, `~/turku3d/private/gmaps-data` (21 GB, 1.68 M files, hardlinked),
lives on bulk storage at `osgallery-dev:/bulk/turku3d/mac/private/gmaps-data/`. The local
directory holds only `MOVED-TO-BULK.txt`. While that marker exists, the private `gmaps` tool
and this command print the restore command and skip scoring (exit 0). Restore with:

```sh
rsync -aH osgallery-dev:/bulk/turku3d/mac/private/gmaps-data/ ~/turku3d/private/gmaps-data/ \
  && rm ~/turku3d/private/gmaps-data/MOVED-TO-BULK.txt
```

Metrics are ranked by street-level priority: ground/wall textures, bridges, ground,
wall/object geometry, then roof and aggregate height metrics marked informational.
Missing evidence abstains.

Context tiles are built at both versions but are not themselves scored. Supply all eight
neighbors when establishing bridge duplicate absence or edge-building coverage; incomplete
context must abstain where required by the private scorer. Never fill absent context from a
reference. Context build seconds are included in the final timing table.
