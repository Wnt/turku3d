# Turku3D audio v1 — CC0

All 20 `.ogg` files in this directory are original procedural sound effects created for Turku3D
on 2026-10-25. Source: [`game/scripts/prepare-audio.mjs`](../../../scripts/prepare-audio.mjs).
No third-party samples, recordings, voices, music or model-generated audio were used.

To the extent possible under law, the authors dedicate the generator and its generated audio to
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/), waiving copyright and
related rights worldwide. The assets may be copied, modified and redistributed for any purpose,
including commercial use, without attribution. No warranty is provided.

| Files | Sound design/source | Licence |
|---|---|---|
| `step-asphalt.ogg` | Original noise and low shoe-impact tone | CC0-1.0 |
| `step-stone.ogg` | Original hard granite/stone impact resonances | CC0-1.0 |
| `step-grass.ogg` | Original filtered noise rustle | CC0-1.0 |
| `step-gravel.ogg` | Original granular noise crunch | CC0-1.0 |
| `step-wood.ogg` | Original wood-like resonant knock | CC0-1.0 |
| `engine.ogg` | Original harmonic combustion-like loop | CC0-1.0 |
| `scooter.ogg` | Original square-edged two-stroke buzz with exhaust flutter (added 2026-10-27) | CC0-1.0 |
| `truck.ogg` | Original V6 firing pulses with open-exhaust rasp and bank burble (added 2026-10-27) | CC0-1.0 |
| `roll.ogg`, `screech.ogg` | Original filtered tyre noise and modulated squeal | CC0-1.0 |
| `curb.ogg`, `impact.ogg` | Original low thump and broadband chassis impact | CC0-1.0 |
| `door-open.ogg`, `door-close.ogg` | Original latch/creak and closing thud | CC0-1.0 |
| `city.ogg`, `traffic.ogg` | Original low urban rumble and passing-traffic swell | CC0-1.0 |
| `river.ogg`, `gulls.ogg` | Original water-like noise and stylized gull calls | CC0-1.0 |
| `wind.ogg` | Original filtered gusting noise | CC0-1.0 |
| `connect.ogg` | Original two-tone UI blip | CC0-1.0 |

These are compact, synthesized first-version effects, not recordings of Turku or realistic car samples.
Every file is mono Ogg/Opus at 32 kbit/s, encoded from 24 kHz PCM. Total audio payload: 232,444 bytes.
`manifest.json` records individual sizes, durations and SHA-256 hashes. Loops have overlapped boundaries;
one-shots have attack/release envelopes. All samples peak below full scale before Opus encoding.

Regenerate from the repository root (requires local FFmpeg with libopus; no network):

```sh
source ../sandbox.env
npm run prepare-audio --prefix game
```

The PCM generation is seeded and deterministic. Ogg container serial numbers can vary on re-encoding;
the manifest records the exact checked-in files.
