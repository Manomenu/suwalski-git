"""`suwgit report --mail`: the mailboxes from `init` and `add-mail`, and sending the PDF.

One test talks real SMTP to a minimal server in a thread, so the message that
leaves is checked as a mail client would read it.
"""

import email
import socket
import subprocess
import threading
from datetime import datetime, timedelta
from email import policy
from typing import ClassVar

import pytest

from suwgit import b2b, config, initcmd, install, mailer, main, paths, picker, tenants
from suwgit.config import Config, LlmConfig, MailConfig

MAILBOX = MailConfig(host="127.0.0.1", port=2525, security="none", username="", sender="me@example.com")
WORK = MailConfig(host="smtp.work.com", username="me@work.com", password="w", sender="me@work.com")


def _repo(tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"], ["config", "user.name", "Test"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "a.txt").write_text("hi")
    start = datetime.now().astimezone() - timedelta(minutes=5)
    message = b2b.compose("[feature] added checkout", "Customers can pay.", ["added a checkout page"], "Lokum Shop", "lokum", start, start)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)
    tenants.record("lokum", repo)
    return repo


def _config(*mailboxes):
    boxes = list(mailboxes) if mailboxes else [MAILBOX]
    return Config(llm=LlmConfig(base_url="http://vllm:8000/v1", model="qwen"), report_author="Piotr Gugnowski", mailboxes=boxes)


def _answers(monkeypatch, *answers):
    """input() answers in order; EOFError once they run out (as with no terminal)."""
    queue, asked = list(answers), []

    def reply(prompt=""):
        asked.append(prompt)
        if not queue:
            raise EOFError
        answer = queue.pop(0)
        if answer is EOFError:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", reply)
    return asked


# --- mailboxes in the config ------------------------------------------------------


def test_passwords_stay_out_of_the_config_one_per_mailbox(sandbox):
    personal = MailConfig(host="smtp.gmail.com", username="me@gmail.com", password="s3cret one", sender="me@gmail.com")
    config.save(_config(personal, WORK))

    text = paths.CONFIG_FILE.read_text()
    assert "s3cret" not in text and '"password"' not in text
    loaded = config.load().mailboxes
    assert [(b.sender, b.password) for b in loaded] == [("me@gmail.com", "s3cret one"), ("me@work.com", "w")]


def test_a_config_with_one_mailbox_as_mail_still_loads(sandbox):
    paths.CONFIG_FILE.parent.mkdir(parents=True)
    paths.CONFIG_FILE.write_text('{"mail": {"host": "smtp.gmail.com", "sender": "me@gmail.com"}, "repos": []}')
    assert [b.sender for b in config.load().mailboxes] == ["me@gmail.com"]


def test_add_replaces_the_same_sender_and_remove_forgets_it(sandbox):
    cfg = _config(WORK)
    assert config.add_mailbox(cfg, MailConfig(host="smtp.gmail.com", sender="me@gmail.com")) == "added"
    assert config.add_mailbox(cfg, MailConfig(host="smtp.new.com", sender="ME@work.com")) == "updated"
    assert [(b.sender, b.host) for b in cfg.mailboxes] == [("ME@work.com", "smtp.new.com"), ("me@gmail.com", "smtp.gmail.com")]
    assert config.remove_mailbox(cfg, "me@gmail.com") is True
    assert config.remove_mailbox(cfg, "me@gmail.com") is False


# --- init and add-mail / remove-mail ---------------------------------------------


def _init(monkeypatch, answers, secret=""):
    _answers(monkeypatch, *answers)
    monkeypatch.setattr(initcmd.getpass, "getpass", lambda prompt="": secret)
    monkeypatch.setattr(install, "link_into_path", lambda: (True, "linked"))
    monkeypatch.setattr(install, "install_task", lambda hours, enabled: (True, "installed"))
    monkeypatch.setattr(install, "path_contains_local_bin", lambda: True)
    assert initcmd.run() == 0
    return config.load()


# url, model, key, hours, schedule, push, author — then "Add a mailbox?"
BEFORE_MAIL = ["http://vllm:8000/v1", "qwen", "", "", "", "", "Piotr Gugnowski"]
# address, SMTP server, encryption, port, login
GMAIL = ["me@gmail.com", "smtp.gmail.com", "", "", ""]


def test_init_adds_one_mailbox_and_points_to_add_mail(sandbox, monkeypatch, capsys):
    loaded = _init(monkeypatch, [*BEFORE_MAIL, "y", *GMAIL, ""], secret="app pass")
    assert loaded.mailboxes == [
        MailConfig(
            host="smtp.gmail.com", port=587, security="starttls", username="me@gmail.com", password="app pass", sender="me@gmail.com"
        )
    ]
    assert "suwgit add-mail" in capsys.readouterr().out


