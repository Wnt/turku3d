"""Dialogue lines (game/src/gameplay/content/dialogue.json) and voice presets (audio/voice/voices.toml)."""

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

ID = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class Line:
    id: str
    speaker: str
    text: str


def voiced_lines(path):
    """Voiced lines in file order. Lines marked "voiced": false are HUD-only and get no audio."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    speakers, lines, seen = data["speakers"], [], set()
    for row in data["lines"]:
        if not ID.match(row["id"]) or row["id"] in seen:
            raise ValueError(f"bad or duplicate line id {row['id']!r}")
        if row["speaker"] not in speakers:
            raise ValueError(f"line {row['id']} has unknown speaker {row['speaker']!r}")
        if not row["text"].strip():
            raise ValueError(f"line {row['id']} has empty text")
        seen.add(row["id"])
        if row.get("voiced", True):
            lines.append(Line(row["id"], row["speaker"], row["text"]))
    return lines


@dataclass(frozen=True)
class Voices:
    engine: dict
    output: dict
    speakers: dict
    lexicon: dict

    def preset(self, speaker):
        if speaker not in self.speakers:
            raise KeyError(f"voices.toml has no [speakers.{speaker}] preset")
        return self.speakers[speaker]

    def spoken(self, text):
        """Text as sent to the model: lexicon words become inline phonemes, `[word](/ipa/)` (misaki markup)."""
        if not self.lexicon:
            return text
        words = sorted(self.lexicon, key=len, reverse=True)
        pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b")
        return pattern.sub(lambda m: f"[{m.group(1)}](/{self.lexicon[m.group(1)]}/)", text)


def load_voices(path):
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Voices(
        engine=data["engine"],
        output=data["output"],
        speakers=data["speakers"],
        lexicon=data.get("lexicon", {}),
    )
