"""Run owned material-range acceptance on frozen deployed OCI source, then capture views."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    source = f"{HOME}/sources/{args.commit}"
    out = Path(os.environ["TURKU3D_WORK"]) / "reports/facade-materials"
    out.mkdir(parents=True, exist_ok=True)
    command = shlex.join(
        [
            "timeout",
            "--kill-after=3",
            "520",
            f"{source}/tools/remote-drafts/run.sh",
            "python",
            f"{source}/tools/remote-drafts/probe-facade-materials.py",
            "--root",
            "/srv/turku3d/work/coarse",
        ]
    )
    try:
        output = remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command)
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        raise SystemExit(error.stderr[-6000:]) from error
    print(output, flush=True)
    result = json.loads(output.splitlines()[-1])
    (out / "oci.json").write_text(json.dumps(dict(commit=args.commit, **result), indent=2) + "\n")
    views = [
        dict(
            id="kristiinankatu1",
            width=1280,
            height=960,
            pose=dict(E=23459641.48, N=6704204.43, H=8.14, yaw=333, pitch=11.6, roll=0, fov=65),
            time="2026-07-01T11:03:00+03:00",
        )
    ]
    import numpy as np
    from turku3d.facade_assets import building

    for key in ["103295900T", "1032959104"]:
        f = max(building(key)["facades"], key=lambda f: f["metadata"]["width_m"])
        a, b = np.asarray(f["coordinates"])[[0, -1], :2]
        normal = np.asarray(f.get("normal", [0, 0, -1]))[[0, 2]] * [1, -1]
        centre = (a + b) / 2
        eye = centre + normal * 22
        direction = centre - eye
        views.append(
            dict(
                id=key,
                width=1280,
                height=960,
                pose=dict(
                    E=eye[0],
                    N=eye[1],
                    H=f["base_height"] + 6,
                    yaw=float(np.degrees(np.arctan2(*direction))) % 360,
                    pitch=6,
                    roll=0,
                    fov=65,
                ),
                time="2026-07-01T11:03:00+03:00",
            )
        )
    view_path = out / "views.jsonl"
    view_path.write_text("".join(json.dumps(v) + "\n" for v in views))
    subprocess.run(
        [
            sys.executable,
            "scripts/dev/host_slots.py",
            "browser",
            "--",
            "node",
            "qa/render/render.mjs",
            "--views",
            str(view_path),
            "--url",
            "https://wnt.github.io/turku-game/game/",
            "--out",
            str(out / "renders"),
            "--pass",
            "color",
            "--timeout",
            "45000",
        ],
        check=True,
        timeout=170,
    )


if __name__ == "__main__":
    main()
