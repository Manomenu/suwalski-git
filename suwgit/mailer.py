"""Sending a report by e-mail, for `suwgit report --mail`.

Plain SMTP from the standard library: the mailbox set up in `suwgit init`, a
short text body, the PDF attached. Nothing is sent unless asked for, one report
at a time.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .config import MailConfig

TIMEOUT_SECONDS = 30

# Every report goes out in the same words: only the period and the sender's
# name change. Nothing a model wrote is in the mail itself — that is in the PDF,
# which is looked at before anything is sent.
SUBJECT = "Work report {period}{by}"
BODY = """Hello,

Please find attached the work report for {period}.

Best regards,
{author}
"""


class MailError(Exception):
    """The report could not be sent."""


def not_set_up() -> str:
    return "e-mail is not set up — run `suwgit init` and answer yes to 'Send reports by e-mail?'"


def report_text(period: str, author: str) -> tuple[str, str]:
    """(subject, body) of a report's e-mail, from the fixed template."""
    subject = SUBJECT.format(period=period, by=f" — {author}" if author else "")
    body = BODY.format(period=period, author=author).rstrip() + "\n"
    return subject, body


def looks_like_address(address: str) -> bool:
    name, _, domain = address.partition("@")
    return bool(name) and "." in domain and not any(c.isspace() for c in address)


def build(config: MailConfig, recipients: list[str], subject: str, body: str, pdf: bytes, filename: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    message.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return message


def send(config: MailConfig, message: EmailMessage) -> None:
    """Hand the message to the configured SMTP server; MailError on any failure."""
    if not config.is_configured:
        raise MailError(not_set_up())
    try:
        if config.security == "ssl":
            server = smtplib.SMTP_SSL(config.host, config.port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT_SECONDS)
        with server:
            if config.security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if config.username:
                server.login(config.username, config.password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            f"the mail server refused the login for {config.username} ({exc.smtp_code}) — check the password; "
            f"Gmail and Outlook need an app password, not the account's"
        ) from None
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(f"the mail server refused the recipient(s): {', '.join(exc.recipients)}") from None
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(f"could not send through {config.host}:{config.port} ({config.security}): {exc}") from None
