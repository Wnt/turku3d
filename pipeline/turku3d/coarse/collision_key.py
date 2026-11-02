"""Structural producer freshness survives independent facade and texture deployments."""

import ast
import hashlib
from functools import lru_cache
from pathlib import Path

from .packaging import producer_paths

VISUAL_ONLY = {
    "ground_finishes.py",
    "packaging.py",
    "facade_assets.py",
    "facade_programs.py",
    "facade_inference.py",
    "facade_style_geometry.py",
    "facade_program_geometry.py",
    "facade_relief.py",
    # Reading-driven components are full-pass enrichment, never first-pass geometry.
    "facade_components.py",
    "facade_component_geometry.py",
    "facade_sections.py",
    "facade_learning.py",
    "facade_learned_layout.py",
    "facade_structure.py",
    "facade_program.py",
    "facade_archetypes.py",
}


def paths():
    return sorted(
        {
            Path(__file__),
            *(
                p
                for p in producer_paths()
                if (p.parent.name != "package" or p.name == "gltf.py") and p.name not in VISUAL_ONLY
            ),
        },
        key=lambda p: p.as_posix().rsplit("/pipeline/", 1)[-1],
    )


def structural_source(path):
    source = path.read_bytes()
    if path.name == "stored_facades.py" and path.parent.name == "coarse":
        # Both passes use wall ownership; only the full pass binds facade paint.
        functions = [
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"exposed_wall_quads", "assemble_walls"}
        ]
        return ast.dump(
            ast.Module(body=functions, type_ignores=[]), include_attributes=False
        ).encode()
    if path.name not in {"build.py", "stages.py"} or path.parent.name != "coarse":
        return source
    nodes = ast.parse(source).body
    selected = []
    for node in nodes:
        if not isinstance(node, ast.FunctionDef):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                selected.append(node)
            continue
        if path.name == "stages.py":
            if node.name in {
                "deck_stage",
                "kerb_inputs",
                "cached_terrain",
                "terrain_stage",
                "structures_stage",
            }:
                selected.append(node)
            continue
        if node.name in {"jpeg", "ortho_uv", "default_library"}:
            continue
        if node.name == "build":
            prefix = []
            for statement in node.body:
                if (
                    isinstance(statement, ast.If)
                    and isinstance(statement.test, ast.Name)
                    and statement.test.id == "first_pass"
                ):
                    prefix.append(statement)
                    break
                prefix.append(statement)
            if len(prefix) == len(node.body):
                raise ValueError("collision boundary missing from build")
            node.body = prefix
        selected.append(node)
    return ast.dump(ast.Module(body=selected, type_ignores=[]), include_attributes=False).encode()


def code_hash():
    digest = hashlib.sha256()
    for path in paths():
        digest.update(path.name.encode())
        digest.update(structural_source(path))
    return digest.hexdigest()[:16]


@lru_cache(maxsize=2)
def _current(signature):
    return code_hash()


def current_code():
    signature = []
    for path in paths():
        stat = path.stat()
        signature.append((str(path), stat.st_size, stat.st_mtime_ns))
    return _current(tuple(signature))
