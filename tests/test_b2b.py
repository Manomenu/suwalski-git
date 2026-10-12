"""B2B mode: commits that double as a record of work, and the report read back from them.

The format is written by the committer and read by `suwgit report` with no
database in between, so most of these tests go through a real git history.
"""

import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from suwgit import b2b, committer, gitops, main, paths, report, sweep, tenants
from suwgit.config import Config, LlmConfig, RepoEntry
from suwgit.llm import LlmUnavailable, Suggestion, parse_structured

WARSAW = timezone(timedelta(hours=2))
SUMMARY = "Customers can now download the revenue chart, and the main tab opens again after a break."
SCOPE = ["added CSV export of the revenue chart data", "fixed the main tab not opening after the session expired"]
TREE = gitops.WorkingTree(root=Path("/r"), status=" M a", stat="", diff="")
LLM = LlmConfig(base_url="http://x", model="m")


def _repo(path, email="t@example.com"):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", email], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "a.txt").write_text("hi")
    return path


def _config(**kw):
    return Config(llm=LlmConfig(base_url="http://vllm:8000/v1", model="qwen"), **kw)


def _suggest(cfg, tree, b2b=False):
    return Suggestion(
        "[feature,bugfix] added csv export and fixed main tab", work_scope=SCOPE if b2b else [], summary=SUMMARY if b2b else ""
    )


def _message(repo):
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B"], capture_output=True, text=True, check=True).stdout.strip()


# --- the format ----------------------------------------------------------------


def test_compose_writes_the_documented_shape():
    start = datetime(2026, 10, 12, 9, 14, tzinfo=WARSAW)
    message = b2b.compose("[feature] added csv export", SUMMARY, SCOPE, "dashboard", "lokum", start, start + timedelta(minutes=110))
    assert message == (
        "[feature] added csv export\n"
        "\n"
        f"Summary: {SUMMARY}\n"
        "\n"
        "Work-Scope:\n"
        "- added CSV export of the revenue chart data\n"
        "- fixed the main tab not opening after the session expired\n"
        "\n"
        "Project: dashboard\n"
        "Tenant: lokum\n"
        "Work-Period: 2026-10-12T09:14+02:00/2026-10-12T11:04+02:00"
    )


def test_what_compose_writes_parse_reads_back(tmp_path):
    start = datetime(2026, 10, 12, 9, 14, tzinfo=WARSAW)
    end = start + timedelta(minutes=110)
    entry = b2b.parse(b2b.compose("[feature] added csv export", SUMMARY, SCOPE, "dashboard", "lokum", start, end), tmp_path, "abc")
    assert (entry.title, entry.summary, entry.project, entry.tenant, entry.start, entry.end, entry.work_scope) == (
        "[feature] added csv export",
        SUMMARY,
        "dashboard",
        "lokum",
        start,
        end,
        SCOPE,
    )


def test_project_tenant_and_work_period_are_git_trailers(tmp_path):
    repo = _repo(tmp_path / "repo")
    start = datetime(2026, 10, 12, 9, 14, tzinfo=WARSAW)
    gitops.commit_all(repo, b2b.compose("[feature] x", SUMMARY, SCOPE, "dashboard", "lokum", start, start))
    trailers = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%(trailers:only,unfold)"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    assert trailers[:3] == ["Project: dashboard", "Tenant: lokum", "Work-Period: 2026-10-12T09:14+02:00/2026-10-12T09:14+02:00"]


@pytest.mark.parametrize(
    "message",
    [
        "[feature] an ordinary commit",
        "[feature] x\n\nWork-Scope:\n- did a thing\n\nProject: p",  # no period
        "[feature] x\n\nProject: p\nWork-Period: 2026-10-12T09:14+02:00/2026-10-12T11:04+02:00",  # no scope
        "[feature] x\n\nWork-Scope:\n- did a thing\n\nWork-Period: yesterday/today",
    ],
)
def test_anything_short_of_the_full_format_is_not_a_b2b_commit(tmp_path, message):
    assert b2b.parse(message, tmp_path, "abc") is None


def test_work_period_runs_from_the_first_change_to_the_last_plus_two_minutes():
    now = datetime(2026, 10, 12, 12, 0).timestamp()
    start, end = b2b.work_period((now - 3 * 3600, now - 3600), now)
    assert (start.hour, start.minute) == (9, 0)
    assert (end.hour, end.minute) == (11, 2)


