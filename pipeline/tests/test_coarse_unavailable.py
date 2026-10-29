"""Unsupported ground stays unavailable until producer/input identity changes."""

import time
from unittest.mock import Mock

from turku3d.coarse import service


def test_unavailable_blocks_expired_retry_and_structural_upgrade_until_inputs_change(
    tmp_path, monkeypatch
):
    key = ["raw-v1"]
    builder = service.Builder(
        tmp_path, run=Mock(), code="generator", workers=0, input_key=lambda _: key[0]
    )
    builder.two_pass = True
    builder.built = lambda _: False
    tile = "t0_21_-2"
    error = "turku3d.coarse.sources.NoCoverage: t0_21_-2: only 0 ground key points"
    builder.failed[tile] = (error, time.time() - 2 * service.RETRY_SECONDS)
    builder.failure_codes[tile] = builder.failure_key(tile)
    monkeypatch.setattr(service, "collision_current", lambda *_: {"pass_kind": "first"})
    # A prior structural fragment must not bypass the recorded full unavailability.
    for _ in range(3):
        assert builder._request(tile, 0) == dict(state="failed", error=error)
        assert tile not in builder.upgrade_queue
        assert tile not in builder.priority
    key[0] = "raw-v2"
    assert builder._request(tile, 0) == dict(state="queued")
    assert tile in builder.upgrade_queue


def test_genuine_expired_failure_still_retries(tmp_path):
    builder = service.Builder(tmp_path, run=Mock(), code="generator", workers=0)
    tile = "t0_0_0"
    builder.failed[tile] = (
        "ValueError: malformed geometry",
        time.time() - 2 * service.RETRY_SECONDS,
    )
    builder.failure_codes[tile] = builder.failure_key(tile)
    assert builder._request(tile, 0)["state"] == "queued"
    assert tile in builder.priority
