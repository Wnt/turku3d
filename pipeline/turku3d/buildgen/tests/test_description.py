import copy
import json
from pathlib import Path

import pytest
from turku3d.buildgen import description

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["stone-apartment.json", "wooden-house.json"])
def test_fixture_descriptions_are_valid(name):
    assert description.errors(json.loads((FIXTURES / name).read_text())) == []


def test_minimal_description_is_valid_and_reports_priors():
    doc = {"schema": description.SCHEMA_ID, "id": "b1"}
    assert description.errors(doc) == []
    assert description.unobserved(doc)[0].startswith("no described wall")


def test_broken_references_and_row_lengths_are_reported():
    doc = copy.deepcopy(json.loads((FIXTURES / "stone-apartment.json").read_text()))
    wall = doc["walls"][0]
    wall["storeys"][0]["row"] = ["arch", "nosuch"]
    wall["plinth"]["material"] = "missing"
    wall["src"] = "p9"
    problems = "\n".join(description.errors(doc))
    assert "opening type 'nosuch' is not defined" in problems
    assert "material 'missing' is not defined" in problems
    assert "provenance 'p9' is not defined" in problems
    assert "row has 2 cells for 6 bays" in problems


def test_expand_storeys_unrolls_count():
    doc = json.loads((FIXTURES / "stone-apartment.json").read_text())
    storeys = description.expand_storeys(doc["walls"][0]["storeys"])
    assert [s["kind"] for s in storeys] == ["ground", "upper", "upper", "upper", "upper"]
    assert all("count" not in s for s in storeys)


def test_example_in_the_spec_is_valid():
    text = (description.SCHEMA_PATH.parent / "building-description.md").read_text()
    block = text.split("```json\n", 1)[1].split("```", 1)[0]
    assert description.errors(json.loads(block)) == []
