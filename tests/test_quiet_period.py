"""The daemon waits for a repository to go quiet before committing it.

Committing mid-edit is the failure this prevents: half a refactor, described by
a message written about work that is not finished. `suwgit commit` by hand is
exempt — asking for it is the statement that you are done.
"""

import os
import subprocess
import time

from suwgit import committer, daemon, gitops
from suwgit.config import QUIET_FRACTION, Config, LlmConfig
from suwgit.llm import Suggestion


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "a.txt").write_text("hi")
    return path


def _config(**kw):
    return Config(llm=LlmConfig(base_url="http://vllm:8000/v1", model="qwen"), **kw)


def _age(path, seconds):
    """Backdate a file so it looks like it was last touched `seconds` ago."""
    when = time.time() - seconds
    os.utime(path, (when, when))


def _never_asked(cfg, tree):
    raise AssertionError("the model must not be asked about changes that are still warm")


def test_warm_changes_are_deferred(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", _never_asked)

    result = committer.commit_repo(_config(), repo, quiet_seconds=3600)

    assert not result.committed
    assert result.deferred
    assert gitops.read_working_tree(repo).is_dirty


def test_settled_changes_are_committed(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    _age(repo / "a.txt", 7200)
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added a file"))

    result = committer.commit_repo(_config(), repo, quiet_seconds=3600)

    assert result.committed
    assert not result.deferred


def test_the_newest_file_decides(sandbox, tmp_path, monkeypatch):
    """One file still being typed into holds back the whole repository."""
    repo = _repo(tmp_path / "repo")
    (repo / "b.txt").write_text("still writing this")
    _age(repo / "a.txt", 7200)
    monkeypatch.setattr(committer, "suggest_commit_message", _never_asked)

    assert committer.commit_repo(_config(), repo, quiet_seconds=3600).deferred


def test_manual_commit_ignores_the_quiet_period(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")  # just written, as warm as it gets
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added a file"))

    result = committer.commit_repo(_config(), repo)

    assert result.committed


def test_a_deleted_file_does_not_hold_a_repository_back(sandbox, tmp_path, monkeypatch):
    """A deletion cannot be dated, so it must not count as recent activity."""
    repo = _repo(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)
    (repo / "a.txt").unlink()
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[remove] dropped a file"))

    assert committer.commit_repo(_config(), repo, quiet_seconds=3600).committed


def test_quiet_period_is_three_quarters_of_the_interval(sandbox):
    assert Config(interval_hours=1.5).quiet_seconds == 1.5 * 3600 * QUIET_FRACTION
    assert QUIET_FRACTION == 0.75


def test_sweep_reports_deferred_repositories(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    config = _config(repos=[str(repo)])
    monkeypatch.setattr(daemon.config_module, "load", lambda: config)
    monkeypatch.setattr(committer, "suggest_commit_message", _never_asked)

    assert daemon.run_once() == (0, 0, 1)


def test_renamed_paths_are_dated_by_their_new_name(sandbox, tmp_path):
    repo = _repo(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)
    subprocess.run(["git", "-C", str(repo), "mv", "a.txt", "b.txt"], check=True)
    _age(repo / "b.txt", 7200)

    status = gitops.read_working_tree(repo).status
    idle = gitops.seconds_since_last_change(repo, status)

    assert idle is not None
    assert idle > 3600
