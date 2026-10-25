"""Checks that the shared specs in docs/specs agree with config.py and are internally consistent."""

import re

from turku3d import config as C

REQUIRED = {
    "unknown", "road", "sidewalk", "curb", "grass", "quay", "stair_slope",
    "stair_tread_helper", "wall", "building", "fence", "water",
}


def material_rows():
    text = (C.SPECS_DIR / "materials.md").read_text()
    rows = re.findall(r"^\|\s*(\d+)\s*\|\s*`([a-z0-9_]+)`\s*\|\s*(\w+)\s*\|", text, re.M)
    return [(int(i), n, cls) for i, n, cls in rows]


def test_material_table():
    rows = material_rows()
    ids = [r[0] for r in rows]
    names = [r[1] for r in rows]
    assert ids == list(range(len(ids))), "IDs must be contiguous from 0"
    assert len(set(names)) == len(names)
    assert REQUIRED <= set(names)
    assert all(cls in ("solid", "query", "sensor") for _, _, cls in rows)
    assert max(ids) < 200
    d = {n: (i, cls) for i, n, cls in rows}
    assert d["unknown"][0] == 0
    assert d["stair_tread_helper"][1] == "query"
    assert d["water"][1] == "sensor"


def test_col_header_table_is_96_bytes():
    text = (C.SPECS_DIR / "col-format.md").read_text()
    sizes = {"char[4]": 4, "u16": 2, "u32": 4, "i32": 4, "f64": 8, "f32[3]": 12, "f32[4]": 16, "u8": 1}

    def table(title):
        sect = text.split(title, 1)[1].split("\n### ", 1)[0].split("\n## ", 1)[0]
        return re.findall(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|", sect, re.M)

    for title, total in (("### 2.1 Header", 96), ("### 2.2 Section table entry", 24),
                         ("## 4. Compound primitives", 48)):
        off = 0
        for o, t in table(title):
            assert int(o) == off, (title, o, t)
            off += sizes[t]
        assert off == total, title


def test_tiles_spec_mentions_config_values():
    text = (C.SPECS_DIR / "tiles.md").read_text()
    assert "23459500" in text and "6704500" in text
    assert "62.5 m" in text and "250 m" in text
    for t in C.OFFICE_BLOCK.visual_tiles:
        assert t.id in text
    assert C.OFFICE_BLOCK.laz_tiles[0].name in text
