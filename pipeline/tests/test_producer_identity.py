"""The producer identity (coarse/packaging.py) covers everything a tile build reaches.

A module the build reaches but the identity misses lets a change to it ship without rebuilding
any tile. The reach is the build's static statement closure (turku3d.source_code.Closure seeded
with coarse.build.build), so it follows imports inside functions and literal resource files.
"""

from pathlib import Path

from turku3d import facade_kit
from turku3d.coarse import packaging
from turku3d.source_code import Closure

PACKAGE = Path(packaging.__file__).resolve().parents[1]


def reached():
    closure = Closure(PACKAGE.parent)
    closure.name(PACKAGE / "coarse/build.py", "build")
    modules = {p.resolve() for p, found in closure.modules.items() if found["selected"] or found["whole"]}
    return modules | closure.resources


def covered():
    kit = [Path(facade_kit.__file__), Path(facade_kit.renderer().__file__), *facade_kit.palette_sources()]
    return {p.resolve() for p in [*packaging.producer_paths(), *kit]}


def excluded(path):
    for name, reason in packaging.NOT_PRODUCERS.items():
        target = (PACKAGE / name).resolve()
        if path == target or (name.endswith("/") and path.is_relative_to(target)):
            return reason
    return None


def test_every_module_a_build_reaches_is_a_producer():
    have = covered()
    missing = sorted(
        str(p.relative_to(PACKAGE.parents[1])) for p in reached() - have if excluded(p) is None
    )
    assert not missing, (
        "the build reaches these outside the producer identity; add coarse modules to "
        f"packaging.PRODUCERS, other files to packaging.SOURCES, or a reason to NOT_PRODUCERS: {missing}"
    )


def test_exclusions_are_reached_and_not_producers():
    paths = reached()
    have = covered()
    for name in packaging.NOT_PRODUCERS:
        target = (PACKAGE / name).resolve()
        hits = [p for p in paths if p == target or (name.endswith("/") and p.is_relative_to(target))]
        assert hits, f"NOT_PRODUCERS lists {name}, which the build no longer reaches"
        assert not set(hits) & have, f"{name} is both a producer and excluded"


def test_listed_producers_exist_once():
    assert len(set(packaging.PRODUCERS)) == len(packaging.PRODUCERS)
    assert len(set(packaging.SOURCES)) == len(packaging.SOURCES)
    for name in packaging.PRODUCERS:
        assert (PACKAGE / "coarse" / f"{name}.py").is_file(), name
    for name in packaging.SOURCES:
        assert (PACKAGE / name).is_file(), name
