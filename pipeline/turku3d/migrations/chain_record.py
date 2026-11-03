"""chain-record@1: the first proof migration. It changes no payload; the framework records the
chain (``migrations``) in the manifest, so a served tile shows which edits it carries."""

from .core import Migration, Product


def transform(product: Product) -> None:
    del product  # The chain record is written by the framework for every migration.


MIGRATION = Migration("chain-record", 1, transform)
