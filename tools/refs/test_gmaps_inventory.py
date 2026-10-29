"""Durable reference pointers cannot escape the inventory or invent missing evidence."""

from gmaps_inventory import relocate


def test_relocates_existing_nested_pointer(tmp_path):
    report = tmp_path / "synthetic/captures/quarter/report.json"
    report.parent.mkdir(parents=True)
    report.write_text("immutable score")
    old = "/deleted/work/gmaps-round3/synthetic/captures/quarter/report.json"
    assert relocate({"quarters": [{"report": old}]}, tmp_path) == {
        "quarters": [{"report": str(report)}]
    }
    assert report.read_text() == "immutable score"


def test_missing_external_and_traversal_pointers_stay_unresolved(tmp_path):
    for value in (
        "/deleted/work/gmaps-round3/missing.json",
        "/unrelated/report.json",
        f"/deleted/gmaps-round3/../{tmp_path.name}",
        "relative/gmaps-round3/report.json",
    ):
        assert relocate(value, tmp_path) == value


def test_inventory_requires_complete_audit_and_existing_bodies(tmp_path):
    import json
    from gmaps_inventory import inventory

    root = tmp_path / 'synthetic'
    root.mkdir()
    (root / 'expanded-plan.json').write_text(json.dumps(dict(quarters=[dict(coverage_id='q', tile='t0_0_0')])))
    capture = root / 'captures/q'
    (capture / 'responses').mkdir(parents=True)
    (capture / 'capture.json').write_text(json.dumps(dict(
        inventory_state='eligible', responses=[dict(file='responses/body.bin')],
        detail_progress=dict(complete=True, metadata_complete=True, available=1, loaded=1))))
    assert inventory(tmp_path)['states'] == dict(incomplete=1)
    (capture / 'responses/body.bin').write_bytes(b'observed payload')
    result = inventory(tmp_path)
    assert result['states'] == dict(eligible=1)
    assert result['quarters']['q']['capture'] == 'synthetic/captures/q/capture.json'
