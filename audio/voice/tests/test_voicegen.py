import json
import math
import shutil

import numpy as np
import pytest
from voicegen.build import build, check, text_sha256
from voicegen.content import load_voices, voiced_lines
from voicegen.loudness import integrated_loudness, normalize, trim

VOICES = """
[engine]
name = "fake"
seed = 7

[output]
lufs = -18.0
peak_dbfs = -1.0
bitrate_kbps = 32

[speakers.narrator]
voice = "low"
speed = 1.0

[speakers.market]
voice = "high"
speed = 0.9

[lexicon]
"Linnankatu" = "lˈinːɑŋkˌɑtu"
"Läntinen Rantakatu" = "lˈæntinen ɾˈɑntɑkˌɑtu"
"Rantakatu" = "ɾˈɑntɑkˌɑtu"
"""


def dialogue(tmp_path, lines):
    path = tmp_path / "dialogue.json"
    path.write_text(
        json.dumps(
            {
                "speakers": {"narrator": {"name": "Narrator"}, "market": {"name": "Seller"}},
                "lines": lines,
            }
        ),
        encoding="utf-8",
    )
    return path


LINES = [
    {"id": "intro_1", "speaker": "narrator", "text": "Turku, 2027."},
    {"id": "market_offer", "speaker": "market", "text": "A licence? Eighty euros."},
    {
        "id": "prompt_take",
        "speaker": "narrator",
        "text": "Press A to take the pizza",
        "voiced": False,
    },
]


