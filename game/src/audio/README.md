# Game audio v1

`GameAudio` owns the Web Audio graph: buffer source → per-voice gain → optional HRTF panner →
channel gain → master gain → compressor → output. The renderer backend has no audio dependency.
The 18 original CC0 mono Opus effects total 194,444 bytes; sources and licences are in
[`public/assets/audio/LICENSES.md`](../../public/assets/audio/LICENSES.md).

Gameplay emits typed `AudioEvent` objects from `mix.ts`. `GameplayAudio.update` adapts the existing
character/vehicle snapshots once per physics tick. The main loop only supplies snapshots, camera pose,
input state and mute actions. No gameplay class creates audio nodes or selects sound filenames.

- Footsteps use actual grounded travel distance, with walking/running strides, and the generated
  material table's `audio` key. Asphalt, stone/granite, grass, gravel and wood have separate samples.
  Soil uses grass; sand uses gravel; concrete/metal use stone; water/none are silent.
- The positional engine follows throttle and estimated RPM. Vehicle v1 has no simulated drivetrain:
  a three-gear speed approximation supplies RPM. Rolling follows speed with wheel contact;
  screech follows lateral velocity/handbrake slip with wheel contact.
- Curb events consume W1-07 `curbStrike.id` once. Collision events use optional
  `VehicleState.impact = {id, impulse}` (N·s), measured by summing Rapier contact manifold impulses
  after each fixed step. A 600 N·s threshold and 150 ms cooldown suppress resting-contact chatter.
- Both enter and exit open the door, then close it after 300 ms. Gamepad connection emits one blip.
- City bed, wind and distant traffic mix with a positional river/gull pair. River position is the
  nearest point on an approximate Aura centreline in engine coordinates; the water/gull mix fades
  to zero over 140 m. This is an office-area approximation, not measured water geometry.
- Menu/M toggles mute; View/Escape pauses. The Audio panel has master, footsteps, vehicle, ambience
  and UI sliders. Settings are session-local. Pause fades loops and suppresses gameplay one-shots.

The context is created and resumed synchronously on the first key/pointer input. Gamepad activity
also attempts unlock; Chrome may still require a click or key under its user-activation policy.
Asset loading is asynchronous and errors are contained in `audio.snapshot().errors`; missing audio
never blocks gameplay. No historical steps/impacts are replayed after loading. A pending connection
blip is retained. At most 24 active voices (including eight loops) are allowed. One-shot completion
disconnects every node; `dispose()` stops voices and closes the context on pagehide. QA mode stays silent.

The listener follows camera position, forward and rotated up vectors. Spatial positions use engine
world-local metres. Gain, pitch and listener/emitter changes use 40 ms smoothing. The master limiter
reduces clipping when effects overlap. Synthesized assets are a functional v1; field-recorded realism
and physical Elite controller listening remain later acceptance work.

## Verification

From the repository root, with sandbox environment loaded:

```sh
source ../sandbox.env
npm test --prefix game -- --run tests/unit/audio.test.ts tests/unit/input.test.ts tests/unit/vehicle.test.ts
npm run build --prefix game
(cd game && npx playwright test tests/e2e/audio.spec.ts)
GATE_PYTHON="$HOME/turku3d/envs/pipeline/bin/python" scripts/dev/gate --base main
```

Fake AudioContext tests cover lazy unlock, routing, failures, teardown, mute/volumes, bounded voices,
material mappings and mixing. Synthetic Rapier tests cover resting contacts and wall impact impulses.
The Chrome e2e test unlocks via a real key, decodes every asset, builds all eight loops, walks for
footsteps and triggers each effect family without errors on WebGPU and WebGL2. `window.__game.audio`
exposes `emit`, `snapshot`, `unlock` and `dispose` for diagnostics; `snapshot()` reports graph state,
loaded count, loop/voice counts, volumes, played counts and errors.
