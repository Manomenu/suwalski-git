"""Refusing to commit a secret.

The daemon commits unattended, so the moment the model says "there is a key in
here" is the only moment anyone can stop it reaching the history.
"""

import subprocess

from suwgit import committer, gitops
from suwgit.config import Config, LlmConfig
from suwgit.llm import RESPONSE_SCHEMA, Suggestion, parse_structured

SAFE = '{"categories":["feature"],"description":"added a thing","unsafe_for_commit":false,"unsafe_reason":""}'
UNSAFE = (
    '{"categories":["config"],"description":"added deploy settings","unsafe_for_commit":true,'
    '"unsafe_reason":"an AWS secret access key is added in deploy/.env"}'
)


def _repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True)
    (path / "deploy.env").write_text("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY\n")
    return path


def _config(push=False):
    return Config(llm=LlmConfig(base_url="http://vllm:8000/v1", model="qwen"), push=push)


def test_the_schema_asks_for_the_verdict():
    props = RESPONSE_SCHEMA["json_schema"]["schema"]["properties"]
    assert props["unsafe_for_commit"] == {"type": "boolean"}
    assert "unsafe_for_commit" in RESPONSE_SCHEMA["json_schema"]["schema"]["required"]
    assert "unsafe_reason" in RESPONSE_SCHEMA["json_schema"]["schema"]["required"]


def test_the_verdict_is_read_off_the_answer():
    assert parse_structured(SAFE).unsafe is False
    flagged = parse_structured(UNSAFE)
    assert flagged.unsafe is True
    assert "AWS secret access key" in flagged.unsafe_reason


def test_a_flagged_change_is_not_committed(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: parse_structured(UNSAFE))

    result = committer.commit_repo(_config(), repo)

    assert result.unsafe is True
    assert result.committed is False
    assert "AWS secret access key" in result.reason
    assert gitops.read_working_tree(repo).is_dirty  # the changes are still yours to deal with
    assert not gitops.has_head(repo)  # nothing was written to history at all


def test_a_flagged_change_is_never_pushed(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    (repo / "deploy.env").write_text("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY2\n")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: parse_structured(UNSAFE))

    result = committer.commit_repo(_config(push=True), repo)

    assert result.unsafe and not result.committed
    assert not result.pushed


def test_the_refusal_is_logged_as_a_warning(sandbox, tmp_path, monkeypatch):
    from suwgit import paths

    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: parse_structured(UNSAFE))

    committer.commit_repo(_config(), repo)

    log = paths.LOG_FILE.read_text()
    assert "WARNING" in log
    assert "REFUSED to commit" in log
    assert "AWS secret access key" in log


def test_a_missing_reason_still_refuses(sandbox, tmp_path, monkeypatch):
    """A verdict with no explanation is still a verdict."""
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[chore] x", unsafe=True))

    result = committer.commit_repo(_config(), repo)

    assert result.unsafe and not result.committed
    assert "no reason given" in result.reason