def test_work_period_never_ends_in_the_future():
    now = datetime(2026, 10, 12, 12, 0).timestamp()
    _, end = b2b.work_period((now - 3600, now - 30), now)
    assert (end.hour, end.minute) == (12, 0)


def test_work_period_of_only_deletions_is_now():
    now = datetime(2026, 10, 12, 12, 0).timestamp()
    start, end = b2b.work_period(None, now)
    assert start == end


@pytest.mark.parametrize("name", ["", "two words", "-lead", "a/b"])
def test_a_tenant_must_be_a_plain_key(name):
    with pytest.raises(tenants.TenantError):
        tenants.check(name)


# --- the model ----------------------------------------------------------------


def test_structured_answer_carries_the_work_scope():
    suggestion = parse_structured(
        '{"categories": ["feature"], "description": "added csv export", "unsafe_for_commit": false, "unsafe_reason": "",'
        ' "work_scope": ["- added CSV export.", "  ", "fixed   the tab"]}'
    )
    assert suggestion.work_scope == ["added CSV export", "fixed the tab"]


def test_b2b_without_a_work_scope_is_refused(monkeypatch):
    from suwgit import llm

    content = '{"categories": ["feature"], "description": "added csv export", "unsafe_for_commit": false, "unsafe_reason": ""}'
    monkeypatch.setattr(llm, "_post", lambda cfg, payload: {"choices": [{"message": {"content": content}}]})

    with pytest.raises(LlmUnavailable, match="work scope"):
        llm.suggest_commit_message(LLM, TREE, b2b=True)
    assert llm.suggest_commit_message(LLM, TREE).message == "[feature] added csv export"


def test_b2b_asks_for_the_work_scope_in_the_schema(monkeypatch):
    from suwgit import llm

    sent = {}

    def post(cfg, payload):
        sent.update(payload)
        return {
            "choices": [
                {"message": {"content": '{"categories": ["feature"], "description": "x", "summary": "did x", "work_scope": ["did x"]}'}}
            ]
        }

    monkeypatch.setattr(llm, "_post", post)
    llm.suggest_commit_message(LLM, TREE, b2b=True)
    assert {"summary", "work_scope"} <= set(sent["response_format"]["json_schema"]["schema"]["required"])
    assert "not technical" in sent["messages"][0]["content"].lower()


# --- committing ----------------------------------------------------------------


def test_a_b2b_commit_puts_its_repository_in_the_tenant_index(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)

    committer.commit_repo(_config(), repo, tenant="lokum")
    (repo / "b.txt").write_text("more")
    committer.commit_repo(_config(), repo, tenant="lokum")

    assert tenants.load() == {"lokum": [str(repo)]}


def test_an_ordinary_commit_stays_out_of_the_index(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)
    committer.commit_repo(_config(), repo)
    assert not paths.TENANTS_FILE.exists()


def test_a_tenant_can_be_given_for_one_commit_in_a_shared_repository(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "shared")
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)
    config = _config(repos=[RepoEntry(str(repo))])

    committer.commit_repo(config, repo, tenant="lokum", project="Client portal")
    assert b2b.parse(_message(repo), repo, "x").project == "Client portal"

    (repo / "b.txt").write_text("own project")
    committer.commit_repo(config, repo)
    assert _message(repo) == "[feature,bugfix] added csv export and fixed main tab"


def test_an_unregistered_repository_is_named_after_its_folder(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "dashboard")
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)
    committer.commit_repo(_config(), repo, tenant="lokum")
    assert b2b.parse(_message(repo), repo, "x").project == "dashboard"


def test_the_sweep_leaves_a_repository_without_auto_commit_alone(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    config = _config(repos=[RepoEntry(str(repo), auto_commit=False)])
    monkeypatch.setattr(sweep.config_module, "load", lambda: config)
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)

    assert sweep.run() == (0, 0, 0)
    assert gitops.read_working_tree(repo).is_dirty


# --- reading the history back ----------------------------------------------------


def _b2b_commit(repo, name, start, project="dashboard", tenant="lokum"):
    (repo / name).write_text(name)
    gitops.commit_all(
        repo,
        b2b.compose(f"[feature] added {name}", f"summary of {name}", [f"added {name}"], project, tenant, start, start + timedelta(hours=1)),
    )


