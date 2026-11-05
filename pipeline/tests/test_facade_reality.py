"""Source-driven facade rules found by the street-level patrol against panoramas (2026-11-05)."""

from turkutex.facade_archetypes import archetype, recipe
from turkutex.facade_sections import sections
from turkutex.wall_palette import sample

from turku3d.facade_components import components
from turku3d.facade_inference import building_material, section_material_support
from turku3d.facade_programs import names_windows, reading_head


def reading(*sections, confidence=None):
    return dict(
        observation=dict(needs_review=False),
        structure=dict(render_deferred="partial",
                       facade_observations=dict(sections=list(sections), confidence=confidence or {})),
    )


def test_a_blind_panel_detail_never_blanks_a_named_window_row():
    row = dict(position="main", kind="single tall row of flat windows and boarded openings",
               features=["central triangular pediment above blind boarded panel"])
    parts, _ = sections(dict(facade_observations=dict(sections=[row])), 1)
    assert all(p["kind"] != "blank" for p in parts)
    field = dict(position="main", features=["broad blank field", "partial window at left"])
    parts, _ = sections(dict(facade_observations=dict(sections=[field])), 1)
    assert [p["kind"] for p in parts] == ["blank"]
    assert names_windows(reading(row)) and not names_windows(reading(dict(kind="windowless gable")))


def test_a_stone_reading_keyed_by_its_material_sets_the_section_and_building_family():
    wall = reading(dict(position="wall and top", material="large rough stone blocks", head="flat"),
                   confidence=dict(stone=0.99))
    parts, _ = sections(wall["structure"], 4)
    assert parts[0]["material"] == "stone" and parts[0]["bounds"] == [0.0, 0.0, 1.0, 1.0]
    assert section_material_support(wall, parts[0]) == 0.99
    family = building_material({}, {}, dict(walls=[wall]))
    assert family["value"] == "stone" and family["support"] == 0.99
    # Register cladding outranks the description's material guess; readings outrank both.
    assert building_material({"material_class": "wood"}, {}, None, {"material": "plaster"})["value"] == "wood"
    assert reading_head(dict(walls=[wall])) == "flat"


def test_one_named_balcony_row_is_a_level_with_its_counted_balconies():
    form = dict(facade_observations=dict(sections=[
        dict(position="middle row", features=["Two projecting metal balconies"]),
        dict(position="top row", features=["Two groups behind left balcony"]),
    ]))
    found, pending = components(form, 4)
    assert [(c["kind"], c["rows"], c["count"]) for c in found] == [("balcony", 1, 2)]
    assert pending


def test_register_use_dwellings_and_evidence_only_stone():
    shed = dict(osm_use="yes", use="1360", footprint_m2=20, levels=1, construction_year=1972)
    assert archetype(shed) == "warehouse"
    home = dict(osm_use="house", material_class="metal", levels=1, footprint_m2=80, compactness=0.8)
    assert archetype(home) != "warehouse" and archetype(dict(home, blank=True)) != "warehouse"
    assert archetype(dict(osm_use="school", material_class="wood", construction_year=1910)) == "timber_traditional"
    for i in range(60):
        assert recipe(f"b{i}", dict(construction_year=1890))["material"] == "plaster"


def test_wood_paint_is_multimodal_not_one_brown_mean():
    colours = {sample(f"wood-{i}", dict(material_class="wood", era="1850-1919")) for i in range(200)}
    reds = [c for c in colours if int(c[1:3], 16) - int(c[5:7], 16) > 60]
    yellows = [c for c in colours if int(c[1:3], 16) - int(c[5:7], 16) > 40 and c not in reds]
    greys = [c for c in colours if abs(int(c[1:3], 16) - int(c[5:7], 16)) < 15]
    assert reds and yellows and greys
    assert sample("wood-7", dict(material_class="wood")) == sample("wood-7", dict(material_class="wood"))