def test_init_suggests_the_smtp_server_from_the_address(sandbox, monkeypatch):
    loaded = _init(monkeypatch, [*BEFORE_MAIL, "y", "me@example.com", "", "ssl", "", ""], secret="x")
    [box] = loaded.mailboxes
    assert (box.host, box.security, box.port) == ("smtp.example.com", "ssl", 465)


def test_init_again_keeps_every_mailbox(sandbox, monkeypatch):
    config.save(_config(MAILBOX, WORK), paths.CONFIG_FILE)
    assert _init(monkeypatch, [""] * 12).mailboxes == [MAILBOX, WORK]  # Enter everywhere


def test_add_mail_and_remove_mail(sandbox, monkeypatch, capsys):
    config.save(_config(MAILBOX))
    _answers(monkeypatch, "", "", "", "", "")  # everything suggested, for an address given on the command line
    monkeypatch.setattr(initcmd.getpass, "getpass", lambda prompt="": "w")

    assert main.main(["add-mail", "me@work.com"]) == 0
    assert [(b.sender, b.host, b.username, b.password) for b in config.load().mailboxes][1] == (
        "me@work.com",
        "smtp.work.com",
        "me@work.com",
        "w",
    )
    assert "added me@work.com" in capsys.readouterr().out.replace("\x1b[1m", "").replace("\x1b[0m", "")

    assert main.main(["remove-mail", "me@work.com"]) == 0
    assert [b.sender for b in config.load().mailboxes] == ["me@example.com"]
    assert main.main(["remove-mail", "me@work.com"]) == 1


def test_list_shows_the_mailboxes_and_where_reports_went(sandbox, monkeypatch, capsys):
    config.save(_config(MAILBOX, WORK))
    tenants.remember_mail("lokum", ["client@example.com"], "me@work.com")
    monkeypatch.setattr(main.install, "task_status", lambda: "Ready")
    main.main(["list"])
    out = capsys.readouterr().out
    assert "me@example.com" in out and "me@work.com" in out
    assert "last report for lokum went to client@example.com from me@work.com" in out


# --- report --mail: who to, and from which mailbox ---------------------------------


def _report_setup(tmp_path, monkeypatch, *mailboxes):
    _repo(tmp_path)
    cfg = _config(*mailboxes) if mailboxes else _config()
    monkeypatch.setattr(main.config_module, "load", lambda: cfg)
    monkeypatch.setattr(main, "summarize_project", lambda *a: "This period, the shop gained a checkout.")
    monkeypatch.chdir(tmp_path)
    sent = []
    monkeypatch.setattr(mailer, "send", lambda box, message: sent.append(message))
    return sent


def test_no_mailbox_stops_before_anything_is_made(sandbox, tmp_path, monkeypatch, capsys):
    _report_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.config_module, "load", lambda: Config())
    monkeypatch.setattr(main, "summarize_project", lambda *a: pytest.fail("must not ask the model"))

    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"]) == 1
    assert "suwgit add-mail" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.pdf"))


@pytest.mark.parametrize(
    "argv, says",
    [
        (["--mail", "client@example.com", "--format", "json"], "--format json"),
        (["--mail", "not-an-address"], "not an e-mail address"),
        (["--mail-from", "me@example.com"], "add --mail"),
        (["--mail", "client@example.com", "--mail-from", "nobody@example.com"], "suwgit add-mail nobody@example.com"),
    ],
)
def test_mail_refuses_what_it_cannot_send(sandbox, tmp_path, monkeypatch, capsys, argv, says):
    _report_setup(tmp_path, monkeypatch)
    assert main.main(["report", "--tenant", "lokum", *argv]) == 1
    assert says in capsys.readouterr().err
    assert not list(tmp_path.glob("*.pdf"))


def test_the_only_mailbox_is_used_without_asking_which(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch, MAILBOX)
    monkeypatch.setattr(picker, "choose", lambda *a, **k: pytest.fail("one mailbox needs no pick"))
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "-y"]) == 0
    assert sent[0]["From"] == "me@example.com"


def test_mail_from_picks_one_of_several(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch, MAILBOX, WORK)
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "--mail-from", "ME@work.com", "-y"]) == 0
    assert sent[0]["From"] == "me@work.com"


def test_several_mailboxes_are_picked_from_starting_at_the_last_used(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch, MAILBOX, WORK)
    tenants.remember_mail("lokum", ["old@example.com"], "me@work.com")
    shown = {}

    def pick(title, options, default=0, **k):
        shown.update(options=options, default=default)
        return 0

    monkeypatch.setattr(picker, "choose", pick)
    _answers(monkeypatch, "y")
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"]) == 0
    assert shown == {"options": ["me@example.com", "me@work.com"], "default": 1}
    assert sent[0]["From"] == "me@example.com"


