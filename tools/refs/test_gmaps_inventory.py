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
