"""Bridge existing producer CLIs to loaded areas and sandbox-local fetch output."""

import json
import os
import runpy
import shutil
import sys
from pathlib import Path

from turku3d import config as C
from turku3d.fetch.cached import check

from .areas import load_area


def main():
    action, source, *args = sys.argv[1:]
    area = load_area(source)
    C.AREAS[source] = area
    if action == "fetch":
        missing = check(area)
        if missing:
            # Never write shared raw. Hardlink immutable payloads, copy mutable registries.
            local = C.work_root() / "cache/region-inputs"
            shared = C.raw_dir()
            for path in shared.rglob("*"):
                if not path.is_file() or path.name.endswith((".part", ".lock")):
                    continue
                dest = local / "raw" / path.relative_to(shared)
                if dest.exists():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix in (".laz", ".parquet", ".tif", ".pbf"):
                    os.link(path, dest)
                else:
                    shutil.copy2(path, dest)
            os.environ["TURKU3D_DATA"] = str(local)
            from turku3d.fetch.__main__ import main as fetch

            if fetch(["all", "--area", source, "--workers", os.environ["REGION_WORKERS"]]):
                return 1
            missing = check(area)
        if missing:
            raise RuntimeError("incomplete fetched inputs: " + ", ".join(missing))
        Path(os.environ["REGION_FETCH_READY"]).write_text(
            json.dumps({"source": source, "data_root": str(C.data_root())})
        )
    elif action == "build":
        module, *arguments = args
        sys.argv = [module, *arguments]
        runpy.run_module(f"turku3d.{module}", run_name="__main__")
    else:
        module, *tiles = args
        sys.argv = [module, "--area", source, "--tile", *tiles]
        runpy.run_module(f"turku3d.package.{module}", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
