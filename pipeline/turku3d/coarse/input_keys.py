"""Prepared dataset identities exclude unrelated input orchestration and producers."""

import ast
import hashlib
from functools import lru_cache
from pathlib import Path

from turku3d import config as C

HELPERS = {
    "ground": ("grid_axes", "grid_heights", "ground_points", "prepare_ground"),
    "ortho": ("read_ortho",),
    "wfs": ("Building", "_frame", "buildings", "prepare_wfs"),
    "roads": ("prepare_roads",),
    "street_profiles": ("_frame",),
    "boats": ("Building", "_frame", "buildings"),
    "attachments": ("Building", "_frame", "buildings"),
    "soffits": ("Building", "_frame", "buildings"),
    "trees": ("prepare_trees",),
}


@lru_cache(maxsize=2)
def definitions(source):
    return {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }


def _selected(dataset):
    """The ``_prepare_one`` statements and inputs.py helpers that produce one dataset."""
    from . import inputs

    nodes = definitions(Path(inputs.__file__).read_bytes())
    prefix, branch, fallback = [], None, []
    seen_dispatch = False
    for node in nodes["_prepare_one"].body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            test = node.test
            if isinstance(test.left, ast.Name) and test.left.id == "dataset":
                seen_dispatch = True
                if ast.literal_eval(test.comparators[0]) == dataset:
                    branch = node.body
                continue
        (fallback if seen_dispatch else prefix).append(node)
    return [
        *prefix,
        *(branch if branch is not None else fallback),
        *(nodes[name] for name in HELPERS.get(dataset, ())),
    ]


def producer_code(dataset):
    from . import inputs

    dataset = dataset.removesuffix("-first")
    selected = _selected(dataset)
    constants = ({"ground": (inputs.CELLS, inputs.POINT_MARGIN), "ortho": (inputs.ORTHO_PX,)}).get(
        dataset, ()
    )
    # Registering a helper for one dataset must not invalidate every other dataset.
    # Hash the identity algorithm, then only the selected producer definitions below.
    own = definitions(Path(__file__).read_bytes())
    h = hashlib.sha256(ast.dump(own['producer_code'], include_attributes=False).encode())
    h.update(ast.dump(own['definitions'], include_attributes=False).encode())
    h.update(ast.dump(own['_selected'], include_attributes=False).encode())
    h.update(Path(C.__file__).read_bytes())
    h.update(repr(constants).encode())
    for node in selected:
        h.update(ast.dump(node, include_attributes=False).encode())
    if dataset == 'wfs':
        from . import street_materials

        # WFS assigns visual material IDs; texture pixels do not affect surveyed shells.
        finish = definitions(Path(street_materials.__file__).read_bytes())['street_finish']
        h.update(ast.dump(finish, include_attributes=False).encode())
        h.update(repr(sorted(street_materials.FINISH_KEYS.items())).encode())
    return h.digest()


def closure_code(dataset):
    """Hash every local definition the producer can reach, plus its declared data files.

    Edits to unrelated functions in shared modules keep the prepared dataset; any
    reachable definition, constant, import or resource change replaces it.
    """
    from turku3d.source_code import Closure

    from . import inputs

    dataset = dataset.removesuffix("-first")
    path = Path(inputs.__file__)
    closure = Closure(path.parents[2])
    for node in _selected(dataset):
        closure.seed(path, node)
    h = hashlib.sha256(closure.digest())
    for name in inputs.RESOURCES.get(dataset, ()):
        h.update(name.encode() + b"\0" + (path.parent / name).read_bytes())
    return h.digest()
