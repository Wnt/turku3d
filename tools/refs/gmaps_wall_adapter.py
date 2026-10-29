"""Install the public v4 shader sampler into the private wall scoring path."""

import hashlib
from pathlib import Path


def install():
    import facade_sampling
    import loop
    import gmaps_facade_v4

    if not hasattr(facade_sampling, '_v4_original_sampler'):
        original = facade_sampling.sampler
        facade_sampling._v4_original_sampler = original

        def combined(scene, record, triangles):
            if (record.get('kit') or {}).get('version') == 4:
                return gmaps_facade_v4.sampler(scene, record, triangles)
            return original(scene, record, triangles)

        facade_sampling.sampler = combined

    if not hasattr(loop, '_v4_original_digest'):
        old = loop.scorer_digest
        loop._v4_original_digest = old

        def digest():
            source = Path(__file__).read_bytes() + Path(gmaps_facade_v4.__file__).read_bytes()
            return hashlib.sha256(old().encode() + source).hexdigest()

        loop.scorer_digest = digest
