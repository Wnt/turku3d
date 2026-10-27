from turkutex.facade_alternates import alternate_view


def test_alternate_uses_other_panorama_and_prefers_other_source():
    original = {"pano_id": "old", "source": "google"}
    views = [
        {"pano_id": "old", "source": "google", "part": "full", "score": 1},
        {"pano_id": "new", "source": "google", "part": "full", "score": 0.9},
        {"pano_id": "third", "source": "mapillary", "part": "full", "score": 0.5},
        {"pano_id": "fourth", "source": "lookaround", "part": "partial", "score": 2},
    ]
    assert alternate_view(original, views)["pano_id"] == "third"
    assert alternate_view(original, views[:1]) is None


def test_historical_choice_excludes_newest_date():
    views = [
        {"pano_id": "new", "source": "google", "part": "full", "date": "2026-07"},
        {"pano_id": "old", "source": "google", "part": "full", "date": "2017-06"},
    ]
    assert alternate_view(None, views, older_than_newest=True)["pano_id"] == "old"
