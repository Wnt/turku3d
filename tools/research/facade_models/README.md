# Facade model ablations

Run from the repository root after `source ../sandbox.env` and installing
`./textures[cv-models]` in `textures/.venv`. These are manual research commands, not gates.
Inputs remain in the local research fixture and all outputs use `$TURKU3D_WORK/reports/models`.
No source imagery or trained binary is committed. The conclusions and numeric tables are in
[facade-cv-models.md](../../../docs/research/facade-cv-models.md).

```sh
PY=textures/.venv/bin/python
FIXTURE="$HOME/turku3d/wt/textures-fast-facade-pass/work/textures/fast-measure"
$PY -m turkutex.facade_models prepare siglip
$PY tools/research/facade_models/material.py
# Replays boxes in reports/models/grounding-dino-800.json; does not rerun the detector.
$PY tools/research/facade_models/box_grid.py
```

Material ridge splits whole building IDs using SHA-256 buckets: 0–1 test, 2–3 validation,
4–9 train. All 19 buildings in the reviewed set are excluded from all three splits.
Alpha is selected on validation only. The test majority-class result is reported alongside
accuracy; do not mistake plaster prevalence for general material recognition.

Florence's publisher files need Transformers 4.49.0; loading them with native 4.57 classes
leaves unmatched weights and must not be scored. Use an isolated environment whose `.pth`
adds the already installed common packages, with the older transformer/tokenizer taking
precedence. The explicit prepare command downloads pinned publisher Python source; only
this Florence probe uses `trust_remote_code=True`, offline from that local directory.

```sh
$PY -m turkutex.facade_models prepare florence
uv venv --python 3.12 "$TURKU3D_WORK/cache/florence-env"
uv pip install --python "$TURKU3D_WORK/cache/florence-env/bin/python" --no-deps \
  transformers==4.49.0 tokenizers==0.21.4 timm==1.0.22 einops==0.8.1
$PY - <<'PY'
import os
from pathlib import Path
site = Path(os.environ['TURKU3D_WORK'])/'cache/florence-env/lib/python3.12/site-packages'
(site/'shared.pth').write_text(str(Path('textures/.venv/lib/python3.12/site-packages').resolve())+'\n')
PY
PYTHONPATH="$PWD/textures:$PWD/pipeline" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  HF_MODULES_CACHE="$TURKU3D_WORK/cache/hf-modules" \
  "$TURKU3D_WORK/cache/florence-env/bin/python" tools/research/facade_models/florence.py
```

`facade_models_benchmark` is the common reviewed/pseudo runner. Its `--size 800` DINO
results are saved as `grounding-dino-800.json` for the NMS/row-mode replay. Replaying boxes
measures postprocessing cost only; it does not make inference free.
