"""`suwgit init` is re-runnable: pressing Enter everywhere keeps what is configured."""

from pathlib import Path

from suwgit import config as config_module
from suwgit import initcmd, install


def _run(monkeypatch, answers, windows=False):
    """Run init with these answers, in order; Enter for every question past the list."""
    monkeypatch.setattr(initcmd.host, "is_windows", lambda: windows)  # Linux also asks where the config lives
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": queue.pop(0) if queue else "")
    monkeypatch.setattr(install, "link_into_path", lambda: (True, "linked"))
    monkeypatch.setattr(install, "install_schedule", lambda hours, enabled: (True, "installed"))
    monkeypatch.setattr(install, "path_contains_local_bin", lambda: True)
    assert initcmd.run() == 0
    return config_module.load()


def _existing():
    config = config_module.Config(
        llm=config_module.LlmConfig(base_url="http://vllm:8000/v1", model="qwen", api_key="secret"),
        interval_hours=1.0,
        push=True,
        report_author="Piotr Gugnowski",
    )
    config_module.register(config, Path("/a/repo"), default_tenant="lokum")
    return config


def test_enter_everywhere_keeps_the_whole_config(sandbox, monkeypatch):
    config_module.save(_existing(), config_module.paths.CONFIG_FILE)
    loaded = _run(monkeypatch, ["2"])  # keep the config in the home directory

    assert (loaded.llm.base_url, loaded.llm.model, loaded.llm.api_key) == ("http://vllm:8000/v1", "qwen", "secret")
    assert (loaded.interval_hours, loaded.push, loaded.report_author) == (1.0, True, "Piotr Gugnowski")
    assert loaded.entry(Path("/a/repo")).default_tenant == "lokum"


def test_the_report_author_can_be_changed(sandbox, monkeypatch):
    config_module.save(_existing(), config_module.paths.CONFIG_FILE)
    answers = ["2", "", "", "", "", "", "", "Jan Kowalski"]  # location, url, model, key, hours, autostart, push, author
    assert _run(monkeypatch, answers).report_author == "Jan Kowalski"


def test_a_first_run_offers_the_git_user_name(sandbox, monkeypatch):
    monkeypatch.setattr(initcmd.gitops, "global_user_name", lambda: "Piotr Gugnowski")
    answers = ["2", "http://vllm:8000/v1", "qwen"]
    assert _run(monkeypatch, answers).report_author == "Piotr Gugnowski"


def test_dotfiles_mode_keeps_the_config_when_stow_folded_the_whole_folder(sandbox, monkeypatch):
    """stow may link ~/.config/suwgit itself, not the file inside it. Then the
    home path and the dotfiles path are one file, and it must not be "tidied away"."""
    dotfiles_dir = config_module.paths.DOTFILES_CONFIG_FILE.parent
    dotfiles_dir.mkdir(parents=True)
    config_module.paths.CONFIG_FILE.parent.parent.mkdir(parents=True)
    config_module.paths.CONFIG_FILE.parent.symlink_to(dotfiles_dir)
    config_module.save(_existing(), config_module.paths.DOTFILES_CONFIG_FILE)

    loaded = _run(monkeypatch, ["1"])  # keep the config in the dotfiles

    assert config_module.paths.DOTFILES_CONFIG_FILE.exists()
    assert loaded.report_author == "Piotr Gugnowski"


def test_windows_is_not_asked_where_the_config_lives(sandbox, monkeypatch):
    """The dotfiles tree is a Linux (GNU Stow) setup: on Windows the first answer is already the server."""
    config_module.save(_existing())
    loaded = _run(monkeypatch, ["http://other:8000/v1"], windows=True)

    assert loaded.llm.base_url == "http://other:8000/v1"
    assert config_module.paths.CONFIG_FILE.exists()


def test_the_schedule_gets_the_interval_and_the_switch(sandbox, monkeypatch):
    seen = []
    monkeypatch.setattr(install, "install_schedule", lambda hours, enabled: seen.append((hours, enabled)) or (True, "installed"))
    monkeypatch.setattr(install, "link_into_path", lambda: (True, "linked"))
    monkeypatch.setattr(install, "path_contains_local_bin", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    monkeypatch.setattr(initcmd.host, "is_windows", lambda: True)
    config_module.save(_existing())

    assert initcmd.run() == 0
    assert seen == [(1.0, True)]
