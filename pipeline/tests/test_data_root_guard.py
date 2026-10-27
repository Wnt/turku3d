"""The test process cannot write into the shared data root (conftest.py)."""

import os
import shutil
from pathlib import Path

import pytest
from conftest import ABSENT_ROOT, REAL_ROOTS, violations
from turku3d import config as C
from turku3d.fetch.common import Registry


def test_tests_start_on_an_absent_data_root():
    assert C.data_root() == ABSENT_ROOT and not ABSENT_ROOT.exists()
    assert str(Path.home() / "turku3d" / "data") in REAL_ROOTS


def test_writes_under_the_real_data_root_are_refused():
    probe = Path(REAL_ROOTS[0]) / "raw" / "laz" / "maanpinta" / "guard-probe.laz"
    before = len(violations)
    for attempt in (
        lambda: probe.write_bytes(b"fake"),
        lambda: open(probe, "a"),
        lambda: os.open(probe, os.O_WRONLY | os.O_CREAT),
        lambda: probe.unlink(),
        lambda: os.replace(probe, probe.with_suffix(".x")),
        lambda: shutil.rmtree(probe.parent),
    ):
        with pytest.raises(PermissionError):
            attempt()
    assert not probe.exists()
    assert len(violations) == before + 6
    del violations[before:]  # this test provoked them on purpose


def test_a_downloaded_file_is_never_replaced_by_local_content(tmp_path):
    reg = Registry(tmp_path / "laz")
    path = tmp_path / "laz" / "maanpinta" / "t.laz"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"LASF real")
    reg.record(path, url="https://turku.asiointi.fi/3d/maanpinta/t.laz")
    path.write_bytes(b"fake")
    with pytest.raises(PermissionError):
        reg.record(path, url="test")
    path.write_bytes(b"LASF newer")
    reg.record(path, url="https://turku.asiointi.fi/3d/maanpinta/t.laz")  # a new remote version
    assert reg.get(path)["bytes"] == len(b"LASF newer")
