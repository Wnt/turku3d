# Character voices: local TTS benchmark and choice (audio-voice-tts)

2026-10-26, MacBook Pro M1 Max (10 cores, 64 GB), macOS, everything local, no API keys or paid services.
Choice: **Kokoro-82M v1.0** (Apache-2.0). The original short-line build ran on CPU; the
expanded mission voice build uses MPS. Pipeline and commands: [`audio/voice/README.md`](../../audio/voice/README.md).

## Candidates

| Model | Version / revision | Weights | Licence (weights) | Voices | Notes |
|---|---|---|---|---|---|
| Kokoro-82M | `kokoro` 0.9.4, `misaki` 0.9.4, HF `f3ff3571791e` | 0.33 GB + 0.5 MB per voice | Apache-2.0 | 54 built-in packs, blendable | Inline phoneme override `[word](/IPA/)` |
| Chatterbox (0.5B) | `chatterbox-tts` 0.1.7, HF `5bb1f6ee58e5` | 3.2 GB | MIT | Zero-shot clone from a reference clip; exaggeration control | Needs `setuptools<81` (Perth watermarker imports `pkg_resources`); output carries Resemble's Perth watermark |
| Chatterbox-Turbo (350M) | same package, HF `749d1c1a46eb` | 4.0 GB snapshot | MIT | Zero-shot clone | No CFG/exaggeration |
| F5-TTS v1 Base | `f5-tts` 1.1.22, HF `84e5a410d9ce` + Vocos (MIT) | 1.35 GB | **CC-BY-NC-4.0** (trained on Emilia) | Zero-shot clone, needs reference transcript | Non-commercial only. MPS needs a one-line patch (0-d tensor pad width under torch 2.14); the 28 s paragraph still aborts in Metal |

Not run: Sesame CSM-1B (gated download, needs an account), Dia-1.6B and Orpheus-3B (CUDA-first; 1.6–3B
parameters will not reach 10 s per line on this machine), Zonos (needs a system espeak-ng and its MPS
path is unofficial), XTTS-v2 (CPML licence, excluded by the brief).

Cloning references for Chatterbox and F5 were synthesized with Kokoro voices (`am_michael`, `bm_george`,
`am_puck`, `af_nicole`, `af_heart`; 5–8 s each), so no real person's voice is used anywhere.

## Method

Six lines: one per contract speaker (narrator, pizzeria owner, dealer, licence seller, customer), all
naming Finnish streets, plus one 28 s narrator paragraph. Each engine ran in its own venv
(`~/turku3d/tools/voice/bench/{kokoro,chatterbox,f5}`), one engine at a time, with a warm-up line excluded
and a fixed seed. RTF = synthesis time / audio duration. Scores, computed locally:

- **MOS**: UTMOS22 strong (SpeechMOS v1.2.0), a predicted naturalness MOS (1–5).
- **WER**: Whisper `small.en` transcript against the script (numbers and street names inflate it).
- **Finnish names**: Whisper multilingual `small` with `language="fi"` on the name alone, as character
  error rate (CER) against the spelling. English ASR cannot judge Finnish names; a Finnish ear can.
