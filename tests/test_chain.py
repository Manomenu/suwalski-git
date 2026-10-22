"""The chain: git-town's parents in a real repository, ahead/behind from real git against a bare origin,
the merge requests from recorded glab / gh answers, and the two commands."""

import json
import subprocess

import pytest

from suwgit import chain, gitops, main, mrs


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo, name):
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)


def _parent(repo, branch, parent):
    _git(repo, "config", f"git-town-branch.{branch}.parent", parent)


@pytest.fixture
def repo(tmp_path):
    """main <- feature/a <- feature/b, all pushed; checked out on feature/b."""
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "checkout", "-q", "-b", "main")
    _commit(work, "base")
    for branch, parent in (("feature/a", "main"), ("feature/b", "feature/a")):
        _git(work, "checkout", "-q", "-b", branch)
        _commit(work, branch.replace("/", "-"))
        _parent(work, branch, parent)
    _git(work, "push", "-q", "--all", "origin")
    return work.resolve()


GITLAB_URL = "https://gitlab.example.com/team/app.git"


def _gitlab(repo):
    _git(repo, "remote", "set-url", "origin", GITLAB_URL)


def test_the_chain_is_the_git_town_parents_base_first(repo):
    assert chain.read_chain(repo) == ["main", "feature/a", "feature/b"]


def test_a_branch_without_a_parent_says_how_to_set_one(tmp_path, repo):
    _git(repo, "checkout", "-q", "-b", "loose")
    with pytest.raises(chain.ChainError, match=r"git town set-parent"):
        chain.read_chain(repo)


def test_parents_in_a_circle_are_refused(repo):
    _parent(repo, "main", "feature/b")
    with pytest.raises(chain.ChainError, match="circle"):
        chain.read_chain(repo)


def test_a_detached_head_is_refused(repo):
    _git(repo, "checkout", "-q", "--detach")
    with pytest.raises(chain.ChainError, match="detached"):
        chain.read_chain(repo)


def test_a_branch_missing_from_the_clone_is_named(repo):
    _parent(repo, "feature/b", "ghost")
    with pytest.raises(chain.ChainError, match="ghost"):
        chain.links(repo, chain.read_chain(repo))


def test_links_count_ahead_and_behind_with_real_git(repo):
    _commit(repo, "local-only")  # feature/b: one commit origin lacks
    _git(repo, "checkout", "-q", "feature/a")
    _commit(repo, "a-news")  # feature/a: now one commit feature/b lacks
    _git(repo, "checkout", "-q", "feature/b")
    _git(repo, "checkout", "-q", "-b", "feature/c")
    _commit(repo, "c1")
    _parent(repo, "feature/c", "feature/b")

    by_branch = {link.branch: link for link in chain.links(repo, chain.read_chain(repo))}

    assert by_branch["main"].parent is None
    b = by_branch["feature/b"]
    assert (b.pushed, b.ahead_of_origin, b.behind_origin) == (True, 1, 0)
    assert (b.ahead_of_parent, b.behind_parent) == (2, 1)
    c = by_branch["feature/c"]
    assert c.pushed is False
    assert (c.ahead_of_parent, c.behind_parent) == (1, 0)


def test_the_table_marks_unpushed_merge_up_and_wrong_targets(repo):
    _git(repo, "checkout", "-q", "feature/a")
    _commit(repo, "a-news")
    _git(repo, "checkout", "-q", "feature/b")
    found = {
        "feature/a": mrs.MergeRequest(11, "https://x/11", "main"),
        "feature/b": mrs.MergeRequest(12, "https://x/12", "main"),
    }
    names = chain.read_chain(repo)

    table = chain.render(chain.links(repo, names), found, mrs.GITLAB)

    lines = table.splitlines()
    assert lines[0].split() == ["branch", "origin", "parent", "merge", "request"]
    assert "!11 -> main" in lines[2] and "✗" not in lines[2]
    assert "merge up (1 new)" in lines[3]
    assert "!12 -> main" in lines[3] and "✗ should target feature/a" in lines[3]


def test_the_table_without_merge_requests_shows_question_marks(repo):
    table = chain.render(chain.links(repo, chain.read_chain(repo)), None, None)
    assert table.splitlines()[2].endswith("?")


def test_wrong_targets_ignores_branches_without_a_merge_request():
    found = {"b1": None, "b2": mrs.MergeRequest(2, "u", "b1")}
    assert chain.wrong_targets(["main", "b1", "b2"], found) == {}


# --- the commands, with glab replaced by recorded answers -----------------------------------

