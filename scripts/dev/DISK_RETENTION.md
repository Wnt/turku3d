# Disk retention

Every live publication prunes `$TURKU3D_DATA/live-releases`: retain live and the previous two
publications, plus releases referenced by running integration lanes (`from`, `to`,
`previous_release`, `release`) and rollback symlinks in the data root. Data releases with a
commit-plus-key name inherit the commit reference. Publication and pruning share the
integrator publication lock. Each publication records its predecessor in `.publication.json`,
so a newer prepared-but-unpublished directory cannot displace a previous live release.
Older releases without ancestry records bootstrap from complete releases ordered by mtime.

`$TURKU3D_DATA/cache/tool-stages/live/<stage>` retains three most recently stored or restored
keys per stage. Store and publication trigger cleanup; a stage lock protects restores and
stores from eviction, and abandoned temporary directories are removed under that lock.

`scripts/dev/codex-task status <name>` reports the sandbox cache's allocated size and warns
at 5 GiB, including tasks continuing with `--keep-sandbox`. `scripts/dev/wt rm <name>` reports
cache bytes before removing the whole sandbox. `codex-task finish` without `--keep-sandbox`
uses the same removal path, so `work/cache` is removed too. Before either path deletes
anything, `work/reports` moves to `~/turku3d/tasks/<task>/reports/` (or the configured
`TURKU3D_TASKS_ROOT`). Markdown report links and task result links are rewritten to
that durable location. Repeated task runs merge report directories; replaced files
survive under the task's `reports-history/<timestamp>/` directory. An archive failure
retains the sandbox rather than deleting evidence. Stop owned processes first if
cleanup reports that the sandbox is retained.

Synthetic retention checks:
```sh
bash pipeline/env/run.sh python scripts/dev/tests/run.py test_disk_retention.py
bash pipeline/env/run.sh python -m pytest pipeline/tests/test_region.py -q
PYTHONPATH=textures bash pipeline/env/run.sh python -m pytest textures/tests/test_pano_decode.py -q
```