@pytest.mark.parametrize(
    "outcome, says",
    [(picker.NoTerminal, "--mail-from"), (None, "cancelled")],
)
def test_several_mailboxes_with_no_pick_send_nothing(sandbox, tmp_path, monkeypatch, capsys, outcome, says):
    sent = _report_setup(tmp_path, monkeypatch, MAILBOX, WORK)

    def pick(*a, **k):
        if outcome is picker.NoTerminal:
            raise picker.NoTerminal
        return outcome

    monkeypatch.setattr(picker, "choose", pick)
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"]) == 1
    assert says in capsys.readouterr().err and sent == []


def test_dash_y_with_several_mailboxes_takes_the_last_used_or_asks_for_mail_from(sandbox, tmp_path, monkeypatch, capsys):
    sent = _report_setup(tmp_path, monkeypatch, MAILBOX, WORK)
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "-y"]) == 1
    assert "--mail-from" in capsys.readouterr().err

    tenants.remember_mail("lokum", ["old@example.com"], "me@work.com")
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "-y"]) == 0
    assert sent[0]["From"] == "me@work.com"


# --- a bare --mail: the tenant's previous recipients ---------------------------------


def test_a_bare_mail_with_no_history_says_to_name_the_address(sandbox, tmp_path, monkeypatch, capsys):
    _report_setup(tmp_path, monkeypatch)
    assert main.main(["report", "--tenant", "lokum", "--mail"]) == 1
    assert "--mail <address>" in capsys.readouterr().err


def test_a_bare_mail_offers_the_previous_recipients_and_names_them(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch)
    tenants.remember_mail("lokum", ["client@example.com", "boss@example.com"], "me@example.com")
    asked = _answers(monkeypatch, "", "y")  # Enter = use them; then yes, send

    assert main.main(["report", "--tenant", "lokum", "--mail"]) == 0
    assert "client@example.com, boss@example.com" in asked[0] and "[Y/n]" in asked[0]
    assert sent[0]["To"] == "client@example.com, boss@example.com"


def test_no_to_the_previous_recipients_lets_you_type_others(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch)
    tenants.remember_mail("lokum", ["client@example.com"], "me@example.com")
    _answers(monkeypatch, "n", "new@example.com, other@example.com", "y")

    assert main.main(["report", "--tenant", "lokum", "--mail"]) == 0
    assert sent[0]["To"] == "new@example.com, other@example.com"
    assert tenants.last_mail("lokum") == (["new@example.com", "other@example.com"], "me@example.com")


def test_previous_recipients_are_kept_per_tenant(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch)
    tenants.remember_mail("lokum", ["client@lokum.com"], "me@example.com")
    tenants.remember_mail("acme", ["boss@acme.com"], "me@example.com")
    assert main.main(["report", "--tenant", "lokum", "--mail", "-y"]) == 0
    assert sent[0]["To"] == "client@lokum.com"
    assert tenants.last_mail("acme") == (["boss@acme.com"], "me@example.com")


def test_a_bare_mail_and_a_named_one_go_together_once_each(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch)
    tenants.remember_mail("lokum", ["client@example.com"], "me@example.com")
    assert main.main(["report", "--tenant", "lokum", "--mail", "--mail", "boss@example.com", "--mail", "CLIENT@example.com", "-y"]) == 0
    assert sent[0]["To"] == "client@example.com, boss@example.com"


# --- nothing goes out without a yes --------------------------------------------------


@pytest.mark.parametrize("answer", ["", "n", "no", "x", EOFError])
def test_anything_but_yes_sends_nothing_and_keeps_the_pdf(sandbox, tmp_path, monkeypatch, capsys, answer):
    sent = _report_setup(tmp_path, monkeypatch)
    asked = _answers(monkeypatch, answer)

    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"]) == 0
    assert sent == []
    assert "client@example.com" in asked[0] and "me@example.com" in asked[0] and "[y/N]" in asked[0]
    assert list(tmp_path.glob("lokum--*.pdf"))
    assert "not sent" in capsys.readouterr().out
    assert tenants.last_mail("lokum") is None  # nothing sent, nothing remembered


def test_the_question_comes_after_the_pdf_is_written(sandbox, tmp_path, monkeypatch, capsys):
    _report_setup(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr("builtins.input", lambda prompt="": seen.append(list(tmp_path.glob("*.pdf"))) or "")
    main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"])
    assert seen and seen[0], "the PDF must exist when the question is asked"
    assert "Look at the report first" in capsys.readouterr().out


def test_yes_sends_and_remembers_for_the_tenant(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch)
    _answers(monkeypatch, "y")
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"]) == 0
    assert sent[0]["To"] == "client@example.com" and sent[0]["From"] == "me@example.com"
    assert tenants.last_mail("lokum") == (["client@example.com"], "me@example.com")


def test_dash_y_sends_without_asking(sandbox, tmp_path, monkeypatch):
    sent = _report_setup(tmp_path, monkeypatch)
    asked = _answers(monkeypatch)
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "-y"]) == 0
    assert len(sent) == 1 and asked == []


