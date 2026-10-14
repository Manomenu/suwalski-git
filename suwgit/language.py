"""The language commits and reports are written in.

`suwgit init` sets the default, `suwgit register --language` one repository's.
A commit is written in its repository's language: the title's description and,
for B2B, the client summary and work scope. The categories stay the English
words of the closed list, so `git log` stays greppable in every language.

A B2B commit records its language as a trailer (`Language: pl`), so a report
knows it without guessing from the text. A commit without one predates the
setting and is English. A project's section of a report is Polish only when
every one of its commits is; one English commit makes it English, so the client
never reads a Polish summary over English work. The report's own words —
headings, labels, the e-mail — are Polish only when every project is.
"""

from __future__ import annotations

ENGLISH, POLISH = "en", "pl"
LANGUAGES = {ENGLISH: "English", POLISH: "Polish"}
DEFAULT = ENGLISH


class LanguageError(ValueError):
    pass


def check(code: str) -> str:
    code = code.strip().lower()
    if code not in LANGUAGES:
        raise LanguageError(f"unknown language {code!r} — one of: {', '.join(LANGUAGES)}")
    return code


def name(code: str) -> str:
    return LANGUAGES.get(code, code)


def common(codes) -> str:
    """Polish if every one is Polish, else English — the rule for a project and for a report."""
    codes = list(codes)
    return POLISH if codes and all(code == POLISH for code in codes) else ENGLISH


def commit_instruction(code: str) -> str:
    """What the model is told about the language of a commit; nothing for English, the prompt's own."""
    if code != POLISH:
        return ""
    return """
Language: write every text you produce — the description and any other text
field — in Polish (po polsku), with correct Polish letters (ą, ć, ę, ł, ń, ó, ś,
ź, ż). This overrides "plain English" above. The categories stay the English
words from the allowed list; the JSON field names stay as they are.
"""


def summary_instruction(code: str) -> str:
    """The same for a report's project summary."""
    if code != POLISH:
        return ""
    return """
Language: write the summary in Polish (po polsku), with correct Polish letters.
This overrides "Plain English" above; the entries may be in either language.
"""


# The report's own words. Keyed by the English, so English needs no table.
_POLISH = {
    "Work report": "Raport prac",
    "Period": "Okres",
    "Prepared by": "Autor",
    "Generated": "Wygenerowano",
    "Times": "Godziny",
    "local time, UTC": "czas lokalny, UTC",
    "Projects": "Projekty",
    "Project": "Projekt",
    "Work period": "Okres pracy",
    "Summary": "Podsumowanie",
    "Work scope": "Zakres prac",
    "Commit": "Commit",
    "Reverted": "Wycofane",
    "this work was undone later": "ta praca została później wycofana",
    "Page {number} of {count}": "Strona {number} z {count}",
}


def t(code: str, text: str) -> str:
    """`text` in the report's language."""
    return _POLISH.get(text, text) if code == POLISH else text


def count(code: str, n: int, one: str, many: str, pl_forms: tuple[str, str, str]) -> str:
    """`n` with its noun: 1 entry / 2 entries; 1 wpis / 2 wpisy / 5 wpisów."""
    if code != POLISH:
        return f"{n} {one if n == 1 else many}"
    singular, few, plural = pl_forms
    if n == 1:
        return f"{n} {singular}"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} {few}"
    return f"{n} {plural}"
