"""Model accounting in offline tests must never write the shared production ledger."""

import pytest


@pytest.fixture(autouse=True)
def isolated_codex_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_CODEX_LEDGER", str(tmp_path / "codex-ledger.jsonl"))
