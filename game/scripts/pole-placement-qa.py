"""Top-down and oblique local QA renders of every full and draft pole position."""

import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main() -> None:
    work = Path(os.environ["TURKU3D_WORK"])
    source = json.loads((work / "reports/pole-source.json").read_text())
    out = work / "qa/poles"
    out.mkdir(parents=True, exist_ok=True)
    for kind, origin in (("full", 0), ("draft", 500)):
        poles = json.loads((work / f"reports/pole-placement-{kind}.json").read_text())
        references = {p["id"]: p for p in source[kind]}
        deviations = []
        for pole in poles:
            ref = references[pole["id"]]
            deviations.append(
                ((pole["base"]["x"] - ref["x"]) ** 2 + (pole["base"]["z"] - ref["z"]) ** 2) ** 0.5
            )
        fig, ax = plt.subplots(figsize=(10, 10))
        ax.scatter(
            [p["x"] for p in source[kind]],
            [p["z"] for p in source[kind]],
            marker="x",
            s=15,
            color="black",
            label="source",
        )
        ax.scatter(
            [p["base"]["x"] for p in poles],
            [p["base"]["z"] for p in poles],
            s=18,
            facecolors="none",
            edgecolors="crimson",
            label="runtime",
        )
        ax.set(
            xlim=(origin, origin + 250),
            ylim=(0, 250),
            xlabel="Engine x (m)",
            ylabel="Engine z (m)",
            title=f"{kind} poles: {len(poles)} / {len(references)}",
        )
        ax.set_aspect("equal")
        ax.grid(alpha=0.3)
        ax.legend()
        fig.savefig(out / f"{kind}-top.png", dpi=150)
        plt.close(fig)

        fig = plt.figure(figsize=(12, 9))
        ax = fig.add_subplot(projection="3d")
        for pole in poles:
            p = pole["base"]
            ax.plot(
                [p["x"], p["x"]],
                [p["z"], p["z"]],
                [p["y"], p["y"] + pole["height"]],
                color="crimson",
                linewidth=1.2,
            )
        ax.view_init(elev=28, azim=-60)
        ax.set(
            xlim=(origin, origin + 250),
            ylim=(0, 250),
            xlabel="Engine x (m)",
            ylabel="Engine z (m)",
            zlabel="N2000 height (m)",
            title=f"{kind}: all {len(poles)} pole shafts, 2.7 m high",
        )
        fig.savefig(out / f"{kind}-oblique.png", dpi=150)
        plt.close(fig)
        print(f"{kind}: {len(poles)} poles, maximum source displacement {max(deviations):.3f} m")


if __name__ == "__main__":
    main()
