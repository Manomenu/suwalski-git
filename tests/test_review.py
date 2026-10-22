"""`-i`: the message also says how to test the change and how to review it."""

import json
import subprocess
from pathlib import Path

import pytest

from suwgit import b2b, committer, gitops, llm, main
from suwgit.config import Config, LlmConfig
from suwgit.llm import AnswerUnusable, Suggestion

TREE = gitops.WorkingTree(root=Path("/nowhere"), status=" M a", stat="", diff="+x")
LLM = LlmConfig(base_url="http://x", model="m")
TEST = ["`just check` passes", "Start without `Tenant` → the app refuses to start"]
REVIEW = ["Start with `Extensions.cs`", "The renames carry no changes"]


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "a.txt").write_text("hi")
    return path


def _stub(monkeypatch, *answers):
    replies, calls = [json.dumps(a) for a in answers], []

    def post(cfg, payload):
        calls.append(payload)
        return {"choices": [{"message": {"content": replies.pop(0)}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "_post", post)
    return calls


ANSWER = {"categories": ["feature"], "description": "x", "unsafe_for_commit": False, "unsafe_reason": ""}


def test_review_asks_for_both_checklists_and_reads_them(monkeypatch):
    calls = _stub(monkeypatch, {**ANSWER, "how_to_test": ["- [ ] " + TEST[0], TEST[1]], "how_to_review": REVIEW})

    suggestion = llm.suggest_commit_message(LLM, TREE, review=True)

    assert suggestion.how_to_test == TEST and suggestion.how_to_review == REVIEW
    schema = calls[0]["response_format"]["json_schema"]["schema"]
    assert {"how_to_test", "how_to_review"} <= set(schema["required"])
    assert "how_to_review" in calls[0]["messages"][0]["content"]


def test_without_review_nothing_changes(monkeypatch):
    calls = _stub(monkeypatch, ANSWER)
    llm.suggest_commit_message(LLM, TREE)
    assert "how_to_test" not in calls[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert "how_to_test" not in llm.RESPONSE_SCHEMA["json_schema"]["schema"]["properties"]


def test_review_and_b2b_together_need_all_four(monkeypatch):
    calls = _stub(
        monkeypatch,
        {**ANSWER, "summary": "Did x.", "work_scope": ["did x"], "how_to_test": TEST, "how_to_review": []},
        {**ANSWER, "summary": "Did x.", "work_scope": ["did x"], "how_to_test": TEST, "how_to_review": REVIEW},
    )
    suggestion = llm.suggest_commit_message(LLM, TREE, b2b=True, review=True)
    assert suggestion.summary == "Did x." and suggestion.how_to_review == REVIEW
    assert {"summary", "work_scope", "how_to_test"} <= set(calls[0]["response_format"]["json_schema"]["schema"]["required"])


def test_a_missing_checklist_is_unusable(monkeypatch):
    _stub(monkeypatch, {**ANSWER, "how_to_test": TEST, "how_to_review": []}, {**ANSWER, "how_to_test": [], "how_to_review": REVIEW})
    with pytest.raises(AnswerUnusable, match="both checklists"):
        llm.suggest_commit_message(LLM, TREE, review=True)


def _suggest(cfg, tree, b2b=False, review=False, **_):
    return Suggestion(
        "[feature] added a",
        summary="People can do a." if b2b else "",
        work_scope=["added a"] if b2b else [],
        how_to_test=TEST if review else [],
        how_to_review=REVIEW if review else [],
    )


def test_the_checklists_go_under_the_title(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)

    result = committer.commit_repo(Config(llm=LLM), repo, review=True)

    assert result.message == (
        "[feature] added a\n\n"
        "How to test:\n- [ ] `just check` passes\n- [ ] Start without `Tenant` → the app refuses to start\n\n"
        "How to review:\n- [ ] Start with `Extensions.cs`\n- [ ] The renames carry no changes"
    )


def test_a_b2b_commit_with_checklists_still_reads_back(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)

    result = committer.commit_repo(Config(llm=LLM), repo, tenant="lokum", project="Shop", review=True)

    assert "How to review:" in result.message
    entry = b2b.parse(result.message, repo, result.commit)
    assert entry is not None and entry.work_scope == ["added a"] and entry.project == "Shop" and entry.tenant == "lokum"
    trailers = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%(trailers:key=Tenant,valueonly)"], capture_output=True, text=True, check=True
    )
    assert trailers.stdout.strip() == "lokum"


def test_dash_i_reaches_the_committer(sandbox, tmp_path, monkeypatch):
    from suwgit import config as config_module

    repo = _repo(tmp_path / "repo")
    seen = {}
    monkeypatch.setattr(config_module, "load", lambda: Config(llm=LLM))
    monkeypatch.setattr(main, "commit_repo", lambda cfg, root, **kw: seen.update(kw) or committer.Result(root, False, "x"))

    main.main(["push", str(repo), "-i"])
    assert seen["review"] is True
    main.main(["commit", str(repo), "say it is half done", "--instructions"])
    assert seen["review"] is True and seen["hint"] == "say it is half done"
    main.main(["commit", str(repo)])
    assert seen["review"] is False


def test_project_tooling_shows_the_commands_that_exist(tmp_path):
    (tmp_path / "justfile").write_text("check:\n    pytest\n")
    (tmp_path / "package.json").write_text(json.dumps({"name": "x", "dependencies": {"a": "1"}, "scripts": {"test": "vitest"}}))
    (tmp_path / "src").mkdir()

    text = gitops.project_tooling(tmp_path)

    assert "src/" in text and "check:\n    pytest" in text and '"test": "vitest"' in text
    assert "dependencies" not in text


def test_a_clean_repository_prints_no_asking_line(sandbox, tmp_path, monkeypatch, capsys):
    from suwgit import config as config_module

    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    monkeypatch.setattr(config_module, "load", lambda: Config(llm=LLM))
    monkeypatch.setattr(main, "commit_repo", lambda cfg, root, **kw: committer.Result(root, False, "nothing to commit"))

    main.main(["commit", str(repo)])
    assert "asking" not in capsys.readouterr().out

    (repo / "a.txt").write_text("changed")
    main.main(["commit", str(repo)])
    assert "asking" in capsys.readouterr().out


def test_commit_help_describes_the_path(capsys):
    with pytest.raises(SystemExit):
        main.main(["commit", "--help"])
    assert "closest repository above it" in capsys.readouterr().out
