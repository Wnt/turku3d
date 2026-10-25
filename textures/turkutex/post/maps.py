"""Normal / height (DeepBump, fallback Sobel) and ORM heuristic maps.

DeepBump (HugoTini/DeepBump, GPL-3.0, ONNX on CPU) is NOT vendored: `make textures-deepbump` clones it
to <data>/tools/DeepBump and we run its own cli.py in a subprocess (verified: 1254^2 albedo -> normals
in ~5 s on the M1 Max, ai-textures.md Verification 6). Normal maps are OpenGL convention (+Y up, as
glTF expects); DeepBump outputs that convention.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from ..paths import tools_dir

# material class -> (base roughness, roughness spread from luminance detail, metallic)
ORM_PRESETS = {
    "wood_painted": (0.62, 0.20, 0.0),
    "wood_weathered": (0.80, 0.15, 0.0),
    "render": (0.85, 0.10, 0.0),
    "stone": (0.75, 0.15, 0.0),
    "brick": (0.82, 0.12, 0.0),
    "concrete": (0.88, 0.08, 0.0),
    "metal_painted": (0.45, 0.20, 0.6),
    "glass": (0.08, 0.05, 0.0),
    "facade_mixed": (0.75, 0.20, 0.0),
    "asphalt": (0.90, 0.08, 0.0),
    "clay_tile": (0.70, 0.15, 0.0),
}


def deepbump_dir() -> Path:
    return Path(os.environ.get("DEEPBUMP_DIR", tools_dir() / "DeepBump"))


def deepbump_available() -> bool:
    return (deepbump_dir() / "cli.py").exists() and (deepbump_dir() / "deepbump256.onnx").exists()


def _deepbump(inp: Path, out: Path, module: str, *extra: str):
    d = deepbump_dir()
    # ONNX Runtime 1DS teardown can hang waiting for telemetry uploads after inference.
    # This is an offline deterministic worker: disable the uploader before ORT initializes.
    env = {**os.environ, "ORT_DISABLE_TELEMETRY": "1"}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "turkutex.post.deepbump_worker",
            str(d / "cli.py"),
            str(inp),
            str(out),
            module,
            *extra,
        ],
        cwd=d,
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def normals_deepbump(albedo: np.ndarray, overlap: str = "LARGE") -> np.ndarray:
    with tempfile.TemporaryDirectory() as t:
        a, n = Path(t) / "a.png", Path(t) / "n.png"
        Image.fromarray(albedo[..., :3]).save(a)
        _deepbump(a, n, "color_to_normals", "--color_to_normals-overlap", overlap)
        return np.asarray(Image.open(n).convert("RGB"))


def height_deepbump(normals: np.ndarray, seamless: bool = True) -> np.ndarray:
    with tempfile.TemporaryDirectory() as t:
        a, h = Path(t) / "n.png", Path(t) / "h.png"
        Image.fromarray(normals).save(a)
        _deepbump(
            a,
            h,
            "normals_to_height",
            "--normals_to_height-seamless",
            "TRUE" if seamless else "FALSE",
        )
        return np.asarray(Image.open(h).convert("L"))


def periodic_normals(normals: np.ndarray, axes: str) -> np.ndarray:
    """Blend a narrow periodic boundary and restore unit tangent-space vectors."""
    from .tileable import feather_wrap_x

    result = normals
    if "x" in axes:
        result = feather_wrap_x(result, max(4, result.shape[1] // 128))
    if "y" in axes:
        result = feather_wrap_x(
            result.transpose(1, 0, 2), max(4, result.shape[0] // 128)
        ).transpose(1, 0, 2)
    vectors = result.astype(np.float32) / 127.5 - 1
    vectors /= np.maximum(np.linalg.norm(vectors, axis=-1, keepdims=True), 1e-8)
    return np.clip(np.rint((vectors + 1) * 127.5), 0, 255).astype(np.uint8)


def normals_sobel(albedo: np.ndarray, strength: float = 2.0, wrap: bool = True) -> np.ndarray:
    L = albedo[..., :3].astype(np.float32).mean(-1) / 255.0
    L = ndimage.gaussian_filter(L, 1.0, mode="wrap" if wrap else "nearest")
    mode = "wrap" if wrap else "nearest"
    gx = ndimage.sobel(L, axis=1, mode=mode)
    gy = ndimage.sobel(L, axis=0, mode=mode)
    n = np.stack([-gx * strength, gy * strength, np.ones_like(L)], -1)  # +Y up (OpenGL)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return ((n * 0.5 + 0.5) * 255 + 0.5).astype(np.uint8)


def height_from_luminance(albedo: np.ndarray) -> np.ndarray:
    L = albedo[..., :3].astype(np.float32).mean(-1)
    L = ndimage.gaussian_filter(L, 1.5, mode="wrap")
    L = (L - L.min()) / max(1e-6, np.ptp(L))
    return (L * 255 + 0.5).astype(np.uint8)


def orm(
    albedo: np.ndarray, height: np.ndarray, material: str = "facade_mixed", ao_strength: float = 0.6
) -> np.ndarray:
    """R = ambient occlusion from height cavities, G = roughness, B = metallic (glTF ORM packing)."""
    base, spread, metal = ORM_PRESETS.get(material, ORM_PRESETS["facade_mixed"])
    h = height.astype(np.float32) / 255.0
    H, W = h.shape
    cav = ndimage.gaussian_filter(h, max(H, W) / 128, mode="wrap") - h
    ao = np.clip(1.0 - ao_strength * np.maximum(cav, 0) * 6, 0.35, 1.0)
    L = albedo[..., :3].astype(np.float32).mean(-1) / 255.0
    detail = L - ndimage.gaussian_filter(L, 4.0, mode="wrap")
    rough = np.clip(base - spread * detail * 4 + 0.1 * (1 - ao), 0.04, 1.0)
    # dark glass-like regions (very dark, low saturation) get lower roughness on facades
    if (
        material.startswith("facade")
        or material.startswith("wood")
        or material in ("render", "stone", "brick")
    ):
        rgb = albedo[..., :3].astype(np.float32) / 255
        sat = rgb.max(-1) - rgb.min(-1)
        glass = (L < 0.18) & (sat < 0.08)
        rough[glass] = 0.12
    met = np.full_like(h, metal)
    return (np.stack([ao, rough, met], -1) * 255 + 0.5).astype(np.uint8)


def ensure_deepbump(url: str = "https://github.com/HugoTini/DeepBump.git") -> Path:
    d = deepbump_dir()
    if not (d / "cli.py").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", url, str(d)], check=True)
    return d