def test_collect_reads_only_your_commits_for_the_tenant_in_the_range(tmp_path):
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] an ordinary commit")
    _b2b_commit(repo, "before", datetime(2026, 9, 12, 10, 0).astimezone())
    _b2b_commit(repo, "first", datetime(2026, 9, 13, 10, 0).astimezone())
    _b2b_commit(repo, "other-client", datetime(2026, 9, 14, 10, 0).astimezone(), tenant="acme")
    _b2b_commit(repo, "last", datetime(2026, 10, 13, 10, 0).astimezone(), project="api")
    _b2b_commit(repo, "after", datetime(2026, 10, 14, 10, 0).astimezone())
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "colleague@example.com"], check=True)
    _b2b_commit(repo, "theirs", datetime(2026, 9, 20, 10, 0).astimezone())
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)

    entries = b2b.collect([repo], "lokum", date(2026, 9, 13), date(2026, 10, 13))

    assert [(e.project, e.title) for e in entries] == [("api", "[feature] added last"), ("dashboard", "[feature] added first")]


def test_collect_finds_work_on_an_unmerged_branch(tmp_path):
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "feature"], check=True)
    _b2b_commit(repo, "branch-work", datetime.now().astimezone())
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    assert len(b2b.collect([repo], "lokum", date.today(), date.today())) == 1


def test_the_report_is_a_pdf_with_every_entry_and_no_tenant(tmp_path):
    repo = _repo(tmp_path / "repo")
    for day in range(1, 40):
        _b2b_commit(repo, f"file{day}", datetime(2026, 9, 1, 10, 0).astimezone() + timedelta(days=day), tenant="lokum-internal")
    entries = b2b.collect([repo], "lokum-internal", date(2026, 9, 1), date(2026, 10, 31))

    pdf = report.build(entries, date(2026, 9, 1), date(2026, 10, 31), datetime.now().astimezone())

    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert b"Work period" in pdf and b"Work scope" in pdf and b"Project: dashboard" in pdf
    assert all(f"added file{day}".encode() in pdf for day in range(1, 40))
    assert b"lokum-internal" not in pdf  # the tenant is a working name, not for the client
    assert pdf.count(b"/Type /Page ") > 1  # 39 entries do not fit on one page


def test_wrap_keeps_every_line_inside_the_width():
    text = "fixed the main tab not opening after the session expired " * 6 + "averyveryverylongidentifierwithoutanyspacesinsideitatall" * 3
    lines = report.wrap(text, report.REGULAR, 10, 200)
    assert all(report.text_width(line, report.REGULAR, 10) <= 200 for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")


def test_report_dates_must_be_iso(sandbox):
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["report", "--tenant", "lokum", "--from", "9/13/2026"])
    assert main.build_parser().parse_args(["report", "--tenant", "lokum", "--from", "2026-09-13"]).since == date(2026, 9, 13)


def test_report_searches_every_repository_in_the_tenant_index(sandbox, tmp_path, monkeypatch):
    registered = _repo(tmp_path / "registered")
    unregistered = _repo(tmp_path / "unregistered")
    for repo in (registered, unregistered):
        _b2b_commit(repo, repo.name, datetime.now().astimezone() - timedelta(minutes=5))
        tenants.record("lokum", repo)
    target = tmp_path / "out.pdf"

    assert main.main(["report", "--tenant", "lokum", "-o", str(target)]) == 0
    pdf = target.read_bytes()
    assert b"added registered" in pdf and b"added unregistered" in pdf
    assert main.main(["report", "--tenant", "acme", "-o", str(target)]) == 1


