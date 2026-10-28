"""Structural source admission remains independent of decorative data arrivals."""

from test_coarse_prefetch import _real_raw, raw  # noqa: F401 (shared isolated raw fixture)
from turku3d import config as C
from turku3d.coarse.sources import Sources, WFS_LAYERS, block_of, tile_blocks


def test_structural_inputs_do_not_wait_for_orthophoto_or_signs(raw, tmp_path):
    from turku3d.coarse.inputs import gather

    tile = C.VisualTile(0, 1, 1)
    _real_raw(tile)
    sources = Sources()
    sources._listed = {b.name for b in tile_blocks(tile)}
    ortho = sources.ortho_source(tile)
    saved = ortho.read_bytes()
    ortho.unlink()
    decorative = C.raw_dir('wfs') / f'coarse-{block_of(tile).name}' / (WFS_LAYERS[-1].replace(':', '_') + '.summary.json')
    summary = decorative.read_bytes()
    decorative.unlink()
    sources.invalidate()
    assert {u.dataset for u in sources.missing_for_service(tile)} == {'ortho', 'wfs'}
    assert sources.missing_for_service(tile, first_pass=True) == []
    key = sources.input_key(tile, first_pass=True)
    assert key and sources.input_key(tile) is None
    prepared = gather(tile, tmp_path / 'coarse', sources, first_pass=True)
    assert prepared.key == key and prepared.buildings and prepared.roads
    ortho.write_bytes(saved)
    decorative.write_bytes(summary)
    sources.invalidate()
    assert sources.input_key(tile, first_pass=True) == key
    assert sources.input_key(tile) is not None
