import shapely
from turku3d.coarse import span_lamps as S
from turku3d.migrations import registry


def record(wires, lamps=()):
    return {"version": 1, "kinds": ["span", "utility", "power"], "wires": [list(w) for w in wires],
            "lamps": [list(lamp) for lamp in lamps]}


# A lit street along x at z = 0; span wires across it from facade to facade (z -14 .. 14).
STREET = [shapely.LineString([(-200, 0), (200, 0)])]


def span(x, z0=-14.0, z1=14.0, y=9.0, sag=0.4, kind=0):
    return (x, y, z0, x, y, z1, sag, kind)


def lamps_of(result):
    updated, _ = result
    return [] if updated is None else updated["lamps"]


def test_a_span_wire_crossing_a_lit_street_without_its_lamp_gets_one_over_the_centreline():
    wires = [span(0), span(30), span(60)]
    updated, stats = S.infer(record(wires, [(0, 8.6, 0.2, 0), (60, 8.6, -0.3, 2)]), STREET)
    assert updated["lamps"][2:] == [[30.0, 8.6, 0.0, 1]]
    assert updated["wires"] == [list(w) for w in wires]
    assert stats == {"span": 3, "lampless": 1, "inferred": 1, "extended": 0, "low": 0}


def test_a_lamp_hung_near_the_wire_carries_it_whoever_lists_it():
    # A neighbour tile's lamp (wire -1) on this wire, or a register lamp hung over the street.
    assert S.infer(record([span(30)], [(30.4, 8.6, 0.5, -1)]), STREET)[0] is None
    assert S.infer(record([span(30)]), STREET, hung=[(31.0, 0.3)])[0] is None
    # A lamp 3 m off the wire hangs on another span, but it lights this crossing already.
    assert S.infer(record([span(30)], [(33.0, 8.6, 0.0, -1)]), STREET)[0] is None
    assert lamps_of(S.infer(record([span(30)], [(35.0, 8.6, 0.0, -1)]), STREET))[1:] == [[30.0, 8.6, 0.0, 0]]


def test_a_span_piece_over_the_median_reaches_both_carriageways_of_a_divided_street():
    # Carriageways 20 m apart; the survey kept 8 m of the span over the median and a footway.
    divided = [shapely.LineString([(-200, -10), (200, -10)]), shapely.LineString([(-200, 10), (200, 10)])]
    updated, stats = S.infer(record([span(40, z0=-4, z1=4, sag=0.1)]), divided)
    assert updated["lamps"] == [[40.0, 9.0, -10.0, 0], [40.0, 9.0, 10.0, 0]]
    assert updated["wires"][0][:6] == [40.0, 9.0, -10.0, 40.0, 9.0, 10.0]
    assert stats["extended"] == 1
    # Beyond EXTEND past its ends a piece belongs to something else.
    assert S.infer(record([span(40, z0=1, z1=4)]), [shapely.LineString([(-200, -10), (200, -10)])])[0] is None


def test_only_span_wires_crossing_a_lit_street():
    wires = [
        span(10, kind=1),        # utility line: no lamp to infer
        span(20, z0=12, z1=30),  # a yard behind the street: 12 m past its end
        span(50, z0=-6, z1=10),  # across the street, off centre: the lamp hangs where the sag puts it
    ]
    updated, stats = S.infer(record(wires), STREET)
    assert updated["lamps"] == [[50.0, 8.62, 0.0, 2]]
    assert stats["span"] == 2 and stats["lampless"] == 2
    assert S.infer(record(wires), [])[0] is None


def test_a_wire_too_low_over_the_street_for_a_lighting_span_gets_no_lamp():
    wires = [span(10, y=25.0, sag=0.1), span(40, y=30.0, sag=0.1)]
    updated, stats = S.infer(record(wires), STREET, ground=lambda x, z: 20.0)
    assert updated["lamps"] == [[40.0, 29.9, 0.0, 1]]
    assert stats["low"] == 1
    # Past its surveyed end a span keeps the end's height.
    sloped = (60, 9.0, -4.0, 60, 10.0, 4.0, 0.0, 0)
    updated, _ = S.infer(record([sloped]), [shapely.LineString([(-200, 10), (200, 10)])])
    assert updated["lamps"] == [[60.0, 10.0, 10.0, 0]] and updated["wires"][0][3:6] == [60.0, 10.0, 10.0]


def test_the_rule_runs_last_in_the_chain():
    assert registry.CHAIN[-1].key == "span-lamps@1"
