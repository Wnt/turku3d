"""Slow source discovery must not serialize demand and unrelated tile requests."""
import threading

from turku3d.coarse.service import Builder


def test_source_discovery_releases_scheduler_condition(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def inputs(tile):
        entered.set()
        assert release.wait(2)
        return ["missing-input"]

    builder = Builder(tmp_path, code="test", workers=0, inputs=inputs)
    request = threading.Thread(target=builder.request, args=("t0_0_0", 0))
    request.start()
    try:
        assert entered.wait(1)
        assert builder.lock.acquire(timeout=.5), "HTTP input I/O retained the scheduler condition"
        builder.lock.release()
    finally:
        release.set()
        request.join(2)
        builder.stop()
