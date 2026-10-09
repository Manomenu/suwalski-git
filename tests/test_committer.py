"""The daemon must never crash on one bad repository, and must never race
`suwgit commit` over the same index."""

import subprocess

from suwgit import committer, gitops
from suwgit.config import Config, LlmConfig
from suwgit.llm import LlmUnavailable, Suggestion
from suwgit.locking import repo_lock


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "a.txt").write_text("hi")
    return path


def _config():
    return Config(llm=LlmConfig(base_url="http://vllm:8000/v1", model="qwen"))


def test_commits_with_the_suggested_message(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added a file"))

    result = committer.commit_repo(_config(), repo)

    assert result.committed
    assert result.message == "[feature] added a file"
    assert gitops.read_working_tree(repo).is_dirty is False


def test_a_prefix_goes_in_front_of_the_message(sandbox, tmp_path, monkeypatch):
    """`suwgit push -p EH-3311111` — the ticket id leads, the model's message follows."""
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] example"))

    result = committer.commit_repo(_config(), repo, prefix="EH-3311111")

    assert result.message == "EH-3311111 [feature] example"
    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"], capture_output=True, text=True, check=True)
    assert log.stdout.strip() == "EH-3311111 [feature] example"


def test_the_prefix_reaches_the_cli(sandbox, tmp_path, monkeypatch):
    from suwgit import config as config_module
    from suwgit import main

    repo = _repo(tmp_path / "repo")
    seen = {}
    monkeypatch.setattr(config_module, "load", _config)
    monkeypatch.setattr(
        main, "commit_repo", lambda cfg, root, push, prefix: seen.update(prefix=prefix, push=push) or committer.Result(root, False, "x")
    )

    main.main(["push", "-p", "EH-3311111", str(repo)])
    assert seen == {"prefix": "EH-3311111", "push": True}
    main.main(["commit", "--prefix", "EH-1", str(repo)])
    assert seen["prefix"] == "EH-1"


def test_unreachable_llm_leaves_the_tree_alone_and_does_not_raise(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")

    def boom(cfg, tree):
        raise LlmUnavailable("cannot reach http://vllm:8000/v1")

    monkeypatch.setattr(committer, "suggest_commit_message", boom)

    result = committer.commit_repo(_config(), repo)

    assert result.committed is False
    assert "LLM unavailable" in result.reason
    assert gitops.read_working_tree(repo).is_dirty is True


def test_clean_repository_is_skipped_without_calling_the_model(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")

    def refuse(cfg, tree):
        raise AssertionError("the model must not be asked about a clean tree")

    monkeypatch.setattr(committer, "suggest_commit_message", refuse)

    result = committer.commit_repo(_config(), repo)
    assert result.committed is False
    assert result.reason == "nothing to commit"


def test_interrupted_rebase_is_skipped(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    (repo / "a.txt").write_text("changed")
    (repo / ".git" / "MERGE_HEAD").write_text("deadbeef")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[chore] nope"))

    result = committer.commit_repo(_config(), repo)
    assert result.committed is False
    assert "MERGE_HEAD" in result.reason


def test_a_locked_repository_is_left_to_the_other_process(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[chore] should not happen"))

    with repo_lock(repo):
        result = committer.commit_repo(_config(), repo)

    assert result.committed is False
    assert "already working" in result.reason


def test_a_sweep_reports_commits_and_pushes_separately(sandbox, tmp_path, monkeypatch):
    from suwgit import config as config_module
    from suwgit import sweep

    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    monkeypatch.setattr(config_module, "load", lambda: Config(repos=[str(repo)]))

    assert sweep.run() == (0, 0, 0)
