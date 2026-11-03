"""roof-classes@1: roof ortho crops resampled up to power-of-two square size classes (``coarse.roof_classes``).

Placement models whose ``roof_ortho`` crop is not a square power of two get a resampled crop (next class of its
longer side: never fewer texels) in the shared store and a rewritten model GLB; model ids follow their content and
the tile's placements and block LODs follow the ids. The client stacks a block's same-class roofs in one texture
array and batches them as one draw. No manifest key changes."""

from turku3d.coarse import roof_classes

from .core import Migration, Product


def transform(product: Product) -> None:
    roof_classes.classify_models(product)


MIGRATION = Migration("roof-classes", 1, transform)