- Spectrogram sheets per engine (`spectrogram-*.png`) were checked for clipping, dropouts, long silences
  and artifacts. Kokoro shows clean harmonics and natural phrase pauses; its `af_nicole` pack is
  broadband breath noise (a whisper voice). Chatterbox-MPS put two pauses of about 2 s into the market
  line (7.1 s vs Turbo's 3.5 s). F5's market line carries steady horizontal tonal bands (a hum artifact),
  and its lines start with up to 0.7 s of silence. No clipping or truncation was found.

**Host load caveat.** Other sandboxes ran ground builds, texture encodes and Chrome GPU suites during
the run; the 1-minute load average was 60–180 (logged per line as `load1`). Absolute RTFs are therefore
pessimistic, and MPS was hit hardest because it shares the GPU with the Chrome suites. The same holds
for every future `make voice-build` on this shared host, so the 10 s limit was judged under these conditions.

## Results

| Engine, device | Lines timed | Mean RTF, short lines | Slowest short line | 28 s paragraph | MOS mean (min) | WER | load1 |
|---|---|---|---|---|---|---|---|
| Kokoro CPU, first run | 5+1 | 0.16 | 1.4 s | 0.137 (3.9 s) | 4.17 (3.27) | 0.26 | low (before spike) |
| Kokoro CPU, rerun | 5+1 | 0.46 | 4.8 s | 0.349 (9.8 s) | 4.17 (3.27) | 0.26 | 148–161 |
| Kokoro MPS | 5+1 | 0.14 | 1.0 s | 0.113 (3.2 s) | 4.19 (3.29) | 0.26 | 132–137 |
| Chatterbox-Turbo CPU | 5+1 | 2.21 | 17.6 s | 1.73 (43.6 s) | 4.38 (4.23) | 0.22 | high |
| Chatterbox-Turbo MPS | 5 | 7.14 | 58.8 s | stopped (2.5 s per token) | 4.42 (4.34) | 0.22 | high |
| Chatterbox MPS | 5 | 5.71 | 73.8 s | skipped (RTF > 3) | 4.27 (3.80) | 0.30 | 82–145 |
| Chatterbox CPU | 1 | 6.28 | 48.2 s | stopped | 4.38 (4.29) | 0.19 | high |
| F5-TTS MPS | 5 | 1.54 | 9.0 s | aborts (Metal) | 3.85 (3.38) | 0.27 | 60–94 |
| F5-TTS CPU | 5+1 | 9.79 | 71.2 s | 9.37 (257 s) | 4.06 (3.57) | 0.23 | high |

Kokoro's low minimum is the `af_nicole` whisper voice used for the seller (MOS 3.27). The final presets
replace it (below). In the six-line set no engine pronounced Finnish names recognisably by default:
English Whisper heard "Christian and Khatu" / "Kristen and Katsu" / "Kristi Nankatu" for Kristiinankatu
and "Orakatu" for Aurakatu from every model.

### Finnish street names (Kokoro, Finnish Whisper CER, lower is better)

| Name | English G2P | IPA, Finnish tap ɾ | IPA, English ɹ | Shipped |
|---|---|---|---|---|
| Kristiinankatu | 0.43 "Kristian and Cartu" | 0.07 | **0.07** "Kristi Nankatu", MOS 4.34 | `kɹˈistiːnɑŋkˌɑtu` |
| Aurakatu | 0.25 "ORAKATU" | 0.63 "Olda kato" | **0.25** "Aura kato", MOS 4.06 (`…tuː`) | `ˈɑuɹɑkˌɑtuː` |
| Linnankatu | **0.00** | 0.30 | 0.30 | G2P |
| Läntinen Rantakatu | **0.11** | 0.28 | 0.17 | G2P |
| Turku | **0.00** | 0.40 "Tudkuu" | 0.20 | G2P |
| Kaskenkatu | **0.30** | 0.50 | 0.40–0.50 | G2P |

misaki turns `ɾ` into its American flap token, which is heard as "d", so lexicon entries use `ɹ`. Only
the phoneme control of Kokoro could fix Kristiinankatu; the cloning models offer only respelling.

For the 2027 mission address build, a Finnish Whisper small spot check on full address clips returned
"Lentonen Rantokatu 15" for Läntinen Rantakatu 15, "Kaskan Kato 4A" for Kaskenkatu 4a, and
"Mustainvilliesting Kuya 1" for Mustainveljestenkuja 1. The latter's English G2P baseline returned
"Mustang Velju Stankuja 1", so the shipped phoneme hint preserved more of the Finnish name. These
transcripts are an automated pronunciation proxy, not a human listening score.

### Final presets (34 lines of the current dialogue, built by `make voice-build`)

The preset sweep scored 2–5 candidate packs per role on a typical line. Shipped: narrator `bf_emma` @0.95
(4.41), pizzeria `bm_george` @1.05 (4.31, the older voice), dealer `am_puck,am_fenrir` blend @1.15 (4.48),
seller `af_sarah` @0.88 (4.52; `af_nicole` 3.30), customer `af_heart` @1.0 (4.50).

| Speaker | Lines | MOS | WER |
|---|---|---|---|
| narrator | 4 | 4.38 | 0.04 |
| pizzeria | 8 | 4.19 | 0.09 |
| dealer | 6 | 4.44 | 0.12 |
| market | 4 | 4.47 | 0.10 |
| customer | 12 | 4.45 | 0.01 |
| **all** | 34 | **4.38** | **0.06** |

Full build: 34 lines in 115 s on the loaded host (3.4 s per line), 0.6 MB of Opus. A rebuild with
nothing changed takes 0.0 s without loading the model. Rebuilding from the raw cache and re-synthesizing
deleted lines both reproduced every file byte for byte.

## Decision

**Kokoro-82M.** The expanded mission voice build uses Kokoro on MPS, pinned by `voices.toml`;
the benchmark above measured comparable quality and shorter latency than Kokoro CPU under host load.
Ranked on quality first, Chatterbox(-Turbo) scored about 0.2 MOS higher on the benchmark
set, but on this machine it takes 13–74 s per line, so it fails the hard limit of about 10 s per line,
and MPS made it slower, not faster. Kokoro with tuned presets reaches the same mean MOS (4.38) on the
real dialogue, produces a line in 1–5 s, is deterministic, needs no reference voices, and is the only
candidate that can pronounce Kristiinankatu. Apache-2.0 allows shipping the clips in a published,
even commercial, game. F5-TTS is excluded by its CC-BY-NC weights and by its MPS failures.

Runner-up if quality must rise later: Chatterbox-Turbo (MIT) on an idle host or a CUDA box, with Kokoro
clips as references, keeping this pipeline (only `voicegen/engine.py` and the `[engine]` table change).

## Reproduce

Benchmark outputs, WAVs, logs, scores and spectrograms: `~/turku3d/tools/voice/bench/out/`
(`<engine>-<device>/<line>.wav`, `refs/`, `sweep/`, `sweep2/`, `lexicon/`, `scores.json`). Scripts:
`~/turku3d/tools/voice/bench/scripts/` (`lines.json`, `bench.py`, `run_all.sh`, `evaluate.py`,
`sweep_synth.py`, `sweep_score.py`, `score_clips.py`). Example:

```sh
cd ~/turku3d/tools/voice/bench
HF_HOME=~/turku3d/tools/voice/hf kokoro/bin/python -I scripts/bench.py kokoro cpu out
XDG_CACHE_HOME=~/turku3d/tools/voice/cache TORCH_HOME=~/turku3d/tools/voice/cache/torch \
  eval/bin/python -I scripts/evaluate.py out
```

Disk: model caches under `~/turku3d/tools/voice/hf` (Kokoro 0.33 GB, Chatterbox 3.2 GB, Turbo 4.0 GB,
F5 1.35 GB, Vocos 0.05 GB), scorers under `~/turku3d/tools/voice/cache` (Whisper small/small.en
0.46 GB each, UTMOS 0.4 GB). Only the Kokoro snapshot is needed for `make voice-build`; the rest can be
deleted.