def test_report_goes_where_output_says_or_into_the_current_folder(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    _b2b_commit(repo, "work", datetime.now().astimezone() - timedelta(minutes=5))
    tenants.record("lokum", repo)
    name = f"lokum--{date.today():%Y-%m-%d}.pdf"

    assert main.main(["report", "--tenant", "lokum", "-o", str(tmp_path / "new" / "deep" / "march.pdf")]) == 0
    assert (tmp_path / "new" / "deep" / "march.pdf").read_bytes().startswith(b"%PDF")

    (tmp_path / "reports").mkdir()
    assert main.main(["report", "--tenant", "lokum", "-o", str(tmp_path / "reports")]) == 0
    assert (tmp_path / "reports" / name).exists()

    assert main.main(["report", "--tenant", "lokum", "-o", f"{tmp_path / 'fresh'}/"]) == 0
    assert (tmp_path / "fresh" / name).exists()

    monkeypatch.chdir(tmp_path / "repo")
    assert main.main(["report", "--tenant", "lokum"]) == 0
    assert (tmp_path / "repo" / name).exists()


def test_report_without_dates_covers_the_last_30_days(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    today = datetime.now().astimezone().replace(hour=10, minute=0)
    _b2b_commit(repo, "inside", today - timedelta(days=29))
    _b2b_commit(repo, "outside", today - timedelta(days=30))
    tenants.record("lokum", repo)
    seen = {}
    collect = b2b.collect
    monkeypatch.setattr(main.b2b, "collect", lambda repos, tenant, first, last: seen.update(first=first, last=last) or [])

    main.main(["report", "--tenant", "lokum"])
    assert (seen["first"], seen["last"]) == (date.today() - timedelta(days=29), date.today())
    assert [e.title for e in collect([repo], "lokum", seen["first"], seen["last"])] == ["[feature] added inside"]


def test_a_b2b_commit_gets_the_full_format(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)

    result = committer.commit_repo(_config(), repo, tenant="lokum", project="Client portal")

    entry = b2b.parse(_message(repo), repo, "x")
    assert result.committed
    assert (entry.project, entry.tenant, entry.work_scope) == ("Client portal", "lokum", SCOPE)


def _settled(repo):
    old = datetime.now().timestamp() - 7 * 86400  # long settled, so the sweep does not wait for it
    os.utime(repo / "a.txt", (old, old))
    return repo


def test_the_sweep_commits_b2b_where_a_default_tenant_is_set(sandbox, tmp_path, monkeypatch):
    repo = _settled(_repo(tmp_path / "repo"))
    config = _config(repos=[RepoEntry(str(repo), default_tenant="lokum", default_project="Portal")])
    monkeypatch.setattr(sweep.config_module, "load", lambda: config)
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)

    assert sweep.run() == (1, 0, 0)
    entry = b2b.parse(_message(repo), repo, "x")
    assert (entry.tenant, entry.project) == ("lokum", "Portal")
    assert tenants.repos_of("lokum") == [repo]


def test_the_sweep_commits_ordinarily_without_a_default_tenant(sandbox, tmp_path, monkeypatch):
    repo = _settled(_repo(tmp_path / "repo"))
    config = _config(repos=[RepoEntry(str(repo), default_project="Portal")])
    monkeypatch.setattr(sweep.config_module, "load", lambda: config)
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)

    assert sweep.run() == (1, 0, 0)
    assert b2b.parse(_message(repo), repo, "x") is None


# --- --b2b / --project on commit and push --------------------------------------


def _resolve(argv, entry=None, root=Path("/r/shop")):
    return main._b2b_for(entry, root, main.build_parser().parse_args(argv))[:2]


DEFAULTS = RepoEntry("/r/shop", default_tenant="lokum", default_project="Lokum Shop")


def test_without_b2b_a_commit_is_ordinary_whatever_the_defaults(sandbox):
    assert _resolve(["commit"], DEFAULTS) == ("", "")
    assert _resolve(["push"], DEFAULTS) == ("", "")


def test_a_bare_b2b_takes_the_registered_defaults(sandbox):
    assert _resolve(["commit", "--b2b"], DEFAULTS) == ("lokum", "Lokum Shop")
    assert _resolve(["push", "--b2b", "--project"], DEFAULTS) == ("lokum", "Lokum Shop")


def test_named_values_override_the_defaults(sandbox):
    assert _resolve(["push", "--b2b", "acme"], DEFAULTS) == ("acme", "Lokum Shop")
    assert _resolve(["commit", "--b2b", "acme", "--project", "Acme CRM"], DEFAULTS) == ("acme", "Acme CRM")


def test_without_a_default_project_the_folder_name_is_used_later(sandbox):
    assert _resolve(["commit", "--b2b", "lokum"]) == ("lokum", "")


