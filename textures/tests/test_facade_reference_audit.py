"""Pure paired-reference audit decisions."""

from turkutex.facade_reference_audit import eligible, missing_readings


def reading(**updates):
    result = dict(
        usable=True,
        wrong_wall=False,
        multiple_surfaces=False,
        occlusion_share=0.1,
    )
    result.update(updates)
    return result


def test_second_review_only_needed_for_first_review_positives():
    rows = [dict(facade_id=fid) for fid in ("good", "blocked", "wrong", "missing")]
    first = {
        "good": reading(),
        "blocked": reading(usable=False),
        "wrong": reading(wrong_wall=True),
    }
    assert missing_readings(rows, first, {}) == ["good", "missing"]
    assert missing_readings(rows, first, {"good": reading(usable=False)}) == ["missing"]


def test_foreground_threshold_is_inclusive():
    assert eligible(reading(occlusion_share=0.2))
    assert not eligible(reading(occlusion_share=0.21))
