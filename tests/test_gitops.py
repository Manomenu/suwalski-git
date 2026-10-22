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


def _git_run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit_bytes(repo, name, data, message):
    (repo / name).write_bytes(data)
    _git_run(repo, "add", "-A")
    _git_run(repo, "commit", "-qm", message)


def test_output_that_is_not_utf8_does_not_crash(tmp_path):
    """A Windows-1252 ellipsis (0x85) in an old commit once made every later commit fail."""
    repo = _repo(tmp_path / "repo")
    _commit_bytes(repo, "notes.md", b"wait\x85 what\n", "[docs] legacy encoding")
    (repo / "notes.md").write_bytes(b"wait\x85 what\nmore\n")

    tree = gitops.read_working_tree(repo)

    assert "more" in tree.diff
    assert gitops.exact_revert_of(repo) is None


def test_an_exact_undo_of_a_non_utf8_commit_is_still_found(tmp_path):
    repo = _repo(tmp_path / "repo")
    _commit_bytes(repo, "a.txt", b"one\n", "[chore] init")
    _commit_bytes(repo, "notes.md", b"wait\x85\n", "[docs] add notes")
    (repo / "notes.md").unlink()

    found = gitops.exact_revert_of(repo)

    assert found is not None
    assert found[1] == "[docs] add notes"


def test_a_different_non_utf8_byte_is_not_an_undo(tmp_path):
    """Bytes are compared as bytes: 0x86 is not the 0x85 that was added, though both decode to U+FFFD."""
    repo = _repo(tmp_path / "repo")
    _commit_bytes(repo, "a.txt", b"one\n", "[chore] init")
    _commit_bytes(repo, "n.md", b"x\x85\n", "[docs] add notes")
    (repo / "n.md").write_bytes(b"x\x86\n")
    assert gitops.exact_revert_of(repo) is None
    (repo / "n.md").unlink()
    assert gitops.exact_revert_of(repo) is not None


def test_exact_revert_does_not_diff_commits_that_cannot_match(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    for index in range(5):
        _commit_bytes(repo, f"f{index}.txt", b"x\n", f"[chore] add {index}")
    (repo / "other.txt").write_text("unrelated\n")
    patches = []
    real = gitops._patch
    monkeypatch.setattr(gitops, "_patch", lambda *a: patches.append(a) or real(*a))

    assert gitops.exact_revert_of(repo) is None
    assert patches == []  # only the cheap path/line-count comparison ran


def test_a_deleted_lockfile_does_not_fill_the_prompt(tmp_path):
    from suwgit import llm

    repo = _repo(tmp_path / "repo")
    (repo / "uv.lock").write_text("".join(f'[[package]]\nname = "pkg{i}"\nversion = "1.{i}"\n\n' for i in range(4400)))
    (repo / "app.py").write_text("print(1)\n")
    gitops.commit_all(repo, "[chore] init")
    (repo / "uv.lock").unlink()
    (repo / "app.py").write_text("print(2)\n")

    tree = gitops.read_working_tree(repo, max_diff_chars=20_000)
    prompt = llm._user_prompt(tree)

    assert len(prompt) < 22_000
    assert "uv.lock: deleted file, +0 -17600 lines" in tree.diff
    assert "+print(2)" in tree.diff
    assert "pkg4399" not in prompt


def test_lockfiles_generated_and_binary_files_are_one_line_each(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / ".gitattributes").write_text("gen.txt linguist-generated\nraw.txt -diff\n")
    for name in ("package-lock.json", "gen.txt", "raw.txt", "logo.bin", "app.py"):
        (repo / name).write_bytes(b"\0\1\2\n" if name == "logo.bin" else b"a\n")
    gitops.commit_all(repo, "[chore] init")
    for name in ("package-lock.json", "gen.txt", "raw.txt", "logo.bin", "app.py"):
        (repo / name).write_bytes(b"\0\3\4\n" if name == "logo.bin" else b"b\n")

    diff = gitops.read_working_tree(repo).diff

    assert "package-lock.json: lockfile, +1 -1 lines" in diff
    assert "gen.txt: generated file" in diff
    assert "raw.txt: generated file" in diff
    assert "logo.bin: binary file" in diff
    assert "+b" in diff
    assert diff.count("+b") == 1  # app.py only


def test_a_new_lockfile_and_a_new_binary_are_not_previewed(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "keep.txt").write_text("x")
    gitops.commit_all(repo, "[chore] init")
    (repo / "yarn.lock").write_text("a\nb\n")
    (repo / "pic.png").write_bytes(b"\x89PNG\0\0")

    diff = gitops.read_working_tree(repo).diff

    assert "yarn.lock: new lockfile or generated file, 2 lines" in diff
    assert "pic.png: new binary file" in diff
    assert "--- new file" not in diff


def test_truncation_tells_the_model(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "big.txt").write_text("a\n")
    gitops.commit_all(repo, "[chore] init")
    (repo / "big.txt").write_text("line\n" * 5000)
    diff = gitops.read_working_tree(repo, max_diff_chars=500).diff
    assert "diff truncated at 500 characters" in diff


def test_a_stale_origin_head_does_not_make_the_default_branch_look_like_a_feature(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "a.txt").write_text("x")
    gitops.commit_all(repo, "[chore] init")
    _git_run(repo, "remote", "add", "origin", "git@github.com:owner/repo.git")
    _git_run(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    _git_run(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")

    assert gitops.merge_request_url(repo, "origin", "master", "") == ""
    assert gitops.merge_request_url(repo, "origin", "feat/x", "") == "https://github.com/owner/repo/pull/new/feat/x"
