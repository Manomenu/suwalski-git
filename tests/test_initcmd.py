"""`suwgit init` is re-runnable: pressing Enter everywhere keeps what is configured."""

from pathlib import Path

from suwgit import config as config_module
from suwgit import initcmd, install


def _run(monkeypatch, answers):
    """Run init with these answers, in order; Enter for every question past the list."""
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": queue.pop(0) if queue else "")
    monkeypatch.setattr(install, "link_into_path", lambda: (True, "linked"))
    monkeypatch.setattr(install, "install_task", lambda hours, enabled: (True, "installed"))
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
    config_module.save(_existing())
    loaded = _run(monkeypatch, [])

    assert (loaded.llm.base_url, loaded.llm.model, loaded.llm.api_key) == ("http://vllm:8000/v1", "qwen", "secret")
    assert (loaded.interval_hours, loaded.push, loaded.report_author) == (1.0, True, "Piotr Gugnowski")
    assert loaded.entry(Path("/a/repo")).default_tenant == "lokum"


def test_the_report_author_can_be_changed(sandbox, monkeypatch):
    config_module.save(_existing())
    answers = ["", "", "", "", "", "", "Jan Kowalski"]  # url, model, key, hours, schedule, push, author
    assert _run(monkeypatch, answers).report_author == "Jan Kowalski"


def test_a_first_run_offers_the_git_user_name(sandbox, monkeypatch):
    monkeypatch.setattr(initcmd.gitops, "global_user_name", lambda: "Piotr Gugnowski")
    answers = ["http://vllm:8000/v1", "qwen"]
    assert _run(monkeypatch, answers).report_author == "Piotr Gugnowski"
