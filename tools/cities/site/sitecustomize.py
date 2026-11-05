"""City processes (tools/cities/run.sh): apply the city's frame to turku3d.config before anything
else imports it. A Turku process never loads this file, so Turku's code identity is unchanged."""
import os
from pathlib import Path

if os.environ.get("TURKU3D_CITY") or (Path(os.environ.get("TURKU3D_DATA", "~/turku3d/data")).expanduser()
                                      / "city.json").exists():
    from turku3d.cities import apply

    apply()