GITLAB_ANSWERS = {
    "feature/a": json.dumps([{"iid": 11, "web_url": "https://g/11", "target_branch": "main", "source_branch": "feature/a"}]),
    "feature/b": json.dumps([{"iid": 12, "web_url": "https://g/12", "target_branch": "main", "source_branch": "feature/b"}]),
}


@pytest.fixture
def glab(monkeypatch):
    """Records every call of the host's CLI; answers `mr list` from GITLAB_ANSWERS."""
    calls = []

    def fake(root, tool, *args):
        calls.append((tool.cli, args))
        if args[:2] == ("mr", "list"):
            return GITLAB_ANSWERS.get(args[args.index("--source-branch") + 1], "[]")
        return ""

    monkeypatch.setattr(mrs, "_run", fake)
    return calls


def test_status_prints_the_table(repo, glab, capsys):
    _gitlab(repo)

    assert main.main(["chain", "status", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "!11 -> main" in out and "!12 -> main" in out
    assert ("glab", ("mr", "list", "--source-branch", "feature/a", "-F", "json")) in glab


def test_status_still_prints_git_facts_when_glab_is_missing(repo, monkeypatch, capsys):
    _gitlab(repo)

    def missing(root, tool, *args):
        raise mrs.MrError("`glab` is not installed")

    monkeypatch.setattr(mrs, "_run", missing)

    assert main.main(["chain", "status", str(repo)]) == 0

    captured = capsys.readouterr()
    assert "feature/a" in captured.out
    assert "not installed" in captured.err


def test_status_outside_a_chain_fails_with_the_guidance(repo, capsys):
    _git(repo, "checkout", "-q", "main")

    assert main.main(["chain", "status", str(repo)]) == 1

    assert "git town set-parent" in capsys.readouterr().err


def test_fix_targets_with_yes_retargets_the_misaimed_requests(repo, glab, capsys):
    _gitlab(repo)

    assert main.main(["chain", "fix-targets", "--yes", str(repo)]) == 0

    updates = [args for cli, args in glab if args[:2] == ("mr", "update")]
    assert updates == [("mr", "update", "12", "--target-branch", "feature/a")]
    assert "!12: target main → feature/a" in capsys.readouterr().out


@pytest.mark.parametrize(("answer", "updates"), [("y", 1), ("n", 0), ("", 0)])
def test_fix_targets_asks_first(repo, glab, monkeypatch, capsys, answer, updates):
    _gitlab(repo)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)

    assert main.main(["chain", "fix-targets", str(repo)]) == 0

    assert len([c for c in glab if c[1][:2] == ("mr", "update")]) == updates


def test_fix_targets_without_a_terminal_changes_nothing(repo, glab, monkeypatch):
    _gitlab(repo)

    def no_input(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_input)

    assert main.main(["chain", "fix-targets", str(repo)]) == 0
    assert not [c for c in glab if c[1][:2] == ("mr", "update")]


def test_fix_targets_with_everything_right_changes_nothing(repo, glab, monkeypatch, capsys):
    _gitlab(repo)
    monkeypatch.setitem(GITLAB_ANSWERS, "feature/b", GITLAB_ANSWERS["feature/b"].replace('"main"', '"feature/a"'))

    assert main.main(["chain", "fix-targets", "--yes", str(repo)]) == 0

    assert not [c for c in glab if c[1][:2] == ("mr", "update")]
    assert "targets the previous branch" in capsys.readouterr().out


def test_fix_targets_reports_a_refused_change_and_fails(repo, monkeypatch, capsys):
    _gitlab(repo)

    def fake(root, tool, *args):
        if args[:2] == ("mr", "update"):
            raise mrs.MrError("`glab mr update` failed: 403")
        return GITLAB_ANSWERS.get(args[args.index("--source-branch") + 1], "[]")

    monkeypatch.setattr(mrs, "_run", fake)

    assert main.main(["chain", "fix-targets", "-y", str(repo)]) == 1
    assert "403" in capsys.readouterr().err


def test_fix_targets_needs_a_known_host(repo, capsys):
    _git(repo, "remote", "set-url", "origin", "https://git.example.org/team/app.git")

    assert main.main(["chain", "fix-targets", "-y", str(repo)]) == 1

    assert "only GitLab (glab) and GitHub (gh)" in capsys.readouterr().err


def test_a_branch_the_remote_lacks_is_not_pushed(repo):
    _git(repo, "update-ref", "-d", "refs/remotes/origin/feature/b")
    assert gitops.ref_exists(repo, "refs/remotes/origin/feature/b") is False
    link = chain.links(repo, chain.read_chain(repo))[-1]
    assert link.pushed is False
