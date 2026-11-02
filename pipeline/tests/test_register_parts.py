from turku3d.register_parts import collect, extract, source_paths


def page(reverse=False):
    # Synthetic source: the final child is a projection, while its sibling is a
    # recess. Neither the building footprint nor address point is a projection.
    line = "3,0 1,0" if reverse else "1,0 3,0"
    return f'''<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs"
      xmlns:gml="http://www.opengis.net/gml" xmlns:k="urn:test">
      <gml:featureMember><k:Rakennus><k:rakennustunnus>B1</k:rakennustunnus>
      <k:alue><gml:Polygon><gml:outerBoundaryIs><gml:LinearRing>
      <gml:coordinates>0,0 4,0 4,4 0,4 0,0</gml:coordinates>
      </gml:LinearRing></gml:outerBoundaryIs></gml:Polygon></k:alue>
      <k:osa><k:RakennuksenOsa><k:tyyppi>sisennys</k:tyyppi><k:kplkoodi>66520</k:kplkoodi>
      <k:reunaviiva><gml:LineString><gml:coordinates>{line}</gml:coordinates></gml:LineString></k:reunaviiva>
      </k:RakennuksenOsa></k:osa>
      <k:osa><k:RakennuksenOsa><k:tyyppi>uloke</k:tyyppi><k:kplkoodi>66516</k:kplkoodi>
      <k:reunaviiva><gml:LineString><gml:coordinates>1,4 3,4</gml:coordinates></gml:LineString></k:reunaviiva>
      </k:RakennuksenOsa></k:osa>
      <k:osoite><gml:Point><gml:coordinates>2,2</gml:coordinates></gml:Point></k:osoite>
      </k:Rakennus></gml:featureMember></wfs:FeatureCollection>'''.encode()


def test_nested_part_owns_its_type_and_never_absorbs_building_or_address_geometry():
    rows = list(extract(page()))
    assert [owner for owner, _ in rows] == ["B1", "B1"]
    assert [(p["kind"], p["register_code"]) for _, p in rows] == [("recess", "66520"), ("projection", "66516")]
    assert all(p["height_m"] is None for _, p in rows)


def test_overlapping_pages_and_reversed_lines_keep_one_owned_fact(tmp_path):
    a, b = tmp_path / "a.gml", tmp_path / "b.gml"
    a.write_bytes(page()); b.write_bytes(page(reverse=True))
    index = collect([b, a, a])
    assert len(index["buildings"]["B1"]) == 2
    assert index == collect([a, b])
    assert a.read_bytes() == page() and b.read_bytes() == page(reverse=True)


def test_shared_cache_root_follows_area_symlinks_and_excludes_other_layers(tmp_path):
    area = tmp_path / "source-area"
    buildings = area / "gml/kanta_Rakennus"
    buildings.mkdir(parents=True)
    source = buildings / "page.gml"
    source.write_bytes(page())
    other = area / "gml/kanta_Puu"
    other.mkdir()
    (other / "page.gml").write_bytes(b"not building data")
    cache = tmp_path / "wfs"
    cache.mkdir()
    (cache / "area").symlink_to(area, target_is_directory=True)
    assert source_paths([cache, area, buildings, source]) == [source.resolve()]
    assert source_paths([tmp_path / "missing"]) == []
