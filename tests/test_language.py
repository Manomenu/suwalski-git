"""Commits and reports in Polish or English (see suwgit/language.py)."""

import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from suwgit import b2b, committer, initcmd, install, language, llm, mailer, main, report
from suwgit import config as config_module
from suwgit.config import Config, LlmConfig
from suwgit.llm import Suggestion

WARSAW = timezone(timedelta(hours=2))
LLM = LlmConfig(base_url="http://x", model="m")


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "a.txt").write_text("hi")
    return path


def _entry(project, lang, summary="did x", scope=("did x",)):
    start = datetime(2026, 10, 12, 9, 0, tzinfo=WARSAW)
    return b2b.Entry(
        Path("/r/shop"), "abc1234567", "Test", "[feature] x", summary, project, "lokum", start, start, list(scope), language=lang
    )


# --- the rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    "codes, expected",
    [(["pl", "pl"], "pl"), (["pl", "en"], "en"), (["en"], "en"), ([], "en")],
)
def test_polish_only_when_everything_is(codes, expected):
    assert language.common(codes) == expected


@pytest.mark.parametrize("n, text", [(1, "1 wpis"), (2, "2 wpisy"), (5, "5 wpisów"), (12, "12 wpisów"), (22, "22 wpisy")])
def test_polish_plurals(n, text):
    assert language.count("pl", n, "entry", "entries", ("wpis", "wpisy", "wpisów")) == text


def test_english_plurals():
    assert language.count("en", 1, "day", "days", ("dzień", "dni", "dni")) == "1 day"
    assert language.count("en", 3, "day", "days", ("dzień", "dni", "dni")) == "3 days"


# --- the config -----------------------------------------------------------------


def test_a_new_repository_takes_the_default_and_keeps_it(sandbox):
    config = Config(language="pl")
    config_module.register(config, Path("/a"))
    config_module.register(config, Path("/b"), language="en")
    config.language = "en"
    assert config_module.register(config, Path("/a"), default_tenant="lokum") == "updated"

    assert (config.entry(Path("/a")).language, config.entry(Path("/b")).language) == ("pl", "en")
    assert config.language_of(Path("/a")) == "pl"
    assert config.language_of(Path("/unregistered")) == "en"


def test_the_language_survives_a_save_and_an_old_entry_follows_the_default(sandbox):
    config = Config(language="pl")
    config_module.register(config, Path("/a"), language="en")
    config_module.save(config, config_module.paths.CONFIG_FILE)
    raw = json.loads(config_module.paths.CONFIG_FILE.read_text())
    raw["repos"].append({"path": "/old"})
    raw["repos"][0]["language"] = "en"
    config_module.paths.CONFIG_FILE.write_text(json.dumps(raw))

    loaded = config_module.load()
    assert loaded.language == "pl"
    assert loaded.language_of(Path("/a")) == "en" and loaded.language_of(Path("/old")) == "pl"