def test_a_bare_b2b_with_no_default_says_how_to_set_one(sandbox):
    with pytest.raises(ValueError) as exc:
        _resolve(["push", "--b2b"], RepoEntry("/r/shop"))
    assert "suwgit push --b2b lokum" in str(exc.value)
    assert "suwgit register /r/shop --default-b2b lokum" in str(exc.value)


def test_a_bare_project_with_no_default_says_how_to_set_one(sandbox):
    with pytest.raises(ValueError) as exc:
        _resolve(["commit", "--b2b", "lokum", "--project"])
    assert "--default-project" in str(exc.value)


def test_project_without_b2b_is_an_error(sandbox):
    with pytest.raises(ValueError, match="--project only goes with --b2b"):
        _resolve(["commit", "--project", "Lokum Shop"], DEFAULTS)


def test_register_flags_parse_into_defaults(sandbox):
    args = main.build_parser().parse_args(["register", ".", "--default-b2b", "lokum", "--default-project", "Shop", "--no-auto-commit"])
    assert (args.default_tenant, args.default_project, args.auto_commit) == ("lokum", "Shop", False)
    assert main.build_parser().parse_args(["register", ".", "--no-default-b2b"]).default_tenant == ""


def test_a_bare_b2b_with_no_default_fails_the_command_before_asking_the_model(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(main.config_module, "load", lambda: _config(repos=[RepoEntry(str(repo))]))
    monkeypatch.setattr(main, "commit_repo", lambda *a, **k: pytest.fail("must not commit"))
    assert main.main(["commit", str(repo), "--b2b"]) == 1


def test_b2b_without_a_summary_is_refused(monkeypatch):
    from suwgit import llm

    content = '{"categories": ["feature"], "description": "x", "summary": "", "work_scope": ["did x"]}'
    monkeypatch.setattr(llm, "_post", lambda cfg, payload: {"choices": [{"message": {"content": content}}]})
    with pytest.raises(LlmUnavailable, match="summary"):
        llm.suggest_commit_message(LLM, TREE, b2b=True)


def test_the_report_shows_the_client_summary_not_the_technical_title(tmp_path):
    repo = _repo(tmp_path / "repo")
    _b2b_commit(repo, "checkout", datetime.now().astimezone())
    (repo / "older.txt").write_text("older")
    gitops.commit_all(
        repo,
        "[feature] older\n\nWork-Scope:\n- did it\n\nTenant: lokum\nWork-Period: "
        + b2b.format_period(*b2b.work_period(None, datetime.now().timestamp())),
    )
    entries = b2b.collect([repo], "lokum", date.today(), date.today())

    pdf = report.build(entries, date.today(), date.today(), datetime.now().astimezone())

    assert b"summary of checkout" in pdf and b"[feature] added checkout" not in pdf
    assert b"[feature] older" in pdf  # no summary to show, so the title stands in


# --- the last --b2b used by hand -------------------------------------------------


def test_a_bare_b2b_offers_the_last_tenant_and_project_used_here(sandbox):
    tenants.remember(Path("/r/shop"), "lokum", "Lokum Shop")
    args = main.build_parser().parse_args(["push", "--b2b"])
    assert main._b2b_for(RepoEntry("/r/shop"), Path("/r/shop"), args) == ("lokum", "Lokum Shop", True)


def test_defaults_win_over_the_last_used_and_need_no_question(sandbox):
    tenants.remember(Path("/r/shop"), "acme", "Acme CRM")
    args = main.build_parser().parse_args(["push", "--b2b"])
    assert main._b2b_for(DEFAULTS, Path("/r/shop"), args) == ("lokum", "Lokum Shop", False)


def test_the_last_project_is_not_carried_over_to_another_tenant(sandbox):
    tenants.remember(Path("/r/shop"), "lokum", "Lokum Shop")
    assert _resolve(["commit", "--b2b", "acme"]) == ("acme", "")
    assert _resolve(["commit", "--b2b", "lokum"]) == ("lokum", "Lokum Shop")


def test_the_last_used_is_kept_per_repository(sandbox):
    tenants.remember(Path("/r/shop"), "lokum", "Lokum Shop")
    with pytest.raises(ValueError, match="no default one"):
        _resolve(["commit", "--b2b"], root=Path("/r/other"))


def _b2b_cli(sandbox, tmp_path, monkeypatch, answer):
    repo = _repo(tmp_path / "shop")
    monkeypatch.setattr(main.config_module, "load", lambda: _config())
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)
    monkeypatch.setattr(main, "commit_repo", committer.commit_repo)
    asked = []

    def fake_input(prompt):
        asked.append(prompt)
        if answer is EOFError:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    return repo, asked


