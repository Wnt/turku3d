"""External-buffer assets and malformed chunk boundaries, per glTF 2.0 section 4.4."""
import json
import struct

import pytest

from turku3d.package.gltf import read_glb


def container(chunks):
    body = b"".join(struct.pack("<II", len(data), kind) + data for kind, data in chunks)
    return struct.pack("<III", 0x46546C67, 2, len(body) + 12) + body


def document(value):
    data = json.dumps(value).encode()
    return 0x4E4F534A, data + b" " * (-len(data) % 4)


def test_glb_external_buffers_need_no_bin_chunk():
    value = {"asset": {"version": "2.0"}, "buffers": [{"uri": "geometry.bin", "byteLength": 4}]}
    assert read_glb(container([document(value)])) == (value, b"")


def test_glb_embedded_buffer_and_unknown_extension():
    value = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}]}
    data = container([document(value), (0x004E4942, b"abcd"), (0x12345678, b"extra   ")])
    assert read_glb(data) == (value, b"abcd")


@pytest.mark.parametrize("value", [None, [], 12])
def test_glb_rejects_non_object_document(value):
    with pytest.raises(ValueError, match="object"):
        read_glb(container([document(value)]))


@pytest.mark.parametrize("chunks", [
    [(0x004E4942, b"abcd")],
    [document({}), document({})],
    [document({}), (0x12345678, b"abcd"), (0x004E4942, b"abcd")],
    [document({}), (0x004E4942, b"abc")],
])
def test_glb_rejects_invalid_chunk_layout(chunks):
    with pytest.raises(ValueError):
        read_glb(container(chunks))


def test_glb_rejects_truncated_chunk_instead_of_slicing_silently():
    data = bytearray(container([document({}), (0x004E4942, b"abcd")]))
    data = data[:-1]
    struct.pack_into("<I", data, 8, len(data))
    with pytest.raises(ValueError, match="chunk length"):
        read_glb(data)
    with pytest.raises(ValueError, match="header"):
        read_glb(b"glTF")
