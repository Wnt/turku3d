# Offline facade characterization

Research only. Nothing here is imported by the OCI generator or publishes images.
All paths containing photographs, model predictions, covariates, descriptors or
contact sheets belong under `$TURKU3D_WORK`. The only committed evidence is aggregate
metrics and the research report. See `docs/research/facade-characterization.md`.

Use a sandbox-owned venv. The shared conda environment encountered duplicate OpenMP
runtimes when importing torch and OpenCV together; the Homebrew Python venv works.
The existing pinned model caches are read-only inputs; inference never downloads.

```sh
source ../sandbox.env
uv venv --python 3.12 "$TURKU3D_WORK/venv"
uv pip install --python "$TURKU3D_WORK/venv/bin/python" \
  -e './textures[cv-models,dev,ground]' -e './pipeline[buildings,lidar]'
export PYTHONPATH=textures:pipeline HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PY="$TURKU3D_WORK/venv/bin/python"
ROOT="$TURKU3D_WORK/characterization"
mkdir -p "$ROOT"
```

`panorama-index.json` is a frozen list of existing `PanoMeta.to_dict()` records,
with `cached_images: [{path: "google/<id>/pano_z5.jpg", bytes: N}]` added from the
OCI cache directory listing. Metadata-only panoramas must have an empty list.
Take the snapshot with a read-only SSH Python command; retain it locally. The
selection scans only these existing images and tests full-wall LOD2 visibility.
No provider download or model is invoked in selection.

```sh
$PY tools/research/facade_characterization/prepare.py \
  --inventory "$HOME/turku3d/research/facade-cv/truth100/whole-walls.geojson" \
  --panoramas "$ROOT/panorama-index.json" --output "$ROOT" --buildings 340
# Use the existing SSH identity; copy only these selected paths.
rsync -a --files-from="$ROOT/rsync-files.txt" \
  -e 'ssh -i /Users/wnt/.ssh/oci_cuttlefish -o BatchMode=yes' \
  ubuntu@82.70.50.61:/srv/turku3d/data/cache/streetlevel/ "$ROOT/panoramas/"
for stage in enrich extract fit doors rules contact; do
  scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- \
    "$PY" tools/research/facade_characterization/run.py "$stage" --root "$ROOT" || break
done
```

`doors` is the opening refinement stage: it caches the detector's windows **and**
doors. It replaces the collapsed CMP window class with metric/NMS-filtered DINO
boxes, excludes semantic foreground and refits wall colour outside the windows.
CMP still supplies wall/ground-opening/balcony segmentation. Its published six
classes cannot recover the original CMP cornice/sill/molding/pillar/shop labels.
No native helper was needed: model inference, not the small NumPy fit or painter,
dominates this workload.

Raw extraction keys include panorama bytes, camera record, model revisions and
extractor code. Detector keys include crop bytes, detector implementation/revision
and size. Fits and rule tables are cheap deterministic recomputations. Always run
`fit` before `doors` when changing covariates. `programs.json` is the photographed
building list; `rules.json` is the local empirical distribution; `sampled.json`
contains buildings without a usable selected view. These are not assertions that
no photograph of those buildings exists anywhere.

## Evaluation

`evaluate.py compare` consumes the eligible round-2 reports read-only, plus a
local frozen `round3-measurements.json` with `buildings` and `wall_groups`, as
extracted by the wall-palette study. It matches register IDs and one unambiguous
parallel photographed wall. It never fits to Google reference colour targets.

```sh
$PY tools/research/facade_characterization/evaluate.py compare --root "$ROOT"
$PY tools/research/facade_characterization/evaluate.py manual_prepare --root "$ROOT"
# Review manual-01.png through manual-12.png before looking at predictions.
# Save manual-template.json as manual-labels.json with counts/abstentions and notes.
$PY tools/research/facade_characterization/evaluate.py manual_score --root "$ROOT"
$PY tools/research/facade_characterization/evaluate.py timing --root "$ROOT"
```

The historical `original20-colour.json` was recovered from the complete JSON array
in `facade-cv-describe/events.jsonl`, completed command item_12. Retain that local
array unchanged. Original fixture JPEG hashes were deleted; `reviewed20.py` uses
the highest-score retained full view and the original 768-pixel JPEG transform,
and explicitly reports the reconstruction limitation.

```sh
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- \
  "$PY" tools/research/facade_characterization/reviewed20.py --root "$ROOT" \
  --refs "$HOME/turku3d/research/facade-cv/refs"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest -q textures/tests/test_facade_program.py
```

GPU-rental estimates are scenario arithmetic (1x/2x/4x neural inference speed,
unchanged CPU stages), not a CUDA benchmark or a purchased service. The unique
wall count per panorama remains unknown; the report gives a 2/4/8-wall range.
