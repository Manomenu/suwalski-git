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
    assert config_module.register(config, Path("/b/repo")) == "added"
    assert config_module.register(config, Path("/a/repo")) == "added"
    assert config_module.register(config, Path("/a/repo")) == "unchanged"
    assert [e.path for e in config.repos] == [str(Path("/a/repo")), str(Path("/b/repo"))]
    assert config_module.unregister(config, Path("/a/repo")) is True
    assert config_module.unregister(config, Path("/a/repo")) is False


def test_a_new_registration_auto_commits_and_has_no_defaults(sandbox):
    config = _config()
    config_module.register(config, Path("/a/repo"))
    entry = config.entry(Path("/a/repo"))
    assert (entry.auto_commit, entry.default_tenant, entry.default_project) == (True, "", "")


def test_registering_again_changes_only_the_flags_given(sandbox):
    config = _config()
    config_module.register(config, Path("/a/repo"), default_tenant="lokum", default_project="Client portal")
    assert config_module.register(config, Path("/a/repo"), auto_commit=False) == "updated"
    entry = config.entry(Path("/a/repo"))
    assert (entry.auto_commit, entry.default_tenant, entry.default_project) == (False, "lokum", "Client portal")
    assert config_module.register(config, Path("/a/repo"), default_tenant="") == "updated"
    assert entry.default_tenant == ""


def test_registry_settings_round_trip(sandbox):
    config = _config()
    config_module.register(config, Path("/a/repo"), auto_commit=False, default_tenant="lokum", default_project="Portal")
    config_module.save(config)
    entry = config_module.load().entry(Path("/a/repo"))
    assert (entry.auto_commit, entry.default_tenant, entry.default_project) == (False, "lokum", "Portal")


def test_an_older_config_with_bare_paths_still_loads(sandbox):
    paths.CONFIG_FILE.parent.mkdir(parents=True)
    paths.CONFIG_FILE.write_text('{"repos": ["/a/repo"]}')
    entry = config_module.load().entry(Path("/a/repo"))
    assert (entry.auto_commit, entry.default_tenant) == (True, "")
