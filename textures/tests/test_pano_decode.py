import concurrent.futures as cf

import numpy as np
from PIL import Image
from turkutex.streetview.panos import PanoCache


def test_workers_share_exact_read_only_decoded_pixels(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    rng = np.random.default_rng(41)
    Image.fromarray(rng.integers(0, 256, (64, 128, 3), dtype=np.uint8)).save(source)
    with Image.open(source) as image:
        expected = np.asarray(image.convert("RGB"))
    calls = []
    original = Image.open

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(Image, "open", counted)
    cache = PanoCache(tmp_path / "cache")
    with cf.ThreadPoolExecutor(4) as workers:
        images = list(workers.map(cache.decoded_image, [source] * 8))
    assert len(calls) == 1
    for image in images:
        np.testing.assert_array_equal(image, expected)
        assert isinstance(image, np.memmap) and not image.flags.writeable
    assert len(list((tmp_path / "cache/decoded").glob("*.npy"))) == 1
    assert not list((tmp_path / "cache/decoded").glob("*.tmp"))


def test_decoded_cache_invalidates_changed_source(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "red").save(source)
    cache = PanoCache(tmp_path / "cache")
    first = cache.decoded_image(source)
    Image.new("RGB", (9, 8), "blue").save(source)
    second = cache.decoded_image(source)
    assert first.shape == (8, 8, 3) and second.shape == (8, 9, 3)
    np.testing.assert_array_equal(second[0, 0], [0, 0, 255])