def test_a_b2b_commit_by_hand_is_remembered_and_offered_next_time(sandbox, tmp_path, monkeypatch):
    repo, asked = _b2b_cli(sandbox, tmp_path, monkeypatch, "")
    assert main.main(["commit", str(repo), "--no-push", "--b2b", "lokum", "--project", "Lokum Shop"]) == 0
    assert asked == []  # named outright: nothing to confirm

    (repo / "b.txt").write_text("more")
    assert main.main(["commit", str(repo), "--no-push", "--b2b"]) == 0
    assert len(asked) == 1 and "lokum" in asked[0] and "Lokum Shop" in asked[0]
    assert b2b.parse(_message(repo), repo, "x").project == "Lokum Shop"


def test_answering_no_commits_nothing(sandbox, tmp_path, monkeypatch):
    repo, _ = _b2b_cli(sandbox, tmp_path, monkeypatch, "n")
    tenants.remember(repo, "lokum", "Lokum Shop")
    assert main.main(["commit", str(repo), "--no-push", "--b2b"]) == 1
    assert gitops.read_working_tree(repo).is_dirty


def test_no_terminal_to_answer_commits_nothing(sandbox, tmp_path, monkeypatch):
    repo, _ = _b2b_cli(sandbox, tmp_path, monkeypatch, EOFError)
    tenants.remember(repo, "lokum", "Lokum Shop")
    assert main.main(["commit", str(repo), "--no-push", "--b2b"]) == 1
    assert gitops.read_working_tree(repo).is_dirty


def test_yes_skips_the_question(sandbox, tmp_path, monkeypatch):
    repo, asked = _b2b_cli(sandbox, tmp_path, monkeypatch, "n")
    tenants.remember(repo, "lokum", "Lokum Shop")
    assert main.main(["commit", str(repo), "--no-push", "--b2b", "-y"]) == 0
    assert asked == []
    assert b2b.parse(_message(repo), repo, "x").tenant == "lokum"


def test_list_shows_every_tenant_with_its_folders_and_projects(sandbox, monkeypatch, capsys):
    tenants.record("lokum", Path("/r/shop"))
    tenants.record("lokum", Path("/r/shared"))
    tenants.record("acme", Path("/r/shared"))
    tenants.remember(Path("/r/shared"), "acme", "Acme CRM")
    config = _config(
        repos=[RepoEntry("/r/shop", default_tenant="lokum", default_project="Lokum Shop"), RepoEntry("/r/api", default_tenant="lokum")]
    )
    monkeypatch.setattr(main.config_module, "load", lambda: config)
    monkeypatch.setattr(main.install, "task_status", lambda: "Ready")

    assert main.main(["list"]) == 0
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    plain = [line.strip() for line in out.split("tenants\n")[1].splitlines() if line.strip()]
    assert plain == ["acme, shared (Acme CRM)", "lokum, api", "lokum, shared", "lokum, shop (Lokum Shop)"]


def _entry(start, end):
    return b2b.Entry(Path("/r/shop"), "abc", "Test", "[feature] x", "did x", "shop", "lokum", start, end, ["did x"])


def test_the_report_shows_days_by_default_and_hours_with_time():
    one_day = _entry(datetime(2026, 10, 12, 9, 14).astimezone(), datetime(2026, 10, 12, 11, 4).astimezone())
    two_days = _entry(datetime(2026, 10, 12, 23, 30).astimezone(), datetime(2026, 10, 13, 1, 15).astimezone())

    assert report._period(one_day, with_time=False) == "2026-10-12"
    assert report._period(two_days, with_time=False) == "2026-10-12 – 2026-10-13"
    assert report._period(one_day, with_time=True) == "2026-10-12  09:14 – 11:04"
    assert report._period(two_days, with_time=True) == "2026-10-12 23:30 – 2026-10-13 01:15"

    generated = datetime(2026, 10, 13, 8, 0).astimezone()
    days = report.build([one_day], date(2026, 10, 1), date(2026, 10, 13), generated)
    hours = report.build([one_day], date(2026, 10, 1), date(2026, 10, 13), generated, with_time=True)
    assert b"09:14" not in days and b"Times" not in days and b"08:00" not in days
    assert b"09:14" in hours and b"Times" in hours


