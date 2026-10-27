# Voice lines (audio/voice)

Local text-to-speech for the characters' dialogue. The pipeline reads
`game/src/gameplay/content/dialogue.json`, `phrases.json`, and the address pools named by
the active `$TURKU3D_DATA/dist/tiles/manifest.json` and sandbox manifest. It writes one mono
Ogg Opus clip per line to
`game/public/assets/voice/<id>.ogg`, plus `manifest.json`. The game's `VoicePlayer`
(`game/src/audio/voice.ts`) plays a direct text match or resolves an address sentence to its
phrase and address clips in HUD order.

## Model

| | |
|---|---|
| Model | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) v1.0, `kokoro-v1_0.pth` (327,212,226 bytes) + 6 voice packs (~0.5 MB each) |
| Revision | `f3ff3571791e39611d31c381e3a41a3af07b4987` (pinned in `voices.toml`) |
| Licence | Apache-2.0 for the weights and the `kokoro` 0.9.4 / `misaki` 0.9.4 code: commercial use allowed, so clips may ship in a published game |
| Runtime | PyTorch 2.11 on MPS, 24 kHz output; no network after `voice-setup` |
| Where | `~/turku3d/tools/voice/hf` (`VOICE_MODELS`, Hugging Face cache layout), never in the repo |

Why this model, and the measurements against Chatterbox, Chatterbox-Turbo and F5-TTS:
[`docs/reports/audio-voice-tts.md`](../../docs/reports/audio-voice-tts.md).

## Commands

From the repository root with the sandbox environment loaded (`source ../sandbox.env`):

```sh
make voice-setup   # audio/voice/.venv (Python 3.12, requirements.txt) + pinned weights and voice packs
make voice-build   # synthesize changed lines -> game/public/assets/voice (idempotent; through host slots)
make voice-check   # committed clips match dialogue.json + voices.toml (no model, no synthesis)
make voice-test    # pytest with a fake synthesizer (no model download)
```

After changing a voiced line's text, a speaker preset or the lexicon, run `make voice-build` and commit
the changed clips with `manifest.json`. The gate's `voice-tests` stage runs on changes to `audio/voice/`,
`dialogue.json` or the clips; it includes the committed-clip check, so a stale or missing clip fails the
gate with the line id. A full rebuild of the current 84 lines takes about 5 minutes (3 s per line on a
loaded host); unchanged lines cost nothing.

## How it works

- `voices.toml`: engine pin and seed, output settings and one preset per speaker (`voice` = Kokoro voice
  pack, `"a,b"` blends packs; `speed`). Every speaker that has a voiced line needs a preset.
- `[lexicon]` maps Finnish names to phonemes. Before synthesis each whole-word match becomes misaki
  inline markup `[Kristiinankatu](/kɹˈistiːnɑŋkˌɑtu/)`; the displayed text is unchanged. Add an entry only
  when a Finnish ASR check beats the G2P (see the report): use English `ɹ`, because misaki maps the
  Finnish tap `ɾ` to an American flap that is heard as "d".
- Lines with `"voiced": false` (HUD prompts) get no clip.
- `phrases.json` has twelve clips per announcement pool. Address clips use stable `address_<slug>` ids;
  `make voice-build` reads every published address pool from the shared and sandbox tile roots, so a new
  tile's addresses receive clips on the next voice build. The four scripted opening addresses are included
  even when a pool is unavailable. Run `make voice-build` after adding a tile and commit its clips.
- The British `bm_george` pizzeria voice speaks address clips. Existing listening checks found
  Läntinen Rantakatu and Kaskenkatu acceptable without hints. The lexicon supplies a phoneme hint for
  Mustainveljestenkuja; a Finnish Whisper spot check recognized more of that name with the hint than
  with English G2P. Foreign street-name spelling in the ASR transcript is still approximate.
- Post-processing: trim silence below -50 dB of peak (50 ms margin, 5 ms fades), normalize to
  -18 LUFS integrated (ITU-R BS.1770-4, `loudness.py`) with a -1 dBFS peak ceiling, encode Opus
  32 kbit/s with ffmpeg (`-fflags +bitexact`, so the same samples give the same bytes).
- Content addressing: the *synth key* hashes the engine pin and seed, the speaker preset and the spoken
  text (after the lexicon); raw model output is cached as `$TURKU3D_WORK/cache/voice/<synth key>.npz`.
  The *clip key* adds the output settings and `PIPELINE_VERSION`; it is stored per line in
  `manifest.json` with the file's SHA-256. A line whose clip key and file hash match is skipped
  without loading the model; a changed text, preset or lexicon entry regenerates only the lines it
  affects. Clips without a current static line, phrase or address are deleted. Synthesis is seeded, so a regenerated line is
  byte-identical.

`manifest.json`: `{"version": 1, "engine": {...}, "lines": [{"id", "speaker", "file", "seconds",
"text", "textSha256", "key", "fileSha256"}]}` in static-dialogue, phrase-pool and address order.

## Assets in the repo

The clips are committed, each below the 512 KiB per-file budget in `scripts/dev/gate.toml`.
`make game-build` (and so the live release) copies `game/public/assets/voice/` like the other
audio; nobody needs the model to build the game. `make voice-check` verifies the committed set
against the current dialogue, phrase pools and published addresses.
