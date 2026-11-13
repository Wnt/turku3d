"""The `building` kind: a stored building description (docs/specs/building-description.md) laid over the LoD2
shell and the register facts becomes real geometry and procedural material records.

`discover` is the source side kept in ../building_shell.py (one entity per LoD2 building or uncovered register
footprint, with `shell` and `register` as facts); `describe` is the merge rule of describe.py; `compile` is
compile.py. The one-building workbench (workbench.py) runs the same describe and compile on one stored description.
"""

from __future__ import annotations

from pathlib import Path

from ...api import Area, Material, Output, Part, Stub, envelope
from .. import building_shell
from . import compile as compile_module
from . import describe as describe_module
from . import landmark


class Building:
    name = "building"
    code = (Path(building_shell.__file__),)

    def discover(self, area: Area, sources) -> list[Stub]:
        return building_shell.discover(area, sources)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        key = stub.id.split("/", 1)[1]
        doc = describe_module.effective(
            key, stub.facts["shell"], stub.facts.get("register", {}), stored
        )
        head = envelope(self.name, 1, stub, stored, "w2-buildings")
        # A landmark description of the proven game (landmark.py) raises an unread building to its own tier.
        proven = [p for p in doc.get("provenance", []) if p.get("by") == landmark.BY]
        tier = head["tier"] if stored or not proven else proven[0]["tier"]
        doc["tier"], doc["provenance"] = tier, head["provenance"] + proven
        return doc

    def compile(self, description: dict) -> Output:
        built = compile_module.compile(description)
        parts = [
            Part(
                part.name,
                part.positions,
                part.triangles,
                Material(
                    part.material.family,
                    part.material.params,
                    tuple(part.material.color),
                    part.material.roughness,
                    part.material.metalness,
                ),
                part.normals,
                part.uvs,
                None,
                part.attributes,
            )
            for part in built.parts
        ]
        return Output(tuple(built.origin), parts)
