"""Shared procedural facade floor: parameter-only buildings and a content-addressed atlas."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from turku3d import config as C

MATERIALS = ("plaster", "brick", "stone", "wood", "concrete", "glass", "metal")
CELL = 128
GUTTER = 4


def renderer():
    # The pipeline is also usable with only pipeline/ on PYTHONPATH.
    import sys

    path = str(C.REPO_ROOT / "textures")
    if path not in sys.path:
        sys.path.insert(0, path)
    from turkutex import facade_fast_render

    return facade_fast_render


def code_hash() -> str:
    h = hashlib.sha256(Path(__file__).read_bytes())
    h.update(Path(renderer().__file__).read_bytes())
    return h.hexdigest()[:16]


def store_path(key: str, root: Path | None = None) -> Path:
    """Opaque WFS/building ids cannot escape the parameter store."""
    return (root or C.work_root() / "facade-params") / (
        hashlib.sha256(str(key).encode()).hexdigest() + ".json"
    )


def read_description(key: str, root: Path | None = None, width: float | None = None) -> dict | None:
    try:
        from turku3d.facade_assets import description_row

        row = json.loads(store_path(key, root).read_text()) if root else description_row(key)
        if row is None:
            return None
        description = row["description"]
        if row["building_key"] == str(key) and not renderer().validate(
            description, description["facade_id"]
        ):
            if width and row.get("width_m"):
                return renderer().adopt(
                    description, description["facade_id"], width, row["width_m"]
                )
            return description
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write_description(
    key: str,
    description: dict,
    source: str,
    reference_hash: str,
    root: Path | None = None,
    width: float | None = None,
    *,
    engine: str = "astra",
    field_sources: dict | None = None,
    fields: dict | None = None,
):
    problems = renderer().validate(description, description.get("facade_id", ""))
    if problems:
        raise ValueError(problems)
    dest = store_path(key, root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    row = dict(
        building_key=str(key),
        description=description,
        source=source,
        reference_hash=reference_hash,
        width_m=width,
        engine=engine,
        field_sources=field_sources or {field: engine for field in description},
        fields=fields or {},
    )
    with tempfile.NamedTemporaryFile(mode="w", dir=dest.parent, delete=False) as f:
        json.dump(row, f, sort_keys=True)
    Path(f.name).replace(dest)


def parameters(
    key: str,
    height: float,
    width: float,
    attributes: dict | None = None,
    description: dict | None = None,
) -> dict:
    fr = renderer()
    d = (
        description
        or read_description(key, width=width)
        or fr.register_description(str(key), str(key), attributes or {}, width, height)
    )
    # The shared atlas has no roof band/plinth: the shell supplies the full wall height.
    plan = fr.layout({**d, "roof_line": "none", "plinth": False}, max(width, 1.0), max(height, 1.0))
    module = (
        MATERIALS.index(d["material"]) * 6
        + int(d["window_width"] > 0.55) * 3
        + {"windows": 0, "plain": 0, "doors": 1, "shops": 2}[d["ground_floor"]]
    )
    if d["window_columns"] == 0 and d["ground_floor"] == "plain":
        module = 42 + MATERIALS.index(d["material"])
    storey = max(1.0, height / plan["storeys"])
    # Only mapped retail use supports stretching the ground floor. Other walls
    # retain the register's regular floor grid rather than an arbitrary 8% uplift.
    ground = plan["ground_m"] if d["ground_floor"] == "shops" else storey
    upper = (
        (height - ground) / (plan["storeys"] - 1)
        if d["ground_floor"] == "shops" and plan["storeys"] > 1
        else storey
    )
    return dict(
        module=module,
        wall=d["wall_colour"],
        trim=d["trim_colour"],
        bay=3.0 if module >= 42 else plan["bay_m"],
        storey=storey,
        ground=ground,
        upper=upper,
    )


def module_maps(description: dict, module: int, size=(120, 240)) -> dict:
    """Neutral atlas with a colour-independent tint mask in opaque albedo alpha.

    Alpha 0 is wall, 1 is trim and 0.5 is untinted glass/mortar. Paired procedural
    renders isolate each pigment, including antialiasing, without guessing from brightness.
    """
    fr = renderer()
    description = {**description, "wall_colour": "#ffffff", "trim_colour": "#ffffff"}
    result = fr.render(description, 3.0, 6.4, size, f"kit-{module}")
    base = result["albedo"].astype(np.float32)
    weights = []
    for field in ("wall_colour", "trim_colour"):
        probe = fr.render({**description, field: "#000000"}, 3.0, 6.4, size, f"kit-{module}")
        delta = base - probe["albedo"].astype(np.float32)
        weights.append(np.clip(delta.sum(axis=2) / np.maximum(base.sum(axis=2), 1), 0, 1))
    alpha = np.rint(127.5 * (1 + weights[1] - weights[0])).astype(np.uint8)
    result["albedo"] = np.dstack([result["albedo"], alpha])
    return result


def resize_map(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Filter channels independently: pigment alpha must never premultiply wall RGB."""
    return np.stack(
        [
            np.asarray(
                Image.fromarray(image[:, :, channel]).resize(size, Image.Resampling.BILINEAR)
            )
            for channel in range(image.shape[2])
        ],
        axis=2,
    )


