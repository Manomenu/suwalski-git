"""Config round-trips, and the two things that are easy to get wrong:
the API key must not land in the file, and a save must follow a stow symlink."""

from pathlib import Path

from suwgit import config as config_module
from suwgit import paths


def _config():
    return config_module.Config(
        llm=config_module.LlmConfig(base_url="http://vllm:8000/v1", model="qwen", api_key="secret"),
        interval_hours=1.5,
    )


def test_round_trip(sandbox):
    config_module.save(_config())
    loaded = config_module.load()
    assert loaded.llm.base_url == "http://vllm:8000/v1"
    assert loaded.interval_hours == 1.5
    assert loaded.interval_seconds == 5400
    assert loaded.open_on_system_start is True


def test_api_key_stays_out_of_the_config_file(sandbox):
    config_module.save(_config())
    assert "secret" not in paths.CONFIG_FILE.read_text()
    assert paths.API_KEY_FILE.read_text().strip() == "secret"
    assert paths.API_KEY_FILE.stat().st_mode & 0o777 == 0o600
    assert config_module.load().llm.api_key == "secret"


def test_save_follows_the_stow_symlink(sandbox):
    dotfiles_copy = paths.DOTFILES_CONFIG_FILE
    config_module.save(_config(), dotfiles_copy)
    paths.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.CONFIG_FILE.symlink_to(dotfiles_copy)

    config = config_module.load()
    config.interval_hours = 3
    written = config_module.save(config)

    assert written == dotfiles_copy
    assert not paths.CONFIG_FILE.is_file() or paths.CONFIG_FILE.is_symlink()
    assert '"interval_hours": 3' in dotfiles_copy.read_text()


def test_dotfiles_copy_is_read_before_stow_has_run(sandbox):
    config_module.save(_config(), paths.DOTFILES_CONFIG_FILE)
    assert not paths.CONFIG_FILE.exists()
    assert config_module.load().llm.model == "qwen"


def test_register_is_idempotent_and_sorted(sandbox):
    config = _config()
    assert config_module.register(config, Path("/b/repo")) is True
    assert config_module.register(config, Path("/a/repo")) is True
    assert config_module.register(config, Path("/a/repo")) is False
    assert config.repos == ["/a/repo", "/b/repo"]
    assert config_module.unregister(config, Path("/a/repo")) is True
    assert config_module.unregister(config, Path("/a/repo")) is False
