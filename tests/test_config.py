"""Config round-trips, and the thing that is easy to get wrong: the API key
must not land in the file."""

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
    assert loaded.scheduled is True


def test_api_key_stays_out_of_the_config_file(sandbox):
    config_module.save(_config())
    assert "secret" not in paths.CONFIG_FILE.read_text()
    assert paths.API_KEY_FILE.read_text().strip() == "secret"
    assert config_module.load().llm.api_key == "secret"


def test_register_is_idempotent_and_sorted(sandbox):
    config = _config()
    assert config_module.register(config, Path("/b/repo")) is True
    assert config_module.register(config, Path("/a/repo")) is True
    assert config_module.register(config, Path("/a/repo")) is False
    assert config.repos == [str(Path("/a/repo")), str(Path("/b/repo"))]
    assert config_module.unregister(config, Path("/a/repo")) is True
    assert config_module.unregister(config, Path("/a/repo")) is False