def test_register_language_on_the_command_line(sandbox, tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@example.com:x/y.git"], check=True)
    config_module.save(Config(llm=LLM, language="en"), config_module.paths.CONFIG_FILE)

    assert main.main(["register", str(repo), "--language", "pl"]) == 0
    assert "commits in Polish" in capsys.readouterr().out
    assert config_module.load().language_of(repo) == "pl"
    with pytest.raises(SystemExit):
        main.main(["register", str(repo), "--language", "de"])


def test_init_asks_for_the_language_and_insists_on_a_known_one(sandbox, monkeypatch):
    queue = ["2", "http://vllm:8000/v1", "qwen", "", "", "", "", "Jan", "n", "de", "pl"]
    monkeypatch.setattr("builtins.input", lambda prompt="": queue.pop(0) if queue else "")
    monkeypatch.setattr(install, "link_into_path", lambda: (True, "linked"))
    monkeypatch.setattr(install, "install_schedule", lambda hours, enabled: (True, "installed"))
    monkeypatch.setattr(install, "path_contains_local_bin", lambda: True)
    assert initcmd.run() == 0
    assert config_module.load().language == "pl"


# --- commits --------------------------------------------------------------------


def _stub(monkeypatch):
    calls = []

    def post(cfg, payload):
        calls.append(payload)
        answer = {"categories": ["feature"], "description": "dodano eksport", "unsafe_for_commit": False, "unsafe_reason": ""}
        return {"choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "_post", post)
    return calls


def test_a_polish_commit_tells_the_model_and_an_english_one_does_not(monkeypatch):
    calls = _stub(monkeypatch)
    from suwgit import gitops

    tree = gitops.WorkingTree(root=Path("/r"), status=" M a", stat="", diff="")
    llm.suggest_commit_message(LLM, tree, language="pl")
    llm.suggest_commit_message(LLM, tree)
    assert "in Polish" in calls[0]["messages"][0]["content"]
    assert "in Polish" not in calls[1]["messages"][0]["content"]


def test_a_b2b_commit_records_its_language(sandbox, tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(
        committer,
        "suggest_commit_message",
        lambda cfg, tree, **kw: Suggestion(
            "[feature] dodano eksport", summary="Klienci mogą pobrać wykres.", work_scope=["dodano eksport"]
        ),
    )

    result = committer.commit_repo(Config(llm=LLM), repo, tenant="lokum", language="pl")

    assert "\nLanguage: pl" in result.message
    assert b2b.parse(result.message, repo, result.commit).language == "pl"
    assert b2b.parse(result.message.replace("\nLanguage: pl", ""), repo, result.commit).language == "en"


def test_the_cli_and_the_daemon_commit_in_the_repositorys_language(sandbox, tmp_path, monkeypatch):
    from suwgit import daemon

    repo = _repo(tmp_path / "repo")
    config = Config(llm=LLM, language="en")
    config_module.register(config, repo, language="pl")
    monkeypatch.setattr(config_module, "load", lambda: config)
    seen = []
    fake = lambda cfg, root, **kw: seen.append(kw["language"]) or committer.Result(root, False, "x")
    monkeypatch.setattr(main, "commit_repo", fake)
    monkeypatch.setattr(daemon, "commit_repo", fake)

    main.main(["commit", str(repo)])
    daemon.run_once()
    assert seen == ["pl", "pl"]


# --- reports --------------------------------------------------------------------


def _pdf(entries):
    return report.build(entries, date(2026, 10, 1), date(2026, 10, 31), datetime(2026, 10, 31, 12, tzinfo=WARSAW))


def test_a_polish_report_has_polish_words_and_letters():
    pdf = _pdf([_entry("Sklep", "pl", "Klienci mogą zapłacić", ["dodano płatność"])])

    assert b"(Raport prac)" in pdf and b"(Zakres prac)" in pdf and b"1 wpis, 1 dzie" in pdf
    assert "Klienci mog\x81 zap\x90aci\x8d".encode("latin-1") in pdf
    assert b"/Differences [129 /aogonek" in pdf and b"/lslash" in pdf


def test_one_english_project_keeps_the_report_words_english():
    entries = [_entry("Sklep", "pl"), _entry("Shop", "en")]
    pdf = _pdf(entries)
    assert b"(Work report)" in pdf and b"(Raport prac)" not in pdf
    assert report.project_languages(entries) == {"Sklep": "pl", "Shop": "en"}


def test_one_english_commit_makes_the_project_english():
    assert report.project_languages([_entry("Sklep", "pl"), _entry("Sklep", "en")]) == {"Sklep": "en"}


def test_project_summaries_are_asked_for_in_each_projects_language(sandbox, monkeypatch):
    entries = [_entry("Sklep", "pl"), _entry("Shop", "en"), _entry("Mixed", "pl"), _entry("Mixed", "en")]
    monkeypatch.setattr(config_module, "load", lambda: Config(llm=LLM))
    asked = {}
    monkeypatch.setattr(main, "summarize_project", lambda cfg, name, *a, language: asked.update({name: language}) or "x")

    main._project_summaries(entries, date(2026, 10, 1), date(2026, 10, 31))
    assert asked == {"Sklep": "pl", "Shop": "en", "Mixed": "en"}


def test_the_summary_prompt_asks_for_polish(monkeypatch):
    calls = []

    def post(cfg, payload):
        calls.append(payload)
        return {"choices": [{"message": {"content": '{"summary": "W tym okresie sklep zyskał płatności."}'}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "_post", post)
    assert llm.summarize_project(LLM, "Sklep", "a", "b", [("d", "s", ["w"])], language="pl").startswith("W tym okresie")
    assert "in Polish" in calls[0]["messages"][0]["content"]


def test_json_says_the_languages():
    document = report.as_json([_entry("Sklep", "pl")], "lokum", date(2026, 10, 1), date(2026, 10, 31), datetime.now(WARSAW))
    assert document["language"] == "pl" and document["projects"][0]["language"] == "pl"
    assert document["projects"][0]["entries"][0]["language"] == "pl"


def test_the_mail_follows_the_report():
    subject, body = mailer.report_text("2026-10-01 – 2026-10-31", "Jan", "pl")
    assert subject == "Raport prac 2026-10-01 – 2026-10-31 — Jan" and body.startswith("Dzień dobry")
    assert mailer.report_text("x", "Jan")[0].startswith("Work report")
