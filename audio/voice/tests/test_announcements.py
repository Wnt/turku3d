"""The committed voice inventory is independent of tile publication."""

import json

from voicegen.announcements import extra_lines, published_addresses


def test_committed_addresses_ignore_live_pools(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    phrases = repo / "game/src/gameplay/content/phrases.json"
    phrases.parent.mkdir(parents=True)
    phrases.write_text("{}")
    inventory = repo / "audio/voice/addresses.json"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps({"known-1": "Known 1"}))
    data = tmp_path / "data"
    tiles = data / "dist/tiles"
    tiles.mkdir(parents=True)
    (tiles / "manifest.json").write_text('{"addresses":{"url":"addresses.json"}}')
    (tiles / "addresses.json").write_text('{"addresses":[{"id":"new-2","address":"New 2"}]}')
    monkeypatch.setenv("TURKU3D_DATA", str(data))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    assert [(line.id, line.text) for line in extra_lines(repo)] == [
        ("address_then", "Then"),
        ("address_known_1", "Known 1"),
    ]
    assert published_addresses()["new-2"] == "New 2"
    assert "address_new_2" in {line.id for line in extra_lines(repo, published_addresses())}
