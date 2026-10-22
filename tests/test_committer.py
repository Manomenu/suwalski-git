"""The daemon must never crash on one bad repository, and must never race
`suwgit commit` over the same index."""

import subprocess

from suwgit import committer, gitops
from suwgit.config import Config, LlmConfig, RepoEntry
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
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree, **_: Suggestion("[feature] added a file"))

    result = committer.commit_repo(_config(), repo)

    assert result.committed
    assert result.message == "[feature] added a file"
    assert gitops.read_working_tree(repo).is_dirty is False


def test_unreachable_llm_leaves_the_tree_alone_and_does_not_raise(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")

    def boom(cfg, tree, **_):
        raise LlmUnavailable("cannot reach http://vllm:8000/v1")

    monkeypatch.setattr(committer, "suggest_commit_message", boom)

    result = committer.commit_repo(_config(), repo)

    assert result.committed is False
    assert "LLM unavailable" in result.reason
    assert gitops.read_working_tree(repo).is_dirty is True


def test_clean_repository_is_skipped_without_calling_the_model(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")

    def refuse(cfg, tree, **_):
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
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree, **_: Suggestion("[chore] nope"))

    result = committer.commit_repo(_config(), repo)
    assert result.committed is False
    assert "MERGE_HEAD" in result.reason


def test_a_locked_repository_is_left_to_the_other_process(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree, **_: Suggestion("[chore] should not happen"))

    with repo_lock(repo):
        result = committer.commit_repo(_config(), repo)

    assert result.committed is False
    assert "already working" in result.reason


def test_a_sweep_reports_commits_and_pushes_separately(sandbox, tmp_path, monkeypatch):
    from suwgit import config as config_module
    from suwgit import daemon

    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    monkeypatch.setattr(config_module, "load", lambda: Config(repos=[RepoEntry(str(repo))]))

    assert daemon.run_once() == (0, 0, 0)


def test_a_prefix_goes_in_front_of_the_message(sandbox, tmp_path, monkeypatch):
    """`suwgit push -p EH-3311111` — the ticket id leads, the model's message follows."""
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree, **_: Suggestion("[feature] example"))

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
        main,
        "commit_repo",
        lambda cfg, root, push, prefix, **_: seen.update(prefix=prefix, push=push) or committer.Result(root, False, "x"),
    )

    main.main(["push", "-p", "EH-3311111", str(repo)])
    assert seen == {"prefix": "EH-3311111", "push": True}
    main.main(["commit", "--prefix", "EH-1", str(repo)])
    assert seen["prefix"] == "EH-1"


def test_a_hint_is_passed_to_the_model(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    (repo / "a.txt").write_text("hi")
    seen = {}

    def suggest(cfg, tree, **kwargs):
        seen.update(kwargs)
        return Suggestion("[feature] added a")

    monkeypatch.setattr(committer, "suggest_commit_message", suggest)

    committer.commit_repo(_config(), repo, hint="write it in Polish")
    assert seen["hint"] == "write it in Polish"


def test_a_lone_sentence_is_a_hint_for_the_current_folder():
    from suwgit import main

    args = main.build_parser().parse_args(["push", "say it is a hotfix"])
    assert main._path_and_hint(args) == (".", "say it is a hotfix")

    args = main.build_parser().parse_args(["push", "../other", "say it is a hotfix", "-p", "EH-1"])
    assert main._path_and_hint(args) == ("../other", "say it is a hotfix")


def test_a_bare_prefix_takes_the_ticket_id_from_the_branch(sandbox, tmp_path, monkeypatch, capsys):
    from suwgit import config as config_module
    from suwgit import main

    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    seen = {}
    monkeypatch.setattr(config_module, "load", _config)
    monkeypatch.setattr(main, "commit_repo", lambda cfg, root, **kw: seen.update(kw) or committer.Result(root, False, "x"))

    assert main.main(["commit", str(repo), "-p"]) == 1
    assert "has none" in capsys.readouterr().err

    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feature/EH-3311-csv"], check=True)
    main.main(["push", str(repo), "-p"])
    assert seen["prefix"] == "EH-3311" and seen["push_anyway"] is True
    main.main(["commit", str(repo), "-p"])
    assert seen["push_anyway"] is False


def _merge_in_progress(tmp_path, conflict):
    """A repository in the middle of merging `topic` into main; conflicting when asked."""
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("checkout", "-qb", "topic")
    (repo / "a.txt").write_text("topic")
    (repo / "b.txt").write_text("b")
    git("add", "-A")
    git("commit", "-qm", "add the topic work")
    git("checkout", "-q", "main")
    (repo / "a.txt").write_text("main" if conflict else "hi")
    git("commit", "-qam", "main moves on", "--allow-empty")
    subprocess.run(["git", "-C", str(repo), "merge", "--no-commit", "--no-ff", "topic"], check=False, capture_output=True)
    return repo


def test_a_resolved_merge_is_committed_with_both_parents(sandbox, tmp_path, monkeypatch):
    repo = _merge_in_progress(tmp_path, conflict=False)
    seen = []
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree, **_: seen.append(tree) or Suggestion("[chore] merged topic"))

    result = committer.commit_repo(_config(), repo, finish_merge=True)

    assert result.committed
    parents = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%P"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert len(parents) == 2
    assert "Merge branch 'topic'" in seen[0].merge
    assert "add the topic work" in seen[0].merge


def test_the_daemon_never_commits_a_merge(sandbox, tmp_path, monkeypatch):
    repo = _merge_in_progress(tmp_path, conflict=False)
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree, **_: Suggestion("[chore] nope"))

    result = committer.commit_repo(_config(), repo)

    assert result.committed is False
    assert "MERGE_HEAD in progress" in result.reason


def test_a_merge_with_conflicts_names_the_files(sandbox, tmp_path, monkeypatch):
    repo = _merge_in_progress(tmp_path, conflict=True)
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree, **_: Suggestion("[chore] nope"))

    result = committer.commit_repo(_config(), repo, finish_merge=True)

    assert result.committed is False
    assert "unresolved conflicts: a.txt" in result.reason


def test_other_operations_in_progress_are_still_refused_by_hand(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    (repo / "a.txt").write_text("changed")
    (repo / ".git" / "CHERRY_PICK_HEAD").write_text("deadbeef")
    monkeypatch.setattr(committer, "suggest_commit_message", lambda cfg, tree, **_: Suggestion("[chore] nope"))

    result = committer.commit_repo(_config(), repo, finish_merge=True)

    assert result.committed is False
    assert "CHERRY_PICK_HEAD in progress" in result.reason
