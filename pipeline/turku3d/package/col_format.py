"""Name used by docs/specs/col-format.md and ARCHITECTURE §3. The implementation is turku3d.col."""

from turku3d.col import *  # noqa: F401,F403
from turku3d.col.format import (  # noqa: F401
    ColFormatError,
    ColTile,
    Primitive,
    canonical_triangles,
    check_tile,
    read_col,
    read_header,
    write_col,
)