def test_report_time_flag_parses(sandbox):
    assert main.build_parser().parse_args(["report", "--tenant", "lokum"]).time is False
    assert main.build_parser().parse_args(["report", "--tenant", "lokum", "--time"]).time is True


def test_a_report_of_the_same_name_is_overwritten(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    _b2b_commit(repo, "work", datetime.now().astimezone() - timedelta(minutes=5))
    tenants.record("lokum", repo)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / f"lokum--{date.today():%Y-%m-%d}.pdf"
    target.write_bytes(b"an older report")

    assert main.main(["report", "--tenant", "lokum"]) == 0
    assert target.read_bytes().startswith(b"%PDF")
    assert len(list(tmp_path.glob("lokum--*.pdf"))) == 1


def test_the_report_author_replaces_the_commit_handles():
    entry = _entry(datetime(2026, 10, 12, 9, 0).astimezone(), datetime(2026, 10, 12, 10, 0).astimezone())
    generated = datetime(2026, 10, 13, 8, 0).astimezone()
    assert b"Piotr Gugnowski" in report.build([entry], date(2026, 10, 1), date(2026, 10, 13), generated, author="Piotr Gugnowski")
    assert b"(Test)" in report.build([entry], date(2026, 10, 1), date(2026, 10, 13), generated)  # no name set: commit authors


def test_json_report_goes_to_stdout_and_parses(sandbox, tmp_path, monkeypatch, capsys):
    import json

    repo = _repo(tmp_path / "repo")
    _b2b_commit(repo, "work", datetime.now().astimezone() - timedelta(minutes=5), project="Lokum Shop")
    tenants.record("lokum", repo)
    monkeypatch.chdir(tmp_path)

    assert main.main(["report", "--tenant", "lokum", "--format", "json"]) == 0
    document = json.loads(capsys.readouterr().out)  # stdout is the JSON and nothing else

    assert document["tenant"] == "lokum"
    assert document["period"]["to"] == date.today().isoformat()
    [project] = document["projects"]
    [entry] = project["entries"]
    assert project["name"] == "Lokum Shop"
    assert (entry["summary"], entry["work_scope"], entry["repository"]) == ("summary of work", ["added work"], str(repo))
    assert datetime.fromisoformat(entry["work_period"]["start"]).tzinfo is not None
    assert not list(tmp_path.glob("*.pdf"))  # no file written


def test_json_report_of_an_empty_range_is_still_json(sandbox, tmp_path, capsys):
    import json

    repo = _repo(tmp_path / "repo")
    _b2b_commit(repo, "old", datetime(2026, 1, 5, 10, 0).astimezone())
    tenants.record("lokum", repo)

    assert main.main(["report", "--tenant", "lokum", "--format", "json", "--from", "2026-03-01", "--to", "2026-03-31"]) == 0
    assert json.loads(capsys.readouterr().out)["projects"] == []


def test_json_report_refuses_an_output_file(sandbox, tmp_path, capsys):
    tenants.record("lokum", _repo(tmp_path / "repo"))
    assert main.main(["report", "--tenant", "lokum", "--format", "json", "-o", str(tmp_path / "r.json")]) == 1
    assert capsys.readouterr().out == ""


def test_a_prefix_leads_the_title_of_a_b2b_commit(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(committer, "suggest_commit_message", _suggest)

    committer.commit_repo(_config(), repo, prefix="EH-3311111", tenant="lokum")

    entry = b2b.parse(_message(repo), repo, "x")
    assert entry.title == "EH-3311111 [feature,bugfix] added csv export and fixed main tab"
    assert entry.summary == SUMMARY


# --- the project summary ---------------------------------------------------------


def _answer(content):
    return {"choices": [{"message": {"content": content}}]}


def test_summarize_project_reads_the_structured_answer(monkeypatch):
    from suwgit import llm

    sent = {}

    def post(cfg, payload):
        sent.update(payload)
        return _answer('{"summary": "This period the shop gained a checkout."}')

    monkeypatch.setattr(llm, "_post", post)
    work = [("2026-10-12", "Customers can now pay in the shop.", ["added a checkout page"])]

    assert llm.summarize_project(LLM, "Lokum Shop", "2026-10-01", "2026-10-31", work) == "This period the shop gained a checkout."
    user = sent["messages"][1]["content"]
    assert "Lokum Shop" in user and "2026-10-12: Customers can now pay in the shop." in user and "added a checkout page" in user
    assert "not technical" in sent["messages"][0]["content"].lower()


def test_summarize_project_takes_prose_from_a_server_without_the_schema(monkeypatch):
    from suwgit import llm

    monkeypatch.setattr(llm, "_post", lambda cfg, payload: _answer("<think>hm</think>The shop gained a checkout."))
    assert llm.summarize_project(LLM, "Shop", "a", "b", [("d", "s", ["w"])]) == "The shop gained a checkout."


def test_an_empty_project_summary_is_llm_unavailable(monkeypatch):
    from suwgit import llm

    monkeypatch.setattr(llm, "_post", lambda cfg, payload: _answer('{"summary": ""}'))
    with pytest.raises(LlmUnavailable):
        llm.summarize_project(LLM, "Shop", "a", "b", [("d", "s", ["w"])])


def test_a_long_period_leaves_the_oldest_entries_out(monkeypatch):
    from suwgit import llm

    monkeypatch.setattr(llm, "MAX_PROJECT_INPUT_CHARS", 300)
    work = [(f"2026-10-{day:02d}", f"entry {day}", ["did something worth noting"]) for day in range(1, 21)]
    text = llm._project_input("Shop", "a", "b", work)
    assert len(text) <= 300
    assert "entry 20" in text and "entry 1:" not in text
    assert "older entries left out" in text


def _report_setup(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    _b2b_commit(repo, "work", datetime.now().astimezone() - timedelta(minutes=5), project="Lokum Shop")
    tenants.record("lokum", repo)
    monkeypatch.setattr(main.config_module, "load", lambda: _config())
    monkeypatch.chdir(tmp_path)
    return repo


def test_the_report_opens_each_project_with_its_summary(sandbox, tmp_path, monkeypatch, capsys):
    import json

    _report_setup(tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(main, "summarize_project", lambda cfg, name, *a: asked.append(name) or f"{name} moved forward.")

    assert main.main(["report", "--tenant", "lokum", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["projects"][0]["summary"] == "Lokum Shop moved forward."

    assert main.main(["report", "--tenant", "lokum", "-o", str(tmp_path / "r.pdf")]) == 0
    assert b"Lokum Shop moved forward." in (tmp_path / "r.pdf").read_bytes()
    assert asked == ["Lokum Shop", "Lokum Shop"]


def test_no_summary_never_asks_the_model(sandbox, tmp_path, monkeypatch, capsys):
    import json

    _report_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "summarize_project", lambda *a: pytest.fail("must not ask the model"))

    assert main.main(["report", "--tenant", "lokum", "--format", "json", "--no-summary"]) == 0
    assert json.loads(capsys.readouterr().out)["projects"][0]["summary"] == ""


def test_an_unreachable_model_still_gives_a_report(sandbox, tmp_path, monkeypatch, capsys):
    import json

    _report_setup(tmp_path, monkeypatch)

    def down(*a):
        raise LlmUnavailable("cannot reach http://vllm:8000/v1")

    monkeypatch.setattr(main, "summarize_project", down)

    assert main.main(["report", "--tenant", "lokum", "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["projects"][0]["summary"] == ""  # stdout is still clean JSON
    assert "no project summary" in captured.err


def test_worktrees_of_one_repository_count_each_commit_once(tmp_path):
    repo = _repo(tmp_path / "repo")
    gitops.commit_all(repo, "[chore] init")
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "other", str(tmp_path / "other")], check=True)
    _b2b_commit(repo, "main-work", datetime.now().astimezone())
    _b2b_commit(tmp_path / "other", "other-work", datetime.now().astimezone())

    entries = b2b.collect([repo, tmp_path / "other"], "lokum", date.today(), date.today())

    assert sorted(e.title for e in entries) == ["[feature] added main-work", "[feature] added other-work"]
