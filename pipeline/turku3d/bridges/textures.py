"""Stage read-only ground library assets for the bridge material bindings."""

from pathlib import Path

from turku3d import config as C


def link(destination: Path, source: Path):
    if not source.exists():
        raise FileNotFoundError(source)
    if destination.exists() and destination.resolve() == source.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        if destination.resolve() == source.resolve():
            return
        destination.unlink()
    elif destination.exists():
        raise FileExistsError(destination)
    destination.symlink_to(source, target_is_directory=source.is_dir())


def main():
    source = C.data_root() / "textures"
    target = C.work_root() / "textures"
    link(target / "ground", source / "ground")
    for name in ("albedo", "normal", "orm"):
        link(target / "library" / f"{name}.array.ktx2", source / "library" / f"{name}.array.ktx2")
    library = source / "assembly/library"
    for path in sorted(library.iterdir()):
        if path.is_dir():
            link(target / "assembly/library" / path.name, path)
    for alias, original in (
        ("bridge_asphalt", "ground_asphalt"),
        ("bridge_stone", "ground_granite"),
        ("painted_steel", "roof_standing_seam_metal"),
    ):
        link(target / "assembly/library" / alias, library / original)


if __name__ == "__main__":
    main()
