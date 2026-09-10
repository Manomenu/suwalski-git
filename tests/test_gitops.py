"""Real git repositories in tmp_path — the walking-up and dirty-tree rules
are the ones `suwgit commit <relative-path>` stands on."""

import subprocess

import pytest

from suwgit import gitops


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    return path


def test_finds_the_closest_root_from_a_nested_path(tmp_path):
    outer = _repo(tmp_path / "outer")
    inner = _repo(outer / "vendor" / "inner")
    deep = inner / "src" / "app"
    deep.mkdir(parents=True)

    assert gitops.find_repo_root(deep) == inner.resolve()
    assert gitops.find_repo_root(outer) == outer.resolve()


def test_finds_the_root_from_a_file(tmp_path):
    repo = _repo(tmp_path / "repo")
    target = repo / "a.txt"
    target.write_text("hi")
    assert gitops.find_repo_root(target) == repo.resolve()


def test_outside_any_repository_says_where_it_looked(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(gitops.GitError, match="walked up from"):
        gitops.find_repo_root(plain)


def test_missing_path_is_an_error(tmp_path):
    with pytest.raises(gitops.GitError):
        gitops.find_repo_root(tmp_path / "nope")


def test_clean_repository_is_not_dirty(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "a.txt").write_text("hi")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    assert gitops.read_working_tree(repo).is_dirty is False


def test_untracked_files_show_up_in_the_diff_we_send(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "new.py").write_text("print('hello')\n")
    tree = gitops.read_working_tree(repo)
    assert tree.is_dirty
    assert "new.py" in tree.diff
    assert "print('hello')" in tree.diff


def test_commit_all_stages_everything(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "a.txt").write_text("hi")
    (repo / "sub").mkdir()
    (repo / "sub" / "b.txt").write_text("there")

    short = gitops.commit_all(repo, "[feature] added two files")
    assert short
    assert gitops.read_working_tree(repo).is_dirty is False
    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--pretty=%s"], capture_output=True, text=True, check=True)
    assert log.stdout.strip() == "[feature] added two files"


def test_diff_is_truncated_at_the_budget(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "big.txt").write_text("x" * 5000 + "\n")

    tree = gitops.read_working_tree(repo, max_diff_chars=200)

    assert len(tree.diff) < 300
    assert "diff truncated" in tree.diff
    assert "big.txt" in tree.status  # the filenames survive truncation


def test_a_whole_diff_is_sent_when_it_fits(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "big.txt").write_text("x" * 5000 + "\n")

    tree = gitops.read_working_tree(repo)  # the default budget is 400_000

    assert "truncated" not in tree.diff
    assert "x" * 5000 in tree.diff


def test_interrupted_merge_is_detected(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "a.txt").write_text("hi")
    gitops.commit_all(repo, "[chore] init")
    assert gitops.in_progress_operation(repo) is None

    git_dir = repo / ".git"
    (git_dir / "MERGE_HEAD").write_text("deadbeef")
    assert gitops.in_progress_operation(repo) == "MERGE_HEAD"


def test_remotes(tmp_path):
    repo = _repo(tmp_path / "repo")
    assert gitops.remotes(repo) == []
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@example.com:x/y.git"], check=True)
    assert gitops.remotes(repo) == ["origin"]


def test_files_inside_a_new_directory_are_visible(tmp_path):
    """git collapses an untracked directory to one line; the model needs the files."""
    repo = _repo(tmp_path / "repo")
    (repo / "keep.txt").write_text("x")
    gitops.commit_all(repo, "[chore] init")

    nested = repo / "deploy" / "conf"
    nested.mkdir(parents=True)
    (nested / "settings.py").write_text("TIMEOUT = 30\n")

    tree = gitops.read_working_tree(repo)

    assert "deploy/conf/settings.py" in tree.status
    assert "TIMEOUT = 30" in tree.diff
