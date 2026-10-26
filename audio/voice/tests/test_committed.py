"""The committed clips match dialogue.json and voices.toml; a stale or missing clip names its line."""

from pathlib import Path

from voicegen.build import check

REPO = Path(__file__).resolve().parents[3]


def test_committed_clips_are_current():
    problems = check(
        REPO / "game/src/gameplay/content/dialogue.json",
        REPO / "audio/voice/voices.toml",
        REPO / "game/public/assets/voice",
    )
    assert problems == [], "run `make voice-build` and commit the clips:\n" + "\n".join(problems)
