# Facade bindings after the building rebuild

Rebuilt the four L0 office tiles in the `w1-09d-facades` sandbox against current
building code. The approved export was copied from the shared 316-binding export
before the rebuild; shared raw data and shared derived products were not changed.

| Outcome | Walls |
|---|---:|
| Kept on the same wall and geometry | 82 |
| Re-projected from approved maps after reindex, move or split | 236 |
| Genuinely new street walls found | 40 |
| New walls bound from rectified photos and accepted labels | 26 |
| Old approvals with no safe geometric successor | 14 |
| New walls left unbound | 14 |

The final sandbox export has **344 validated bindings** (82 + 236 + 26). All
344 pass `turku3d.package.assemble.facade_bindings`; verified source KTX2 hashes
and projected UV bounds are checked during migration. The 14 unmatched old
approvals are retained under `textures/assembly/retired/<inventory-hash>/`.
Among the 14 new walls left unbound, eight had no usable full-wall panorama
after Google, Look Around and Mapillary selection. Six were rejected by the
batched visual labels because the wall was black/missing, too thin to read,
or showed roadway. Alternate panoramas recovered four initially rejected walls,
three from Mapillary and one from another Look Around date.

No image-generation calls were made. The eight Luna label calls used 209,268
input and 3,684 output tokens, or 6,655 total tokens per 32 uniquely labelled
new walls, including alternate-view relabels. Weekly quota was 54% at the start
of the task and 58% at the end; every call used the 70% quota guard.

Artifacts in this sandbox:

- `$TURKU3D_WORK/reports/facade-refresh.json`: per-wall migration provenance,
  genuinely new IDs and unmatched approved IDs.
- `$TURKU3D_WORK/reports/facade-refresh-direct.log`: per-new-wall result and reason.
- `$TURKU3D_WORK/reports/facade-refresh-contact.html`: photo/texture contact sheet.

The contact sheet covers all 631 street walls in the rebuilt office-tile
inventory: 344 bound and 287 using library fallbacks. This refresh specifically
reprocessed the 40 walls that had no predecessor in the approved source inventory;
the other unbound walls predate this rebuild.

Reproduce the migration and validate bindings:

```sh
make refresh REFRESH_TARGET=facades
PYTHONPATH=textures:pipeline bash pipeline/env/run.sh python - <<'PY'
import json, os
from pathlib import Path
from turku3d.package.assemble import facade_bindings
root = Path(os.environ['TURKU3D_WORK'])
export = root / 'textures/assembly'
bound = json.loads((export / 'MANIFEST.json').read_text())['bound']
assert set(facade_bindings(export / 'facades', root / 'build/layers')) == set(bound)
print(len(bound))
PY
scripts/dev/gate
```
