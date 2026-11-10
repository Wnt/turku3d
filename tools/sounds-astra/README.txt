Astra soundboard, 2026-11-10

Independent research and candidate audio. Do not merge this work into the game.
Source code and original synthesis/compositions: CC0-1.0.
Third-party recordings keep the licences and credits recorded in sources.json.
No recordings or model weights are committed. All output is under:
  ~/turku3d/loops/sounds-astra/

Dependencies: Python with numpy/scipy/matplotlib, FFmpeg with libopus;
Node with esbuild/playwright installed under work/node in the output directory.
The existing pipeline Python was used; no shared environment was changed.

From the sounds-astra worktree:
  python3 tools/sounds-astra/download-inputs.py
  node tools/sounds-astra/render-tyres.mjs
  python3 ~/turku3d/tools/host-lease/session.py run mac sounds-astra --kind func --slots 1 --headless --queue-timeout 60 -- node tools/sounds-astra/render-baselines.mjs
  OPENBLAS_NUM_THREADS=1 ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/generate-effects.py
  OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/compose.py
  OPENBLAS_NUM_THREADS=1 ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/granular-tyre.py
  node tools/sounds-astra/check-tyres.mjs
  OPENBLAS_NUM_THREADS=1 ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/analyse.py
  ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/build-board.py
  python3 ~/turku3d/tools/host-lease/session.py run mac sounds-astra --kind func --slots 1 --headless --queue-timeout 60 --load-timeout 120 --idle 180 -- node tools/sounds-astra/check-board.mjs
  bash ~/turku3d/loops/sounds/publish.sh astra ~/turku3d/loops/sounds-astra/board

Input provenance: sources.json includes public URL, author, licence, retrieval
date and the exact downloaded MP3 SHA-256. Source HTML snapshots stay in inputs/.
The source audio is intentionally not bundled with this code download.

Candidate loops use overlap/trim for ambience and tyres, circular note tails and
effects for composed music. The player uses decoded AudioBufferSourceNode loops,
not HTMLAudioElement loop timing. A short gain ramp protects manual play/stop.

Measurement limitations: average spectrum, gated RMS, endpoint jump and decode
success do not establish perceived quality. Ratings are provisional design
judgments, not a listener test. Recording species labels come from the recordist.
Common-gull source contains other birds. The tyre model is a perceptual prototype,
not a validated physical solver. No claim of a Turku field recording is made.

Two additional radio candidates were genuinely generated locally using ACE-Step
v1 (Apache-2.0 weights). Model revision and file hashes are in model-source.json;
render-model.py records original prompts, seeds, runtime and code revision.
No artist reference or reference audio was supplied. Model outputs are one-shots
with an end fade, not seamless loops. They are offered under CC0 to the extent
any rights are held; Apache-2.0 describes the weights, not the output licence.

Local-model reproduction uses a separate Python 3.11 environment under work/:
  git clone https://github.com/ace-step/ACE-Step.git ~/turku3d/loops/sounds-astra/work/ACE-Step
  git -C ~/turku3d/loops/sounds-astra/work/ACE-Step checkout 1bee4c9f5b43e30995f8d4d33b3919197ce1bd68
  python3.11 -m venv ~/turku3d/loops/sounds-astra/work/model-env
  ~/turku3d/loops/sounds-astra/work/model-env/bin/pip install -e ~/turku3d/loops/sounds-astra/work/ACE-Step
  python3 tools/sounds-astra/fetch-model.py
  OMP_NUM_THREADS=3 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 ~/turku3d/loops/sounds-astra/work/model-env/bin/python tools/sounds-astra/render-model.py m05-auransilta-fm
  OMP_NUM_THREADS=3 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 ~/turku3d/loops/sounds-astra/work/model-env/bin/python tools/sounds-astra/render-model.py m06-night-run
  OPENBLAS_NUM_THREADS=1 ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/prepare-model-audio.py m05-auransilta-fm m06-night-run
  # Then rerun analyse.py, build-board.py and the browser check above.

Each model run has an eight-minute alarm and a 0.48 MPS memory fraction. Only
one was run at a time. Runs took 116.24 s and 102.37 s including model loading,
excluding the download. Actual installed dependencies are retained in the local
evidence/model-environment.txt; version changes can change numerical outputs.
