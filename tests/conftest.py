import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from suwgit import logs, paths


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every suwgit path at a throwaway directory."""
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config" / "suwgit")
    monkeypatch.setattr(paths, "CONFIG_FILE", tmp_path / "config" / "suwgit" / "config.json")
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state" / "suwgit")
    monkeypatch.setattr(paths, "LOG_FILE", tmp_path / "state" / "suwgit" / "suwgit.log")
    monkeypatch.setattr(paths, "LOCK_DIR", tmp_path / "state" / "suwgit" / "locks")
    monkeypatch.setattr(paths, "API_KEY_FILE", tmp_path / "state" / "suwgit" / "api_key")
    monkeypatch.setattr(paths, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(paths, "BASH_SHIM", tmp_path / "bin" / "suwgit")
    monkeypatch.setattr(paths, "CMD_SHIM", tmp_path / "bin" / "suwgit.cmd")
    # Never the real Task Scheduler from a test.
    monkeypatch.setattr(paths, "TASK_NAME", "suwgit-test-never-registered")
    # The logger caches its handler; without this it stays bound to an earlier
    # test's sandbox and writes there instead.
    monkeypatch.setattr(logs, "_logger", None)
    return tmp_path
