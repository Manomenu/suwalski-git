"""`install.py` hands every call to the implementation of the system it runs on."""

import pytest

from suwgit import install, install_systemd, install_windows, paths


@pytest.mark.parametrize(
    ("windows", "expected"),
    [(False, install_systemd), (True, install_windows)],
)
def test_the_backend_follows_the_system(monkeypatch, windows, expected):
    monkeypatch.setattr(install.host, "is_windows", lambda: windows)
    assert install.backend() is expected


@pytest.mark.parametrize("windows", [False, True])
def test_both_backends_offer_the_same_functions(windows, monkeypatch):
    monkeypatch.setattr(install.host, "is_windows", lambda: windows)
    backend = install.backend()
    for name in (
        "link_into_path",
        "unlink_from_path",
        "launchers",
        "install_schedule",
        "uninstall_schedule",
        "schedule_status",
        "schedule_label",
        "AUTOSTART_QUESTION",
        "PATH_HINT",
    ):
        assert hasattr(backend, name), name


def test_calls_reach_the_backend_of_the_system(monkeypatch):
    seen = []
    monkeypatch.setattr(install.host, "is_windows", lambda: True)
    monkeypatch.setattr(install_windows, "install_schedule", lambda hours, enabled: seen.append((hours, enabled)) or (True, "ok"))
    assert install.install_schedule(2.0, False) == (True, "ok")
    assert seen == [(2.0, False)]


def test_each_system_names_its_own_scheduler(monkeypatch):
    monkeypatch.setattr(install.host, "is_windows", lambda: False)
    assert "system" in install.autostart_question()
    monkeypatch.setattr(install.host, "is_windows", lambda: True)
    assert "Task Scheduler" in install.autostart_question()


def test_the_local_bin_is_found_on_the_path(sandbox, monkeypatch):
    monkeypatch.setenv("PATH", f"/usr/bin{__import__('os').pathsep}{paths.BIN_DIR}")
    assert install.path_contains_local_bin()
    monkeypatch.setenv("PATH", "/usr/bin")
    assert not install.path_contains_local_bin()
