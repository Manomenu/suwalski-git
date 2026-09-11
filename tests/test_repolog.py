"""`.suwgit.log` — the note suwgit leaves inside a repository it could not commit.

The point of the file is that it is where you are: you notice a project has not
been committed for days while standing in it, not while reading the daemon's
central log.
"""

import subprocess

import pytest

from suwgit import committer, gitops, repolog
from suwgit.config import Config, LlmConfig
from suwgit.llm import LlmUnavailable, Suggestion


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "a.txt").write_text("hi")
    return path


def _config(**kw):
    return Config(llm=LlmConfig(base_url="http://vllm:8000/v1", model="qwen"), **kw)


def _entries(root):
    text = (root / repolog.LOG_NAME).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def test_writes_the_blocker_and_ignores_itself(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    repolog.record_blocker(root, "could not push: remote has moved on")

    assert "could not push: remote has moved on" in _entries(root)[0]
    assert (root / ".gitignore").read_text(encoding="utf-8").splitlines() == [repolog.LOG_NAME]


def test_keeps_an_existing_gitignore_and_appends_once(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("*.pyc\n__pycache__/\n", encoding="utf-8")

    repolog.record_blocker(root, "one")
    repolog.record_blocker(root, "two")

    assert (root / ".gitignore").read_text(encoding="utf-8") == f"*.pyc\n__pycache__/\n{repolog.LOG_NAME}\n"


@pytest.mark.parametrize("existing", ["*.pyc\n.suwgit.log\n", "/.suwgit.log\n", "*.pyc\n.suwgit.log"])
def test_an_existing_ignore_rule_is_left_alone(tmp_path, existing):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text(existing, encoding="utf-8")

    assert repolog.ignore_entry(root) is False
    assert (root / ".gitignore").read_text(encoding="utf-8") == existing


def test_a_gitignore_without_a_trailing_newline_does_not_glue_lines(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("*.pyc", encoding="utf-8")

    repolog.record_blocker(root, "blocked")

    assert (root / ".gitignore").read_text(encoding="utf-8") == f"*.pyc\n{repolog.LOG_NAME}\n"


def test_the_oldest_entry_drops_off(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for i in range(repolog.MAX_ENTRIES + 3):
        repolog.record_blocker(root, f"blocker {i}")

    entries = _entries(root)
    assert len(entries) == repolog.MAX_ENTRIES
    assert "blocker 0" not in "\n".join(entries)
    assert "blocker 12" in entries[-1]


def test_the_same_blocker_repeating_is_counted_not_repeated(tmp_path):
    """A server down all night must not push out the other nine failures."""
    root = tmp_path / "repo"
    root.mkdir()
    repolog.record_blocker(root, "earlier trouble")
    for _ in range(20):
        repolog.record_blocker(root, "LLM unavailable: connection refused")

    entries = _entries(root)
    assert len(entries) == 2
    assert "earlier trouble" in entries[0]
    assert entries[1].endswith("(×20)")


def test_a_secret_blocks_and_is_written_into_the_repository(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(
        committer,
        "suggest_commit_message",
        lambda cfg, tree: Suggestion("", unsafe=True, unsafe_reason="prod.env holds a real key"),
    )

    result = committer.commit_repo(_config(), repo)

    assert not result.committed
    assert "prod.env holds a real key" in _entries(repo)[0]


def test_an_unreachable_model_is_written_into_the_repository(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")

    def boom(cfg, tree):
        raise LlmUnavailable("cannot reach http://vllm:8000/v1")

    monkeypatch.setattr(committer, "suggest_commit_message", boom)
    committer.commit_repo(_config(), repo)

    assert "cannot reach http://vllm:8000/v1" in _entries(repo)[0]


def test_a_successful_commit_removes_the_log(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    repolog.record_blocker(repo, "LLM unavailable: connection refused")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree: Suggestion("[feature] added a file"))

    assert committer.commit_repo(_config(), repo).committed
    assert not (repo / repolog.LOG_NAME).exists()
    assert (repo / ".gitignore").exists()  # the ignore rule stays


def test_waiting_for_the_edits_to_settle_is_not_a_blocker(sandbox, tmp_path):
    repo = _repo(tmp_path / "repo")

    assert committer.commit_repo(_config(), repo, quiet_seconds=3600).deferred
    assert not (repo / repolog.LOG_NAME).exists()


def test_nothing_to_commit_is_not_a_blocker(sandbox, tmp_path):
    repo = _repo(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)

    assert not committer.commit_repo(_config(), repo).committed
    assert not (repo / repolog.LOG_NAME).exists()


def test_the_log_does_not_make_the_repository_dirty(sandbox, tmp_path):
    """It is ignored, so it can never become the change that triggers a commit."""
    repo = _repo(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=False)

    repolog.record_blocker(repo, "blocked")
    status = gitops.read_working_tree(repo).status

    assert repolog.LOG_NAME not in status


def _remote_repo(tmp_path):
    """A repo whose remote does not exist, so every push fails."""
    repo = _repo(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "/nonexistent.git"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)
    return repo


def test_a_clean_repository_with_a_failing_push_still_gets_the_note(sandbox, tmp_path):
    """Nothing to commit is not the same as nothing to report: the push failed.

    Establishing the ignore rule leaves a `.gitignore` behind in a repository
    that had none. That is accepted — a blocker nobody hears about is worse
    than one ignored line, and it happens once per repository.
    """
    repo = _remote_repo(tmp_path)  # clean, one commit, unpushable remote

    result = committer.commit_repo(_config(push=True), repo)

    assert result.push_error
    assert "could not push" in _entries(repo)[0]
    assert (repo / ".gitignore").read_text(encoding="utf-8").splitlines() == [repolog.LOG_NAME]


def test_a_clean_repository_still_gets_the_note_once_the_rule_is_there(sandbox, tmp_path):
    """With the log already ignored, writing one changes nothing git can see."""
    repo = _remote_repo(tmp_path)
    (repo / ".gitignore").write_text(f"{repolog.LOG_NAME}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ignore"], check=True)

    assert committer.commit_repo(_config(push=True), repo).push_error
    assert "could not push" in _entries(repo)[0]
    assert not gitops.read_working_tree(repo).is_dirty


def test_committing_by_hand_clears_the_note(sandbox, tmp_path):
    """suwgit never sees your own `git commit` — it sees the empty tree it leaves."""
    repo = _repo(tmp_path / "repo")
    repolog.record_blocker(repo, "LLM unavailable: connection refused")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "done by hand"], check=True)

    result = committer.commit_repo(_config(), repo)

    assert result.reason == "nothing to commit"
    assert not (repo / repolog.LOG_NAME).exists()


def test_a_rule_from_git_info_exclude_counts_as_ignored(tmp_path):
    """check-ignore, not our own reading of .gitignore — so no line is added twice."""
    repo = _repo(tmp_path / "repo")
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(f"{repolog.LOG_NAME}\n", encoding="utf-8")

    assert repolog.is_ignored(repo)
    assert repolog.ignore_entry(repo) is False
    assert not (repo / ".gitignore").exists()
