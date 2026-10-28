# Hybrid appearance in the facade background queue

2026-10-28, M1 Max, sandbox `facade-kit-hybrid-colour`.

The queue enriches buildings in the player's ring/cone with the offline hybrid. It accepts
only measured wall colour, trim colour and material; every other field stays from the
register. The current engine has no trim-colour head, so that field explicitly abstains.
Parameter rows record the engine, per-field accepted source and raw prediction states and
reasons. A changed row changes only its building's draft parameter hash. Existing Astra
records remain authoritative; hybrid rows remain eligible for the original telemetry-ranked
Astra refinement (12 attempts/hour, pause at 60% or unknown quota). Local jobs continue while
Astra is paused. One persistent nice(15) child amortizes model startup across buildings.

The native geo interpreter aborts with duplicate OpenMP runtimes when Torch loads. The child
therefore uses the repository's clean `.venv/bin/python`. Torch, Transformers and scipy are
normal texture dependencies, installed by `scripts/dev/wt sync-env`. No runtime download,
new model weights, API keys or paid services were introduced. The existing pinned caches are
required; missing models produce a failed background job and leave the draft usable.

## Measurements

Six independently agreed pilot buildings were passed through the real `upgrade()` path,
including crop selection/encoding, register lookup, model inference, validation and atomic
parameter writes. Existing prepared references were copied into the sandbox and reference
acquisition was bypassed. Model loading happened once; the remaining five jobs reused the
model. Parameter IO was redirected to the sandbox. No shared parameter rows were changed.

| Measurement | Result |
| --- | ---: |
| First building, including cold model loading | 28.081 s |
| Mean subsequent building (five jobs) | 1.762 s |
| Warm processing throughput | 34.05 buildings/minute |
| Six-building throughput including cold startup | 9.76 buildings/minute |
| Estimated warm queue throughput with its 1 s tick | 21.72 buildings/minute |
| Synchronous model inference per draft | 0 s |

The tick estimate is arithmetic, not a measured live-player queue rate. Provider discovery,
reference rendering, failed screens, queue scans and Astra jobs reduce end-to-end throughput.
These shared-host timings do not establish throughput for arbitrary unseen buildings. All six
rows accepted colour/material and kept trim/storeys/columns from the register. Inference
runs outside draft generation; later parameter changes use the existing asynchronous draft
rebuild. CPU contention and total draft rebuild latency were not measured.

The benchmark and detailed timing JSON remain in
`$TURKU3D_WORK/reports/hybrid-queue/{benchmark.py,timings.json}`; reference pixels remain local.
To rerun the cached-reference measurement in this sandbox:

```sh
source ../sandbox.env
source ~/turku3d/envs/pipeline.env
scripts/dev/codex-task wait-cmd -- env PYTHONPATH=pipeline:textures .venv/bin/python \
  "$TURKU3D_WORK/reports/hybrid-queue/benchmark.py"
```

## Verification

```sh
source ../sandbox.env
.venv/bin/python -m pytest pipeline/tests/test_facade_kit.py -q
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

Seven unit tests pass, including measured-only appearance merging, independent abstentions,
missing/null fields, count preservation, per-field provenance, hash invalidation and Astra
eligibility. A manual empty-inventory protocol smoke also verified two replies reuse the
same child and shutdown reaps it, without reference or model calls. The fast gate is green.
Reference identity/visibility repairs and any future expansion to counts belong to
`cvr-references-truth`; this change preserves its existing reference function signatures.
