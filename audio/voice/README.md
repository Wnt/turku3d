# Voice lines (audio/voice)

Local text-to-speech for the characters' dialogue. The pipeline reads
`game/src/gameplay/content/dialogue.json` (owned by gameplay; see the dialogue contract in
`game/src/gameplay/README.md`) and writes one mono Ogg Opus clip per voiced line to
`game/public/assets/voice/<id>.ogg`, plus `manifest.json`. The game's `VoicePlayer`
(`game/src/audio/voice.ts`) plays the clip whose `text` equals the HUD message being shown.

## Model

| | |
|---|---|
| Model | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) v1.0, `kokoro-v1_0.pth` (327,212,226 bytes) + 5 voice packs (~0.5 MB each) |
| Revision | `f3ff3571791e39611d31c381e3a41a3af07b4987` (pinned in `voices.toml`) |
| Licence | Apache-2.0 for the weights and the `kokoro` 0.9.4 / `misaki` 0.9.4 code: commercial use allowed, so clips may ship in a published game |
| Runtime | PyTorch 2.11 on the CPU (4 threads), 24 kHz output; no network after `voice-setup` |
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
gate with the line id. A full rebuild of the current 34 lines takes about 2 minutes (3 s per line on a
loaded host); unchanged lines cost nothing.

## How it works

- `voices.toml`: engine pin and seed, output settings and one preset per speaker (`voice` = Kokoro voice
  pack, `"a,b"` blends packs; `speed`). Every speaker that has a voiced line needs a preset.
- `[lexicon]` maps Finnish names to phonemes. Before synthesis each whole-word match becomes misaki
  inline markup `[Linnankatu](/lˈinːɑŋkˌɑtu/)`, so street names get first-syllable stress and Finnish
  vowels instead of the English G2P guess. The displayed text is unchanged.
- Lines with `"voiced": false` (HUD prompts) get no clip.
- Post-processing: trim silence below -50 dB of peak (50 ms margin, 5 ms fades), normalize to
  -18 LUFS integrated (ITU-R BS.1770-4, `loudness.py`) with a -1 dBFS peak ceiling, encode Opus
  32 kbit/s with ffmpeg (`-fflags +bitexact`, so the same samples give the same bytes).
- Content addressing: the *synth key* hashes the engine pin and seed, the speaker preset and the spoken
  text (after the lexicon); raw model output is cached as `$TURKU3D_WORK/cache/voice/<synth key>.npz`.
  The *clip key* adds the output settings and `PIPELINE_VERSION`; it is stored per line in
  `manifest.json` with the file's SHA-256. A line whose clip key and file hash match is skipped
  without loading the model; a changed text, preset or lexicon entry regenerates only the lines it
  affects. Clips without a dialogue line are deleted. Synthesis is seeded, so a regenerated line is
  byte-identical.

`manifest.json`: `{"version": 1, "engine": {...}, "lines": [{"id", "speaker", "file", "seconds",
"text", "textSha256", "key", "fileSha256"}]}` in dialogue order.

## Assets in the repo

The clips are committed: 34 lines are about 0.6 MB in total and each file is far below the 512 KiB
per-file budget in `scripts/dev/gate.toml`. `make game-build` (and so the live release) copies
`game/public/assets/voice/` like the other audio, and nobody needs the model to build the game. If the
dialogue grows past about 2 MB of clips, move the output to `$TURKU3D_WORK` and add a live-build step.