# --- the picker ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "keys, picked",
    [
        (["enter"], 1),  # the default
        (["down", "enter"], 2),
        (["down", "down", "enter"], 0),  # wraps round
        (["up", "up", "enter"], 2),
        (["3", "enter"], 2),
        (["x", "enter"], 1),  # other keys do nothing
        (["cancel"], None),
    ],
)
def test_the_picker_moves_with_arrows_and_picks_with_enter(capsys, keys, picked):
    assert picker.choose("Send from?", ["a", "b", "c"], default=1, keys=iter(keys), out=lambda *a: None) == picked


# --- sending -------------------------------------------------------------------------


class FakeSMTP:
    sent: ClassVar[list] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.calls = [("connect", host, port)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        FakeSMTP.sent.append(self.calls)

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message))


def test_starttls_logs_in_and_sends(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    mail = MailConfig(host="smtp.gmail.com", username="me@gmail.com", password="pw", sender="me@gmail.com")
    mailer.send(mail, mailer.build(mail, ["a@example.com"], "s", "b", b"%PDF", "r.pdf"))
    [calls] = FakeSMTP.sent
    assert [c[0] for c in calls] == ["connect", "starttls", "login", "send"]
    assert calls[2] == ("login", "me@gmail.com", "pw")


def test_ssl_uses_an_encrypted_connection_from_the_start(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP", lambda *a, **k: pytest.fail("plain SMTP for an ssl mailbox"))
    mail = MailConfig(host="smtp.example.com", port=465, security="ssl", username="u", password="p", sender="me@example.com")
    mailer.send(mail, mailer.build(mail, ["a@example.com"], "s", "b", b"%PDF", "r.pdf"))
    assert [c[0] for c in FakeSMTP.sent[0]] == ["connect", "login", "send"]


class TinySMTP(threading.Thread):
    """Just enough of an SMTP server to receive one message."""

    def __init__(self):
        super().__init__(daemon=True)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.recipients, self.data = [], b""

    def run(self):
        conn, _ = self.server.accept()
        stream = conn.makefile("rwb")

        def say(line):
            stream.write(line.encode() + b"\r\n")
            stream.flush()

        say("220 tiny")
        while line := stream.readline():
            command = line.decode().strip()
            upper = command.upper()
            if upper.startswith(("EHLO", "HELO")):
                say("250 tiny")
            elif upper.startswith("RCPT TO:"):
                self.recipients.append(command[8:].strip(" <>"))
                say("250 ok")
            elif upper == "DATA":
                say("354 go on")
                while (chunk := stream.readline()) != b".\r\n":
                    self.data += chunk
                say("250 queued")
            elif upper == "QUIT":
                say("221 bye")
                break
            else:
                say("250 ok")
        conn.close()


def test_a_refused_login_keeps_the_pdf_and_says_why(sandbox, tmp_path, monkeypatch, capsys):
    _report_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(mailer, "send", _orig_send)

    def refuse(*a, **k):
        raise mailer.smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(mailer.smtplib, "SMTP", refuse)

    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "-y"]) == 1
    err = capsys.readouterr().err
    assert "was not sent" in err and "app password" in err
    assert list(tmp_path.glob("lokum--*.pdf"))


_orig_send = mailer.send


def test_the_report_arrives_by_real_smtp(sandbox, tmp_path, monkeypatch, capsys):
    server = TinySMTP()
    server.start()
    _report_setup(tmp_path, monkeypatch, MailConfig(host="127.0.0.1", port=server.port, security="none", sender="me@example.com"))
    monkeypatch.setattr(mailer, "send", _orig_send)

    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "--mail", "boss@example.com", "-y"]) == 0
    server.join(timeout=10)

    assert "sent to client@example.com, boss@example.com" in capsys.readouterr().out
    assert server.recipients == ["client@example.com", "boss@example.com"]
    received = email.message_from_bytes(server.data, policy=policy.default)
    assert received["From"] == "me@example.com"
    body = received.get_body(("plain",)).get_content().replace("\r\n", "\n")  # SMTP sends CRLF
    period = f"{(datetime.now() - timedelta(days=29)):%Y-%m-%d} – {datetime.now():%Y-%m-%d}"
    assert body == mailer.BODY.format(period=period, author="Piotr Gugnowski")  # the template, word for word
    assert received["Subject"] == f"Work report {period} — Piotr Gugnowski"
    [attachment] = list(received.iter_attachments())
    assert attachment.get_filename().startswith("lokum--") and attachment.get_content().startswith(b"%PDF")
