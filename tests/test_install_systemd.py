"""The systemd unit gives the daemon the same PATH the user's shell has, so the
git hooks it runs find their tools (gitleaks in ~/.nix-profile/bin, say)."""

from suwgit import install_systemd as install
from suwgit import paths


def test_the_unit_carries_the_shells_path(monkeypatch):
    monkeypatch.setenv("PATH", "/home/u/.nix-profile/bin:/usr/bin")
    unit = install.render_service()
    assert 'Environment="PATH=/home/u/.nix-profile/bin:/usr/bin"\n' in unit
    assert f"ExecStart={paths.ENTRYPOINT} daemon\n" in unit


def test_a_percent_sign_is_escaped_for_systemd(monkeypatch):
    monkeypatch.setenv("PATH", "/opt/100%/bin:/usr/bin")
    assert 'Environment="PATH=/opt/100%%/bin:/usr/bin"\n' in install.render_service()


def test_install_writes_the_unit_and_enables_it(sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(install, "_systemctl", lambda *args: calls.append(args) or (0, ""))

    ok, detail = install.install_schedule(1.5, True)

    assert ok and "enabled and running" in detail
    assert paths.SERVICE_FILE.read_text().startswith("[Unit]")
    assert calls == [("daemon-reload",), ("enable", "--now", "suwgit.service")]


def test_schedule_off_writes_the_unit_and_disables_it(sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(install, "_systemctl", lambda *args: calls.append(args) or (0, ""))

    ok, detail = install.install_schedule(1.5, False)

    assert ok and "autostart off" in detail
    assert calls[-1] == ("disable", "--now", "suwgit.service")


def test_the_launcher_is_a_symlink_and_a_real_file_is_never_deleted(sandbox):
    paths.BIN_DIR.mkdir(parents=True, exist_ok=True)
    paths.BIN_LINK.write_text("#!/bin/sh\necho not ours\n")

    linked, detail = install.link_into_path()
    assert not linked and "not a symlink" in detail

    result = install.unlink_from_path()
    assert paths.BIN_LINK.exists()
    assert "left" in result[0]
