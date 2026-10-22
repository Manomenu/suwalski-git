"""The host's CLI: which one, the command lines, the answers it is read from, and what a missing CLI says."""

import json
import subprocess

import pytest

from suwgit import mrs


def _repo(tmp_path, url):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    if url:
        subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", url], check=True)
    return tmp_path


@pytest.mark.parametrize(
    ("url", "tool"),
    [
        ("git@gitlab.example.com:team/app.git", mrs.GITLAB),
        ("https://gitlab.com/team/app.git", mrs.GITLAB),
        ("git@github.com:owner/app.git", mrs.GITHUB),
    ],
)
def test_the_tool_follows_the_host_of_origin(tmp_path, url, tool):
    assert mrs.tool_for(_repo(tmp_path, url)) is tool


def test_other_hosts_and_a_missing_origin_are_said_plainly(tmp_path):
    with pytest.raises(mrs.MrError, match="only GitLab"):
        mrs.tool_for(_repo(tmp_path / "a", "https://git.example.org/x/y.git"))
    with pytest.raises(mrs.MrError, match="no `origin` remote"):
        mrs.tool_for(_repo(tmp_path / "b", ""))


def test_a_missing_cli_says_what_to_install(tmp_path, monkeypatch):
    def gone(*_args, **_kwargs):
        raise FileNotFoundError("glab")

    monkeypatch.setattr(mrs.subprocess, "run", gone)

    with pytest.raises(mrs.MrError, match=r"`glab` is not installed.*gitlab-org/cli"):
        mrs.open_for(tmp_path, mrs.GITLAB, "b")


def test_a_failing_cli_passes_its_message_on(tmp_path, monkeypatch):
    done = subprocess.CompletedProcess([], 1, b"", b"401 Unauthorized")
    monkeypatch.setattr(mrs.subprocess, "run", lambda *a, **k: done)

    with pytest.raises(mrs.MrError, match="401 Unauthorized"):
        mrs.open_for(tmp_path, mrs.GITLAB, "b")


def _answer(monkeypatch, stdout):
    seen = []

    def fake(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout.encode(), b"")

    monkeypatch.setattr(mrs.subprocess, "run", fake)
    return seen


def test_gitlab_answer_is_read(tmp_path, monkeypatch):
    seen = _answer(monkeypatch, json.dumps([{"iid": 7, "web_url": "https://g/7", "target_branch": "dev"}]))

    mr = mrs.open_for(tmp_path, mrs.GITLAB, "feat")

    assert mr == mrs.MergeRequest(7, "https://g/7", "dev")
    assert seen == [["glab", "mr", "list", "--source-branch", "feat", "-F", "json"]]


def test_github_answer_is_read(tmp_path, monkeypatch):
    seen = _answer(monkeypatch, json.dumps([{"number": 3, "url": "https://h/3", "baseRefName": "dev"}]))

    assert mrs.open_for(tmp_path, mrs.GITHUB, "feat") == mrs.MergeRequest(3, "https://h/3", "dev")
    assert seen[0][:3] == ["gh", "pr", "list"]


def test_no_open_request_is_none(tmp_path, monkeypatch):
    _answer(monkeypatch, "[]")
    assert mrs.open_for(tmp_path, mrs.GITLAB, "feat") is None


@pytest.mark.parametrize("stdout", ["not json", '[{"iid": 1}]', '{"a": 1}'])
def test_an_unknown_shape_is_an_error_not_a_crash(tmp_path, monkeypatch, stdout):
    _answer(monkeypatch, stdout)
    with pytest.raises(mrs.MrError, match="shape"):
        mrs.open_for(tmp_path, mrs.GITLAB, "feat")


def test_retarget_command_lines(tmp_path, monkeypatch):
    seen = _answer(monkeypatch, "")
    mr = mrs.MergeRequest(5, "u", "main")

    mrs.retarget(tmp_path, mrs.GITLAB, mr, "feature/a")
    mrs.retarget(tmp_path, mrs.GITHUB, mr, "feature/a")

    assert seen == [
        ["glab", "mr", "update", "5", "--target-branch", "feature/a"],
        ["gh", "pr", "edit", "5", "--base", "feature/a"],
    ]
