"""Area profiles: what is typical of a place, from the judged locations a light reading characterised.

A location file (`turku-description/location/1`, written by turku3d.observe.light) holds what stands in its judged
photographs and a `profile` aggregated from that: per kind the typical materials, colours and types with their
shares. An automatic rule for an entity nobody read asks for the profile of the nearest characterised location.
"""

import json
import math

from . import store


_files: dict[str, bytes] = {}  # the stored file of each location read, by its id


def locations() -> list[dict]:
    found = {}
    for root in reversed(store.description_roots()):
        for path in sorted((root / "location").glob("*.json")):
            data = path.read_bytes()
            found[path.stem] = json.loads(data)
            _files[found[path.stem]["id"]] = data
    return list(found.values())


def near(e: float, n: float, reach_m: float = 400.0, known: list | None = None) -> dict | None:
    """The profile of the nearest characterised location within reach, with its `location` id and `distance_m`."""
    best = None
    for doc in locations() if known is None else known:
        distance = math.dist((e, n), doc["at"])
        if distance <= reach_m and (best is None or distance < best[0]):
            best = (distance, doc)
    if best is None:
        return None
    if best[1]["id"] in _files:  # a build says in its manifest that this reading was in it
        store.note_read(best[1], _files[best[1]["id"]])
    return {"location": best[1]["id"], "distance_m": round(best[0], 1), "character": best[1].get("character", ""),
            **best[1].get("profile", {})}
