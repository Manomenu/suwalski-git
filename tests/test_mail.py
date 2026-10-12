"""`suwgit report --mail`: the optional mailbox from `init`, and sending the PDF.

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

from suwgit import b2b, config, initcmd, install, mailer, main, paths, tenants
from suwgit.config import Config, LlmConfig, MailConfig

MAILBOX = MailConfig(host="127.0.0.1", port=2525, security="none", username="", sender="me@example.com")


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


def _config(mail=MAILBOX):
    return Config(llm=LlmConfig(base_url="http://vllm:8000/v1", model="qwen"), report_author="Piotr Gugnowski", mail=mail)


# --- the mailbox in the config -----------------------------------------------------


def test_the_mail_password_stays_out_of_the_config(sandbox):
    mail = MailConfig(host="smtp.example.com", username="me@example.com", password="s3cret pass", sender="me@example.com")
    config.save(_config(mail))

    assert "s3cret" not in paths.CONFIG_FILE.read_text()
    assert paths.MAIL_PASSWORD_FILE.stat().st_mode & 0o777 == 0o600
    loaded = config.load().mail
    assert (loaded.host, loaded.port, loaded.security, loaded.password) == ("smtp.example.com", 587, "starttls", "s3cret pass")


def test_a_config_without_mail_is_not_set_up(sandbox):
    paths.CONFIG_FILE.parent.mkdir(parents=True)
    paths.CONFIG_FILE.write_text('{"repos": []}')
    assert config.load().mail.is_configured is False


# --- init ----------------------------------------------------------------------------


def _init(monkeypatch, answers, secret=""):
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": queue.pop(0) if queue else "")
    monkeypatch.setattr(initcmd.getpass, "getpass", lambda prompt="": secret)
    monkeypatch.setattr(install, "link_into_path", lambda: (True, "linked"))
    monkeypatch.setattr(install, "install_service", lambda enabled: (True, "installed"))
    monkeypatch.setattr(install, "path_contains_local_bin", lambda: True)
    assert initcmd.run() == 0
    return config.load()


# location, url, model, key, hours, autostart, push, author, then the mail questions
BEFORE_MAIL = ["2", "http://vllm:8000/v1", "qwen", "", "", "", "", "Piotr Gugnowski"]


def test_init_sets_up_the_mailbox(sandbox, monkeypatch):
    loaded = _init(monkeypatch, [*BEFORE_MAIL, "y", "smtp.gmail.com", "", "", "me@gmail.com", ""], secret="app pass")
    assert loaded.mail == MailConfig(
        host="smtp.gmail.com", port=587, security="starttls", username="me@gmail.com", password="app pass", sender="me@gmail.com"
    )


def test_ssl_suggests_its_own_port(sandbox, monkeypatch):
    loaded = _init(monkeypatch, [*BEFORE_MAIL, "y", "smtp.example.com", "ssl", "", "me@example.com", ""], secret="x")
    assert (loaded.mail.security, loaded.mail.port) == ("ssl", 465)


def test_init_again_keeps_the_mailbox_and_its_password(sandbox, monkeypatch):
    mail = MailConfig(host="smtp.gmail.com", username="me@gmail.com", password="app pass", sender="me@gmail.com")
    config.save(_config(mail), paths.CONFIG_FILE)
    assert _init(monkeypatch, ["2"], secret="").mail == mail  # Enter everywhere, the password too


def test_answering_no_forgets_the_mailbox(sandbox, monkeypatch):
    mail = MailConfig(host="smtp.gmail.com", username="me@gmail.com", password="app pass", sender="me@gmail.com")
    config.save(_config(mail), paths.CONFIG_FILE)
    loaded = _init(monkeypatch, ["2", "", "", "", "", "", "", "", "n"])
    assert loaded.mail.is_configured is False
    assert not paths.MAIL_PASSWORD_FILE.exists()


# --- report --mail -------------------------------------------------------------------


def _report_setup(sandbox, tmp_path, monkeypatch, mail=MAILBOX):
    _repo(tmp_path)
    monkeypatch.setattr(main.config_module, "load", lambda: _config(mail))
    monkeypatch.setattr(main, "summarize_project", lambda *a: "This period, the shop gained a checkout.")
    monkeypatch.chdir(tmp_path)


def test_mail_without_a_mailbox_stops_before_anything_is_made(sandbox, tmp_path, monkeypatch, capsys):
    _report_setup(sandbox, tmp_path, monkeypatch, mail=MailConfig())
    monkeypatch.setattr(main, "summarize_project", lambda *a: pytest.fail("must not ask the model"))

    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"]) == 1
    assert "suwgit init" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.pdf"))


@pytest.mark.parametrize(
    "argv, says",
    [
        (["--mail", "client@example.com", "--format", "json"], "--format json"),
        (["--mail", "not-an-address"], "not an e-mail address"),
    ],
)
def test_mail_refuses_what_it_cannot_send(sandbox, tmp_path, monkeypatch, capsys, argv, says):
    _report_setup(sandbox, tmp_path, monkeypatch)
    assert main.main(["report", "--tenant", "lokum", *argv]) == 1
    assert says in capsys.readouterr().err


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


def test_a_refused_login_keeps_the_pdf_and_says_why(sandbox, tmp_path, monkeypatch, capsys):
    _report_setup(sandbox, tmp_path, monkeypatch)

    def refuse(*a, **k):
        raise mailer.smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(mailer.smtplib, "SMTP", refuse)

    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "-y"]) == 1
    err = capsys.readouterr().err
    assert "was not sent" in err and "app password" in err
    assert list(tmp_path.glob("lokum--*.pdf"))


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


def test_the_report_arrives_by_real_smtp(sandbox, tmp_path, monkeypatch, capsys):
    server = TinySMTP()
    server.start()
    _report_setup(
        sandbox, tmp_path, monkeypatch, mail=MailConfig(host="127.0.0.1", port=server.port, security="none", sender="me@example.com")
    )

    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "--mail", "boss@example.com", "-y"]) == 0
    server.join(timeout=10)

    assert "sent to client@example.com, boss@example.com" in capsys.readouterr().out
    assert server.recipients == ["client@example.com", "boss@example.com"]
    received = email.message_from_bytes(server.data, policy=policy.default)
    assert received["From"] == "me@example.com"
    assert received["Subject"].startswith("Work report ") and received["Subject"].endswith("— Piotr Gugnowski")
    body = received.get_body(("plain",)).get_content().replace("\r\n", "\n")  # SMTP sends CRLF
    period = f"{(datetime.now() - timedelta(days=29)):%Y-%m-%d} – {datetime.now():%Y-%m-%d}"
    assert body == mailer.BODY.format(period=period, author="Piotr Gugnowski")  # the template, word for word
    assert received["Subject"] == f"Work report {period} — Piotr Gugnowski"
    [attachment] = list(received.iter_attachments())
    assert attachment.get_filename().startswith("lokum--") and attachment.get_content().startswith(b"%PDF")


# --- nothing goes out without a yes ------------------------------------------------


def _sending(sandbox, tmp_path, monkeypatch, answer):
    _report_setup(sandbox, tmp_path, monkeypatch)
    sent, asked = [], []
    monkeypatch.setattr(mailer, "send", lambda cfg, message: sent.append(message))

    def reply(prompt=""):
        asked.append(prompt)
        if answer is EOFError:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", reply)
    return sent, asked


@pytest.mark.parametrize("answer", ["", "n", "no", "x", EOFError])
def test_anything_but_yes_sends_nothing_and_keeps_the_pdf(sandbox, tmp_path, monkeypatch, capsys, answer):
    sent, asked = _sending(sandbox, tmp_path, monkeypatch, answer)

    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"]) == 0
    assert sent == []
    assert "client@example.com" in asked[0] and "me@example.com" in asked[0] and "[y/N]" in asked[0]
    assert list(tmp_path.glob("lokum--*.pdf"))
    assert "not sent" in capsys.readouterr().out


def test_the_question_comes_after_the_pdf_is_written(sandbox, tmp_path, monkeypatch, capsys):
    _sent, asked = _sending(sandbox, tmp_path, monkeypatch, "")
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(list(tmp_path.glob("*.pdf"))) or "")
    main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"])
    assert asked and asked[0], "the PDF must exist when the question is asked"
    assert "Look at the report first" in capsys.readouterr().out


def test_yes_sends(sandbox, tmp_path, monkeypatch):
    sent, _ = _sending(sandbox, tmp_path, monkeypatch, "y")
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com"]) == 0
    assert len(sent) == 1 and sent[0]["To"] == "client@example.com" and sent[0]["From"] == "me@example.com"


def test_dash_y_sends_without_asking(sandbox, tmp_path, monkeypatch):
    sent, asked = _sending(sandbox, tmp_path, monkeypatch, "n")
    assert main.main(["report", "--tenant", "lokum", "--mail", "client@example.com", "-y"]) == 0
    assert len(sent) == 1 and asked == []