def ensure_kit(root: Path) -> dict:
    """Render and encode once per renderer hash. No panorama, model calls or per-wall images."""
    key = code_hash()
    target = root / ("facade-kit-" + key)
    meta = target / "kit.json"
    if meta.exists():
        return json.loads(meta.read_text())
    root.mkdir(parents=True, exist_ok=True)
    fr = renderer()
    with tempfile.TemporaryDirectory(prefix="facade-kit-", dir=root) as tmp:
        stage = Path(tmp)
        maps = {
            s: np.zeros((2048, 1024, 4 if s == "albedo" else 3), np.uint8)
            for s in ("albedo", "normal", "orm")
        }
        for module in range(49):
            material, variant = divmod(module, 6) if module < 42 else (module - 42, 0)
            d = fr.register_description("kit", "kit", {}, 3.0, 6.4)
            d.update(
                material=MATERIALS[material],
                wall_colour="#ffffff",
                trim_colour="#b0b0b0",
                storeys=2,
                window_columns=1 if module < 42 else 0,
                window_width=0.65 if variant // 3 else 0.45,
                ground_floor=("windows", "doors", "shops")[variant % 3] if module < 42 else "plain",
                ground_openings=1 if variant % 3 else 0,
                roof_line="none",
                plinth=False,
                features=[],
            )
            rendered = module_maps(d, module)
            col, row = module % 7, module // 7
            split = round(240 * (6.4 - rendered["layout"]["ground_m"]) / 6.4)
            for slot, atlas in maps.items():
                for floor in range(2):
                    image = rendered[slot][:split] if floor == 0 else rendered[slot][split:]
                    image = resize_map(image, (120, 120))
                    atlas[
                        (row * 2 + floor) * CELL : (row * 2 + floor + 1) * CELL,
                        col * CELL : (col + 1) * CELL,
                    ] = np.pad(image, ((GUTTER, GUTTER), (GUTTER, GUTTER), (0, 0)), mode="wrap")
        paths = {}
        for slot, atlas in maps.items():
            src = stage / f"{slot}.png"
            Image.fromarray(atlas).save(src)
            dest = stage / f"{slot}.ktx2"
            command = [
                "toktx",
                "--t2",
                "--genmipmap",
                "--threads",
                "2",
                "--assign_oetf",
                "srgb" if slot == "albedo" else "linear",
                "--encode",
                "uastc" if slot == "normal" else "etc1s",
                *(
                    ["--uastc_quality", "2", "--zcmp", "6"]
                    if slot == "normal"
                    else ["--clevel", "0"]
                ),
                str(dest),
                str(src),
            ]
            subprocess.run(command, check=True, capture_output=True)
            src.unlink()
            paths[slot] = str(target / dest.name)
        result = dict(key=key, maps=paths, version=3)
        (stage / "kit.json").write_text(json.dumps(result))
        try:
            os.rename(stage, target)
        except OSError:
            if not meta.exists():
                raise
    return json.loads(meta.read_text())


def parameter_hash(keys, root: Path | None = None) -> str:
    """This tile's descriptions/maps and shared PBR library invalidate its draft."""
    h = hashlib.sha256()
    if root is None:
        from turku3d.facade_assets import library_hash

        h.update(library_hash())
    for key in sorted(set(map(str, keys))):
        h.update(key.encode())
        if root is None:
            from turku3d.facade_assets import building_hash, description_row

            h.update(building_hash(key))
            h.update(json.dumps(description_row(key), sort_keys=True).encode())
        else:
            path = store_path(key, root)
            try:
                h.update(path.read_bytes())
            except FileNotFoundError:
                h.update(b"missing")
    return h.hexdigest()[:24]


def kit_request(root: Path, keys=()) -> dict:
    result = ensure_kit(root)
    descriptions = {str(key): d for key in keys if (d := read_description(str(key)))}
    widths = {}
    for key in descriptions:
        try:
            from turku3d.facade_assets import description_row

            widths[key] = (description_row(key) or {}).get("width_m")
        except (OSError, ValueError):
            pass
    return {**result, "descriptions": descriptions, "referenceWidths": widths}


def candidates(buildings) -> dict:
    """Four widest exterior walls per draft building; parameters/geometry only, never pixels."""
    features = []
    for b in buildings:
        ring = np.asarray(b.footprint.exterior.coords)[:, :2]
        edges = [
            (i, a, q, float(np.linalg.norm(q - a)))
            for i, (a, q) in enumerate(zip(ring[:-1], ring[1:], strict=True))
        ]
        for index, a, q, length in sorted(edges, key=lambda e: -e[3])[:4]:
            if length < 2:
                continue
            fid = "draft_" + hashlib.sha256(str(b.key).encode()).hexdigest()[:24] + f"_{index}"
            features.append(
                dict(
                    type="Feature",
                    properties=dict(
                        id=fid,
                        building_id=b.facade_key,
                        height_m=b.top - b.base,
                        base_h=b.base,
                        normal="left",
                        register_attributes=b.attributes,
                    ),
                    geometry=dict(
                        type="LineString",
                        coordinates=[
                            [float(p[0] + C.ORIGIN_E), float(C.ORIGIN_N - p[1])] for p in (a, q)
                        ],
                    ),
                )
            )
    return dict(type="FeatureCollection", features=features)
