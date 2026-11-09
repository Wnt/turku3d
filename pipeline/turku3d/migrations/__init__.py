"""Tile migrations: see ``core`` for the format and ``registry`` for the chain."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from .core import (Migration, MissingRebuild, Product, Rebuild, chain_generations, normalise, run,
                   write_in_place)


def chain():
    from .registry import CHAIN

    keys = [m.key for m in CHAIN]
    names = [m.name for m in CHAIN]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate migration names in the chain: {keys}")
    return CHAIN


def apply_in_place(out: Path, manifest: dict, *, measure=None) -> dict:
    """Generator hook: the registered chain over a freshly built product in ``out``.

    Returns the migrated manifest; the caller writes manifest.json. The manifest is normalised
    first so a transform sees exactly what it sees when it reads a stored manifest.json.
    Optional stage measurement records each step and publication outside the product manifest."""
    product = Product(Path(out), normalise(manifest))
    run(product, chain(), measure=measure)
    with measure("migration:publish") if measure else nullcontext():
        write_in_place(product)
    return product.manifest


__all__ = ["Migration", "MissingRebuild", "Product", "Rebuild", "apply_in_place", "chain",
           "chain_generations"]
