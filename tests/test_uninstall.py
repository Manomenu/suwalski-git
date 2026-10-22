"""Uninstall removes what suwgit made and nothing else — and above all it keeps
the config, because reinstalling must not cost the vLLM settings."""

from suwgit import paths, uninstall


def _installed(sandbox):
    """Everything a real install leaves behind, in the sandbox."""
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    paths.LOCK_DIR.mkdir(parents=True, exist_ok=True)
    paths.LOG_FILE.write_text("2026-09-10 21:00:00  INFO     started\n")
    paths.API_KEY_FILE.write_text("secret\n")
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    paths.CONFIG_FILE.write_text('{"repos": []}')
    paths.DOTFILES_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.DOTFILES_CONFIG_FILE.write_text('{"repos": []}')


def test_the_config_survives_a_plain_uninstall(sandbox, monkeypatch):
    _installed(sandbox)
    monkeypatch.setattr(uninstall.install, "uninstall_schedule", list)
    monkeypatch.setattr(uninstall.install, "unlink_from_path", list)

    assert uninstall.run(assume_yes=True) == 0

    assert paths.CONFIG_FILE.exists()
    assert paths.DOTFILES_CONFIG_FILE.exists()
    assert paths.API_KEY_FILE.exists()
    assert not paths.LOG_FILE.exists()
    assert not paths.LOCK_DIR.exists()


def test_purge_takes_the_config_and_the_key_too(sandbox, monkeypatch):
    _installed(sandbox)
    monkeypatch.setattr(uninstall.install, "uninstall_schedule", list)
    monkeypatch.setattr(uninstall.install, "unlink_from_path", list)

    assert uninstall.run(purge=True, assume_yes=True) == 0

    assert not paths.CONFIG_FILE.exists()
    assert not paths.DOTFILES_CONFIG_FILE.exists()
    assert not paths.API_KEY_FILE.exists()
    assert not paths.STATE_DIR.exists()


def test_the_confirmation_defaults_to_no(sandbox, monkeypatch):
    _installed(sandbox)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")  # a bare enter

    assert uninstall.run(purge=True) == 1
    assert paths.CONFIG_FILE.exists()
    assert paths.LOG_FILE.exists()


def test_anything_other_than_yes_aborts(sandbox, monkeypatch):
    _installed(sandbox)
    for answer in ("n", "no", "Y E S", "sure", "yolo"):
        monkeypatch.setattr("builtins.input", lambda _prompt, a=answer: a)
        assert uninstall.run(purge=True) == 1
    assert paths.CONFIG_FILE.exists()


def test_y_proceeds(sandbox, monkeypatch):
    _installed(sandbox)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.setattr(uninstall.install, "uninstall_schedule", list)
    monkeypatch.setattr(uninstall.install, "unlink_from_path", list)

    assert uninstall.run() == 0
    assert not paths.LOG_FILE.exists()


def test_a_clean_machine_is_a_no_op(sandbox, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest_should_not_be_called())

    def pytest_should_not_be_called():
        raise AssertionError("must not ask when there is nothing to remove")

    assert uninstall.run() == 0


def test_a_real_file_on_the_path_is_never_deleted(sandbox, monkeypatch):
    """Only our own symlink goes; a binary someone else put there stays."""
    from suwgit import install

    paths.BIN_DIR.mkdir(parents=True, exist_ok=True)
    paths.BIN_LINK.write_text("#!/bin/sh\necho not ours\n")

    result = install.unlink_from_path()

    assert paths.BIN_LINK.exists()
    assert "left" in result[0]


def test_every_launcher_and_the_schedule_are_listed_and_removed(sandbox, monkeypatch, capsys):
    _installed(sandbox)
    paths.BIN_DIR.mkdir(parents=True, exist_ok=True)
    launcher = paths.BIN_DIR / "suwgit-launcher"
    launcher.write_text("x")
    done = []
    monkeypatch.setattr(uninstall.install, "launchers", lambda: [launcher])
    monkeypatch.setattr(uninstall.install, "schedule_label", lambda: "scheduled task 'suwgit'")
    monkeypatch.setattr(uninstall.install, "uninstall_schedule", lambda: done.append("schedule") or [])
    monkeypatch.setattr(uninstall.install, "unlink_from_path", lambda: done.append("launchers") or [])

    assert uninstall.run(assume_yes=True) == 0

    out = capsys.readouterr().out
    assert str(launcher) in out and "scheduled task 'suwgit'" in out
    assert done == ["schedule", "launchers"]
