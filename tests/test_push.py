"""Pushing, against a real bare remote in tmp_path.

The rules that matter: a failed push never undoes the commit, nothing is ever
forced, and a branch the daemon created still reaches the remote.
"""

import subprocess
from pathlib import Path

import pytest

from suwgit import committer, gitops
from suwgit.config import Config, LlmConfig
from suwgit.llm import Suggestion


def _remote(path):
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    return path


def _clone(remote, path):
    subprocess.run(["git", "clone", "-q", str(remote), str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    return path


def _config(push=True):
    return Config(llm=LlmConfig(base_url="http://vllm:8000/v1", model="qwen"), push=push)


def _remote_log(remote, ref="HEAD"):
    out = subprocess.run(["git", "-C", str(remote), "log", "--oneline", ref], capture_output=True, text=True, check=False)
    return out.stdout


@pytest.fixture
def repo_with_remote(tmp_path):
    remote = _remote(tmp_path / "remote.git")
    work = _clone(remote, tmp_path / "work")
    (work / "a.txt").write_text("hi")
    gitops.commit_all(work, "[chore] init")
    subprocess.run(["git", "-C", str(work), "push", "-q", "-u", "origin", "HEAD"], check=True)
    return remote, work


def test_commit_and_push_reaches_the_remote(sandbox, repo_with_remote, monkeypatch):
    remote, work = repo_with_remote
    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    result = committer.commit_repo(_config(push=True), work)

    assert result.committed and result.pushed
    assert "[feature] added b" in _remote_log(remote)


def test_push_off_leaves_the_commit_local(sandbox, repo_with_remote, monkeypatch):
    remote, work = repo_with_remote
    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    result = committer.commit_repo(_config(push=False), work)

    assert result.committed
    assert not result.pushed
    assert "[feature] added b" not in _remote_log(remote)


def test_a_rejected_push_keeps_the_commit(sandbox, repo_with_remote, monkeypatch):
    """Someone else pushed first. The commit is still ours; the push waits."""
    remote, work = repo_with_remote
    other = _clone(remote, work.parent / "other")
    (other / "theirs.txt").write_text("theirs")
    gitops.commit_all(other, "[chore] theirs")
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)

    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    result = committer.commit_repo(_config(push=True), work)

    assert result.committed  # the commit stands
    assert result.push_error  # and the failure is reported, not swallowed
    assert not result.pushed
    assert "[feature] added b" not in _remote_log(remote)
    assert "[chore] theirs" in _remote_log(remote)  # never force-pushed over them


def test_an_unreachable_remote_is_not_fatal(sandbox, repo_with_remote, monkeypatch):
    _remote_unused, work = repo_with_remote
    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin", "/nonexistent/remote.git"], check=True)
    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    result = committer.commit_repo(_config(push=True), work)

    assert result.committed
    assert result.push_error


def test_a_new_branch_gets_its_upstream_set(sandbox, repo_with_remote, monkeypatch):
    remote, work = repo_with_remote
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "feature/x"], check=True)
    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    result = committer.commit_repo(_config(push=True), work)

    assert "set its upstream" in result.pushed
    assert gitops.upstream_of(work, "feature/x") == "origin/feature/x"
    assert "[feature] added b" in _remote_log(remote, "feature/x")


def test_a_detached_head_is_not_pushed(sandbox, repo_with_remote, monkeypatch):
    _remote_unused, work = repo_with_remote
    head = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(work), "checkout", "-q", head], check=True)
    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    result = committer.commit_repo(_config(push=True), work)

    assert result.committed
    assert "detached HEAD" in result.push_error


def test_the_call_can_override_the_config(sandbox, repo_with_remote, monkeypatch):
    remote, work = repo_with_remote
    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    result = committer.commit_repo(_config(push=False), work, push=True)

    assert result.pushed
    assert "[feature] added b" in _remote_log(remote)


def test_a_clean_repo_with_unpushed_commits_is_pushed(sandbox, repo_with_remote, monkeypatch):
    """You committed by hand; the sweep should still get it out."""
    remote, work = repo_with_remote
    (work / "b.txt").write_text("more")
    gitops.commit_all(work, "[chore] committed by hand")

    def refuse(cfg, tree):
        raise AssertionError("the model must not be asked about a clean tree")

    monkeypatch.setattr(committer, "suggest_commit_message", refuse)

    result = committer.commit_repo(_config(push=True), work)

    assert not result.committed
    assert "1 commit(s) waiting" in result.reason
    assert result.pushed
    assert "[chore] committed by hand" in _remote_log(remote)


def test_a_push_that_failed_earlier_goes_out_on_the_next_sweep(sandbox, repo_with_remote, monkeypatch):
    remote, work = repo_with_remote
    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin", "/nonexistent.git"], check=True)
    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    first = committer.commit_repo(_config(push=True), work)
    assert first.committed and first.push_error

    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin", str(remote)], check=True)
    second = committer.commit_repo(_config(push=True), work)  # tree is clean now

    assert not second.committed
    assert second.pushed
    assert "[feature] added b" in _remote_log(remote)


def test_a_clean_and_fully_pushed_repo_does_nothing(sandbox, repo_with_remote, monkeypatch):
    _remote_unused, work = repo_with_remote
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[chore] nope"))

    result = committer.commit_repo(_config(push=True), work)

    assert not result.committed
    assert not result.pushed
    assert result.reason == "nothing to commit"


def test_pending_is_not_counted_when_pushing_is_off(sandbox, repo_with_remote, monkeypatch):
    _remote_unused, work = repo_with_remote
    (work / "b.txt").write_text("more")
    gitops.commit_all(work, "[chore] committed by hand")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[chore] nope"))

    result = committer.commit_repo(_config(push=False), work)

    assert result.reason == "nothing to commit"
    assert not result.pushed


def test_a_rejection_is_explained_in_one_line(sandbox, repo_with_remote, monkeypatch):
    """git's seven lines of hints are not what a daemon log needs."""
    remote, work = repo_with_remote
    other = _clone(remote, work.parent / "other2")
    (other / "theirs.txt").write_text("theirs")
    gitops.commit_all(other, "[chore] theirs")
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)

    (work / "b.txt").write_text("more")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added b"))

    result = committer.commit_repo(_config(push=True), work)

    assert "\n" not in result.push_error  # one line, not a blob
    assert "hint:" not in result.push_error
    assert "never force-pushes" in result.push_error
    assert "non-fast-forward" in result.push_error


def test_nothing_in_the_codebase_can_force_push():
    """The guarantee, pinned: no force flag reaches git, ever."""
    source = (Path(gitops.__file__).parent).glob("*.py")
    for module in source:
        text = module.read_text()
        assert "--force" not in text, module
        assert "force-with-lease" not in text, module
