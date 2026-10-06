"""The systemd unit gives the daemon the same PATH the user's shell has, so the
git hooks it runs find their tools (gitleaks in ~/.nix-profile/bin, say)."""

from suwgit import install, paths


def test_the_unit_carries_the_shells_path(monkeypatch):
    monkeypatch.setenv("PATH", "/home/u/.nix-profile/bin:/usr/bin")
    unit = install.render_service()
    assert 'Environment="PATH=/home/u/.nix-profile/bin:/usr/bin"\n' in unit
    assert f"ExecStart={paths.ENTRYPOINT} daemon\n" in unit


def test_a_percent_sign_is_escaped_for_systemd(monkeypatch):
    monkeypatch.setenv("PATH", "/opt/100%/bin:/usr/bin")
    assert 'Environment="PATH=/opt/100%%/bin:/usr/bin"\n' in install.render_service()
