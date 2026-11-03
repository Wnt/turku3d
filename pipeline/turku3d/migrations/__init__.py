"""Tile migrations: see ``core`` for the format and ``registry`` for the chain."""

from __future__ import annotations

from pathlib import Path

from .core import Migration, Product, chain_generations, normalise, run, write_in_place


def chain():
    from .registry import CHAIN

    keys = [m.key for m in CHAIN]
    names = [m.name for m in CHAIN]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate migration names in the chain: {keys}")
    return CHAIN


def apply_in_place(out: Path, manifest: dict) -> dict:
    """Generator hook: the registered chain over a freshly built product in ``out``.

    Returns the migrated manifest; the caller writes manifest.json. The manifest is normalised
    first so a transform sees exactly what it sees when it reads a stored manifest.json."""
    product = Product(Path(out), normalise(manifest))
    run(product, chain())
    write_in_place(product)
    return product.manifest


__all__ = ["Migration", "Product", "apply_in_place", "chain", "chain_generations"]
