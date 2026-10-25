"""Native KTX-Software encoder: 2D products and shared library array textures."""
import json
import subprocess
from pathlib import Path
from PIL import Image

from ..post.pipeline import ktx2_header, sha


def encode(inputs: list[Path], output: Path, *, linear=False, uastc=False, normal=False) -> dict:
    if not inputs:
        raise ValueError("at least one image required")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_hashes = [sha(p) for p in inputs]
    with Image.open(inputs[0]) as image:
        large = max(image.size) > 2048
    cmd = ["toktx", "--t2", "--genmipmap", "--threads", "4",
           "--assign_oetf", "linear" if linear else "srgb",
           "--encode", "uastc" if uastc else "etc1s"]
    if len(inputs) > 1:
        cmd += ["--layers", str(len(inputs))]
    if uastc:
        cmd += ["--uastc_quality", "2", "--zcmp", "3"]
    else:
        cmd += ["--qlevel", "128" if large else "160", "--clevel", "0" if large else "2"]
    if normal:
        # toktx otherwise repacks X/Y as RGB=X, A=Y; glTF/TSL expects RGB=XYZ.
        cmd += ["--normal_mode", "--input_swizzle", "rgb1"]
    cmd += [str(output), *map(str, inputs)]
    sidecar = output.with_suffix('.encode.json')
    if sidecar.exists() and output.exists():
        cached = json.loads(sidecar.read_text())
        if (cached.get('inputs') == source_hashes and cached.get('command') == cmd
                and cached.get('sha256') == sha(output)):
            return cached
    temporary = output.with_suffix('.tmp.ktx2')
    actual = [str(temporary) if arg == str(output) else arg for arg in cmd]
    try:
        subprocess.run(actual, check=True, capture_output=True, text=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    result = {**ktx2_header(output), "bytes": output.stat().st_size,
              "sha256": sha(output), "layers": len(inputs), "command": cmd, "inputs": source_hashes}
    sidecar.write_text(json.dumps(result, indent=2) + '\n')
    return result
