"""observe gaps: strata bands and what counts as a stored reading."""

import json

from turku3d.observe import gaps


def test_bands_and_readings(tmp_path):
    assert [gaps.decade(y) for y in (None, 1885, 1912, 1956, 2031)] == ["undated", "-1899", "1900-19", "1950s", "2020s"]
    assert [gaps.storeys_class(s) for s in (None, 1, 2, 4, 7, 12)] == ["?", "1", "2", "3-4", "5-7", "8+"]
    assert gaps.street_age(None) == "open" and gaps.street_age(1965) == "1940-69"
    store = tmp_path / "descriptions"
    (store / "building").mkdir(parents=True)
    (store / "location").mkdir()
    docs = {"read": ("observed", "panorama"), "colour": ("auto", "panorama"), "old": ("observed", "facade-program"),
            "hand": ("reviewed", "hand")}
    for key, (tier, source) in docs.items():
        (store / "building" / f"{key}.json").write_text(json.dumps({"tier": tier, "provenance": [{"source": source}]}))
    (store / "location" / "x.json").write_text(json.dumps({"items": [
        {"kind": "building", "entity": "building/seen", "conf": 0.6},
        {"kind": "building", "entity": "building/faint", "conf": 0.3},
        {"kind": "building", "entity": "building/read", "conf": 0.9}]}))
    assert gaps.readings(store) == {"read": "full", "old": "converted", "hand": "hand", "seen": "light"}
