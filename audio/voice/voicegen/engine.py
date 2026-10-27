"""Kokoro-82M (Apache-2.0) synthesizer, pinned to one Hugging Face revision.

Only `make voice-setup` (fetch) touches the network; synthesis loads the pinned snapshot offline.
"""

from pathlib import Path

import numpy as np

from .content import load_voices

RATE = 24000


def _patterns(voices):
    names = {name for preset in voices.speakers.values() for name in preset["voice"].split(",")}
    return [
        "config.json",
        voices.engine["weights"],
        *(f"voices/{name}.pt" for name in sorted(names)),
    ]


def fetch(voices_path):
    """Download the pinned weights and the voice packs that voices.toml uses; returns the snapshot path."""
    from huggingface_hub import snapshot_download

    voices = load_voices(voices_path)
    return snapshot_download(
        voices.engine["model"], revision=voices.engine["revision"], allow_patterns=_patterns(voices)
    )


def synthesizer(voices):
    import torch
    from huggingface_hub import snapshot_download
    from kokoro import KModel, KPipeline

    engine = voices.engine
    root = Path(
        snapshot_download(
            engine["model"],
            revision=engine["revision"],
            allow_patterns=_patterns(voices),
            local_files_only=True,
        )
    )
    torch.set_num_threads(engine["threads"])
    device = engine.get("device", "cpu")
    model = (
        KModel(
            repo_id=engine["model"],
            config=str(root / "config.json"),
            model=str(root / engine["weights"]),
        )
        .to(device)
        .eval()
    )
    pipelines = {}

    def synth(line, preset):
        # Voice names start with the accent: a = American, b = British English G2P.
        names = preset["voice"].split(",")
        lang = names[0][0]
        if lang not in pipelines:
            pipelines[lang] = KPipeline(lang_code=lang, repo_id=engine["model"], model=model)
        pipeline = pipelines[lang]
        # A comma-separated list blends the voice packs (mean style vector).
        pack = torch.stack(
            [pipeline.load_single_voice(str(root / "voices" / f"{n}.pt")) for n in names]
        ).mean(0)
        # The harmonic source adds noise; seeding makes reruns identical.
        torch.manual_seed(engine["seed"])
        audio = [
            r.audio.numpy()
            for r in pipeline(voices.spoken(line.text), voice=pack, speed=preset["speed"])
        ]
        return np.concatenate(audio), RATE

    return synth