class FakeSynth:
    """Deterministic speech-like tone burst; frequency follows the preset, length follows the text."""

    def __init__(self):
        self.calls = []
        self.created = 0

    def factory(self, voices):
        self.created += 1
        return self.synth

    def synth(self, line, preset):
        self.calls.append(line.id)
        rate = 24000
        t = np.arange(int(rate * (0.3 + 0.03 * len(line.text)))) / rate
        tone = 0.2 * np.sin(2 * math.pi * (140 if preset["voice"] == "low" else 260) * t)
        silence = np.zeros(rate // 2)
        return np.concatenate(
            [silence, tone * (1 + 0.5 * np.sin(2 * math.pi * 3 * t)), silence]
        ), rate


@pytest.fixture
def env(tmp_path):
    voices = tmp_path / "voices.toml"
    voices.write_text(VOICES, encoding="utf-8")
    return {
        "dialogue": dialogue(tmp_path, LINES),
        "voices_path": voices,
        "out_dir": tmp_path / "out",
        "cache_dir": tmp_path / "cache",
    }


def run(env, fake):
    return build(**env, synthesizer_factory=fake.factory, log=lambda _: None)


def snapshot(out):
    return {p.name: p.read_bytes() for p in sorted(out.iterdir())}


def test_build_writes_clips_and_manifest(env):
    fake = FakeSynth()
    report = run(env, fake)
    assert sorted(report.synthesized) == ["intro_1", "market_offer"]
    manifest = json.loads((env["out_dir"] / "manifest.json").read_text())
    rows = {row["id"]: row for row in manifest["lines"]}
    assert set(rows) == {"intro_1", "market_offer"}
    assert rows["market_offer"]["speaker"] == "market"
    assert rows["market_offer"]["file"] == "market_offer.ogg"
    assert rows["market_offer"]["text"] == "A licence? Eighty euros."
    assert rows["market_offer"]["textSha256"] == text_sha256("A licence? Eighty euros.")
    # Trimming removes the half-second pads; the duration is the tone burst plus margins.
    assert 0.3 + 0.03 * 24 < rows["market_offer"]["seconds"] < 0.3 + 0.03 * 24 + 0.15
    assert (env["out_dir"] / "intro_1.ogg").read_bytes()[:4] == b"OggS"
    assert not (env["out_dir"] / "prompt_take.ogg").exists()
    assert check(env["dialogue"], env["voices_path"], env["out_dir"]) == []


def test_rebuild_is_idempotent_and_skips_the_model(env):
    run(env, FakeSynth())
    before = snapshot(env["out_dir"])
    fake = FakeSynth()
    report = run(env, fake)
    assert (
        fake.created == 0
        and report.encoded == []
        and sorted(report.reused) == ["intro_1", "market_offer"]
    )
    assert snapshot(env["out_dir"]) == before


def test_encoding_is_reproducible_from_cache(env):
    run(env, FakeSynth())
    before = snapshot(env["out_dir"])
    shutil.rmtree(env["out_dir"])
    fake = FakeSynth()
    report = run(env, fake)
    assert fake.created == 0 and sorted(report.encoded) == ["intro_1", "market_offer"]
    assert snapshot(env["out_dir"]) == before


def test_changed_text_regenerates_only_that_line(env, tmp_path):
    run(env, FakeSynth())
    lines = [dict(row) for row in LINES]
    lines[1]["text"] = "A licence? Ninety euros."
    env["dialogue"] = dialogue(tmp_path, lines)
    assert check(env["dialogue"], env["voices_path"], env["out_dir"]) == [
        "market_offer: clip is stale (text, preset or output settings changed)"
    ]
    fake = FakeSynth()
    report = run(env, fake)
    assert fake.calls == ["market_offer"] and report.reused == ["intro_1"]


def test_preset_change_regenerates_that_speaker(env):
    run(env, FakeSynth())
    env["voices_path"].write_text(VOICES.replace("speed = 0.9", "speed = 0.95"), encoding="utf-8")
    fake = FakeSynth()
    run(env, fake)
    assert fake.calls == ["market_offer"]


def test_removed_line_deletes_its_clip(env, tmp_path):
    run(env, FakeSynth())
    env["dialogue"] = dialogue(tmp_path, LINES[:1])
    assert check(env["dialogue"], env["voices_path"], env["out_dir"]) == [
        "market_offer: clip has no dialogue line"
    ]
    report = run(env, FakeSynth())
    assert report.removed == ["market_offer"] and not (env["out_dir"] / "market_offer.ogg").exists()


def test_tampered_clip_is_reencoded(env):
    run(env, FakeSynth())
    (env["out_dir"] / "intro_1.ogg").write_bytes(b"broken")
    assert check(env["dialogue"], env["voices_path"], env["out_dir"])
    fake = FakeSynth()
    assert run(env, fake).encoded == ["intro_1"] and fake.created == 0


def test_dialogue_validation(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        voiced_lines(dialogue(tmp_path, LINES[:1] * 2))
    with pytest.raises(ValueError, match="unknown speaker"):
        voiced_lines(dialogue(tmp_path, [{"id": "x", "speaker": "ghost", "text": "Boo."}]))
    with pytest.raises(ValueError, match="bad"):
        voiced_lines(dialogue(tmp_path, [{"id": "Bad-Id", "speaker": "narrator", "text": "Hi."}]))


def test_missing_preset_is_an_error(env, tmp_path):
    env["dialogue"] = dialogue(tmp_path, [{"id": "x", "speaker": "market", "text": "Hi."}])
    env["voices_path"].write_text(VOICES.split("[speakers.market]")[0], encoding="utf-8")
    with pytest.raises(KeyError, match="speakers.market"):
        run(env, FakeSynth())


@pytest.mark.parametrize("rate", [24000, 48000])
def test_bs1770_reference_sine(rate):
    # BS.1770: a full-scale 997 Hz sine reads -3.01 LUFS (K-weighting gain ~0 dB near 1 kHz).
    t = np.arange(rate * 3) / rate
    assert integrated_loudness(np.sin(2 * math.pi * 997 * t), rate) == pytest.approx(
        -3.01, abs=0.05
    )
    assert integrated_loudness(0.1 * np.sin(2 * math.pi * 997 * t), rate) == pytest.approx(
        -23.01, abs=0.05
    )


def test_gating_ignores_silence():
    rate = 24000
    t = np.arange(rate * 2) / rate
    tone = 0.1 * np.sin(2 * math.pi * 997 * t)

    def padded(seconds):
        return np.concatenate([np.zeros(rate * seconds), tone, np.zeros(rate * seconds)])

    # The absolute gate drops silent blocks, so the amount of silence does not matter; only the
    # partially filled edge blocks lower the reading slightly.
    assert integrated_loudness(padded(30), rate) == pytest.approx(
        integrated_loudness(padded(2), rate), abs=0.01
    )
    assert integrated_loudness(padded(2), rate) == pytest.approx(
        integrated_loudness(tone, rate), abs=1.0
    )


def test_normalize_hits_target_and_respects_peak():
    rate = 24000
    t = np.arange(rate * 2) / rate
    out = normalize(0.01 * np.sin(2 * math.pi * 997 * t), rate, -18.0, -1.0)
    assert integrated_loudness(out, rate) == pytest.approx(-18.0, abs=0.05)
    spiky = np.zeros(rate * 2)
    spiky[::2400] = 1.0
    assert np.max(np.abs(normalize(spiky, rate, -10.0, -1.0))) <= 10 ** (-1 / 20) + 1e-6
    with pytest.raises(ValueError):
        normalize(np.zeros(rate), rate, -18.0, -1.0)


def test_trim_keeps_margin_and_fades():
    rate = 24000
    x = np.concatenate([np.zeros(rate), 0.5 * np.ones(rate), np.zeros(rate)])
    out = trim(x, rate)
    assert len(out) == pytest.approx(rate + 2 * 0.05 * rate, abs=2)
    assert out[0] == 0.0 and out[-1] == 0.0


def test_lexicon_marks_whole_words_longest_first(env):
    voices = load_voices(env["voices_path"])
    assert voices.spoken("To Läntinen Rantakatu, then Rantakatu and Linnankatu 17.") == (
        "To [Läntinen Rantakatu](/lˈæntinen ɾˈɑntɑkˌɑtu/), then [Rantakatu](/ɾˈɑntɑkˌɑtu/) and "
        "[Linnankatu](/lˈinːɑŋkˌɑtu/) 17."
    )
    assert voices.spoken("Linnankatuja") == "Linnankatuja"


def test_lexicon_change_regenerates_only_lines_using_the_word(env, tmp_path):
    lines = [*LINES, {"id": "street", "speaker": "narrator", "text": "Go to Linnankatu."}]
    env["dialogue"] = dialogue(tmp_path, lines)
    run(env, FakeSynth())
    env["voices_path"].write_text(VOICES.replace("lˈinːɑŋkˌɑtu", "lˈinnɑŋkɑtu"), encoding="utf-8")
    fake = FakeSynth()
    run(env, fake)
    assert fake.calls == ["street"]
