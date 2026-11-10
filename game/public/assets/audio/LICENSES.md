# Turku3D audio: sources and licences

This directory holds three kinds of audio. No file contains a third-party sample, recording or voice.

1. **Procedural effects** (22 `.ogg` files, table below): original sound effects created for Turku3D from
   2026-10-25 on. Source: [`game/scripts/prepare-audio.mjs`](../../../scripts/prepare-audio.mjs).
2. **Gull calls** (`gull-herring-longcall.ogg`, `gull-common-longcall.ogg`): resyntheses by Turku3D code from
   measurements of two CC0 field recordings (section "Gull calls").

To the extent possible under law, the authors dedicate the generators, the synthesis code and the audio of kinds 1
and 2 to [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/), waiving copyright and
related rights worldwide. They may be copied, modified and redistributed for any purpose,
including commercial use, without attribution. No warranty is provided.

## Procedural effects

| Files | Sound design/source | Licence |
|---|---|---|
| `step-asphalt.ogg` | Original noise and low shoe-impact tone | CC0-1.0 |
| `step-stone.ogg` | Original hard granite/stone impact resonances | CC0-1.0 |
| `step-grass.ogg` | Original filtered noise rustle | CC0-1.0 |
| `step-gravel.ogg` | Original granular noise crunch | CC0-1.0 |
| `step-wood.ogg` | Original wood-like resonant knock | CC0-1.0 |
| `engine.ogg` | Original harmonic combustion-like loop | CC0-1.0 |
| `scooter.ogg` | Original square-edged two-stroke buzz with exhaust flutter (added 2026-10-27) | CC0-1.0 |
| `curb.ogg`, `impact.ogg` | Original low thump and broadband chassis impact | CC0-1.0 |
| `door-open.ogg`, `door-close.ogg` | Original latch/creak and closing thud | CC0-1.0 |
| `city.ogg`, `traffic.ogg` | Original low urban rumble and passing-traffic swell | CC0-1.0 |
| `river.ogg` | Original water-like noise | CC0-1.0 |
| `wind.ogg` | Original filtered gusting noise | CC0-1.0 |
| `connect.ogg` | Original two-tone UI blip | CC0-1.0 |
| `crowd.ogg` | Original crowd murmur: syllable-rate envelopes over speech-band noise, no voices (added 2026-11-01) | CC0-1.0 |
| `crunch.ogg` | Original crash: low thump, crumpling noise bursts and a ringing panel (added 2026-11-06) | CC0-1.0 |
| `scrape.ogg` | Original metal-on-wall grinding loop (added 2026-11-06) | CC0-1.0 |
| `glass-break.ogg` | Original window shatter: a noise crack and random high pings (added 2026-11-06) | CC0-1.0 |
| `rattle.ogg` | Original loose-panel rattle loop of irregular knocks (added 2026-11-06) | CC0-1.0 |
| `hiss.ogg` | Original fluttering steam hiss loop (added 2026-11-06) | CC0-1.0 |

The trophy truck's engine, tyre, suspension and rear-steer sounds have no audio file: they are synthesised at
runtime by Turku3D code (`game/src/audio/engine/`, added 2026-11-05), an additive crossplane-V8 model with noise
layers. Reference recordings of the real car were used only for offline spectral measurement and never enter the
repository or the synthesis; the code and its output are CC0-1.0 like the files above.

Tyre sounds have no audio file either (added 2026-11-10, replacing `roll.ogg` and `screech.ogg`): the squeal, scrub,
rolling noise and gravel grit of every car, scooter and truck are synthesised at runtime by Turku3D code
(`game/src/audio/engine/tyre.ts`), a stick-slip friction model of five tread blocks. Its resonances, harmonic levels
and noise floor were calibrated against four CC0 recordings of real tyre squeal, measured offline only: "Screeching
Tires #1–#4" by Dorian Clair, BigSoundBank.com (https://bigsoundbank.com/screeching-tires-1-s2368.html to
`-4-s2371.html`, CC0). No recording or sample of them is in the repository or in the synthesis; the code and its
output are CC0-1.0.

These are compact, synthesized first-version effects, not recordings of Turku or realistic car samples.
Every file is mono Ogg/Opus at 32 kbit/s, encoded from 24 kHz PCM. Total payload of the 22 files: 255,691 bytes.
`manifest.json` records individual sizes, durations and SHA-256 hashes of every file in this directory. Loops have overlapped boundaries;
one-shots have attack/release envelopes. All samples peak below full scale before Opus encoding.

Regenerate from the repository root (requires local FFmpeg with libopus; no network):

```sh
source ../sandbox.env
npm run prepare-audio --prefix game
# or only some sounds, keeping the other files and manifest rows: npm run prepare-audio --prefix game -- crowd
```

The PCM generation is seeded and deterministic. Ogg container serial numbers can vary on re-encoding;
the manifest records the exact checked-in files.

## Gull calls

| File | Method | Measured recording | Licence |
|---|---|---|---|
| `gull-herring-longcall.ogg` (11.7 s) | Harmonic-plus-noise resynthesis of a European herring gull's long call | XC1178162, *Larus argentatus*, recorded by Philippe Grange at Ars-en-Ré, France, 2026-09-11: https://xeno-canto.org/1178162 (CC0 1.0) | CC0-1.0 |
| `gull-common-longcall.ogg` (9.3 s) | Harmonic-plus-noise resynthesis of a common gull's long call | XC803773, *Larus canus*, recorded by Grégoire Chauvot at Kvaløyvågen, Tromsø, Norway, 2023-05-12: https://xeno-canto.org/803773 (CC0 1.0) | CC0-1.0 |

Method (added 2026-11-10, replacing the procedural `gulls.ogg` loop): the recording is high-passed and cleaned by
spectral subtraction; every 5 ms the call's fundamental frequency, the levels of its first 32 harmonics and the
noise between the harmonics are measured; the call is then rendered again from those numbers alone with an oscillator
bank and shaped noise. The files therefore contain no sample of the recordings, only what Turku3D code computed
from the measurements, and the recordings themselves are not in the repository. Tools: `tools/sounds-fable/gulls/`
(`extract.py`, `resynth.py`) on the branch `local/loop-sounds-fable`. Both files are mono Ogg/Opus at 64 kbit/s from
48 kHz PCM, peak -3 dBFS (164,881 bytes together). The game plays them as occasional one-shots by the river and in
the harbour (`game/src/audio/gulls.ts`).
