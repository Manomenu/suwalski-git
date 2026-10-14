"""`suwgit report`: the B2B commits of a date range, as a PDF for the client.

The PDF is written by hand rather than by a library, to keep suwgit free of
dependencies. A work report is text and a few rules, which is exactly what the
14 fonts every PDF reader carries are for: Helvetica in WinAnsi encoding, so the
file embeds nothing and stays a few kilobytes. WinAnsi has no ą, ć, ę, ł, ń, ś,
ź or ż, but Helvetica has the glyphs, so the encoding names them by `/Differences`
in slots WinAnsi leaves empty or a report has no use for (¤, ¦, ², ¼ …).

The report is in Polish or English, by the rule in `language.py`: each project's
summary in its commits' language, the report's own words in Polish only when
every project is.

Layout: a title block, a summary of projects, then one section per project with
one block per commit — Work period, Summary, Work scope, Commit — never split
across pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from . import language
from .b2b import Entry

PAGE_WIDTH, PAGE_HEIGHT = 595.0, 842.0  # A4 in points
MARGIN = 56.0
FOOTER_Y = 32.0
LABEL_WIDTH = 86.0
BODY_SIZE = 10.0
LEADING = 14.0

REGULAR, BOLD = "F1", "F2"
INK, MUTED, RULE = 0.0, 0.42, 0.82

# Helvetica advance widths (per 1000 units of font size) from its AFM, ASCII 32–126.
_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 "
    "1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 "
    "333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"
)
_WIDTHS = {chr(32 + i): int(w) for i, w in enumerate(_HELVETICA.split())}
_WIDTHS.update({"•": 350, "–": 556, "—": 1000, "…": 1000, "·": 278, "ó": 556, "Ó": 778})

# The Polish letters WinAnsi lacks: (code, Helvetica glyph name, width). The
# codes are WinAnsi's empty slots first, then signs a report never prints.
_POLISH = {
    "ą": (0x81, "aogonek", 556),
    "ć": (0x8D, "cacute", 500),
    "ę": (0x8F, "eogonek", 556),
    "ł": (0x90, "lslash", 222),
    "ń": (0x9D, "nacute", 556),
    "ś": (0xA4, "sacute", 500),
    "ź": (0xA6, "zacute", 500),
    "ż": (0xA8, "zdotaccent", 500),
    "Ą": (0xAA, "Aogonek", 667),
    "Ć": (0xAF, "Cacute", 722),
    "Ę": (0xB2, "Eogonek", 667),
    "Ł": (0xB3, "Lslash", 556),
    "Ń": (0xB8, "Nacute", 722),
    "Ś": (0xB9, "Sacute", 667),
    "Ź": (0xBA, "Zacute", 611),
    "Ż": (0xBC, "Zdotaccent", 611),
}
_WIDTHS.update({char: width for char, (_, _, width) in _POLISH.items()})
_TAKEN = {code for code, _, _ in _POLISH.values()}
_ENCODING = (
    b"<< /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences ["
    + b" ".join(b"%d /%s" % (code, glyph.encode()) for code, glyph, _ in sorted(_POLISH.values()))
    + b"] >>"
)
# Helvetica-Bold runs wider; measuring with a margin is enough for wrapping.
_BOLD_FACTOR = 1.08


def text_width(text: str, font: str, size: float) -> float:
    units = sum(_WIDTHS.get(char, 556) for char in text)
    return units * size / 1000 * (_BOLD_FACTOR if font == BOLD else 1.0)


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap; a word longer than the line is cut where it must be."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, font, size) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while text_width(word, font, size) > width:
            cut = len(word)
            while cut > 1 and text_width(word[:cut], font, size) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines or [""]


def _encode(char: str) -> bytes:
    if char in _POLISH:
        return bytes([_POLISH[char][0]])
    raw = char.encode("cp1252", errors="replace")
    # A sign whose slot now holds a Polish letter would print as that letter.
    return b"?" if raw[0] in _TAKEN else raw


def _pdf_string(text: str) -> bytes:
    raw = b"".join(_encode(char) for char in text)
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _info_string(text: str) -> bytes:
    """A string for the document info: UTF-16, which every reader shows right in its title bar."""
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"


@dataclass
class _Document:
    """Pages as lists of PDF drawing operators, with a cursor running down the page."""

    pages: list[list[bytes]] = field(default_factory=lambda: [[]])
    y: float = PAGE_HEIGHT - MARGIN

    def text(self, x: float, text: str, font: str = REGULAR, size: float = BODY_SIZE, gray: float = INK, y: float | None = None) -> None:
        at = self.y if y is None else y
        self.pages[-1].append(b"%.2f g BT /%s %.1f Tf %.2f %.2f Td " % (gray, font.encode(), size, x, at) + _pdf_string(text) + b" Tj ET")

    def rule(self, gray: float = RULE) -> None:
        self.pages[-1].append(b"%.2f G 0.5 w %.2f %.2f m %.2f %.2f l S" % (gray, MARGIN, self.y, PAGE_WIDTH - MARGIN, self.y))

    def room(self, height: float) -> None:
        """Start a new page unless `height` more points fit on this one."""
        if self.y - height < MARGIN + 10:
            self.pages.append([])
            self.y = PAGE_HEIGHT - MARGIN

    def render(self, title: str, footer: str, lang: str = language.ENGLISH) -> bytes:
        count = len(self.pages)
        for number, ops in enumerate(self.pages, 1):
            here = _Document(pages=[ops])
            here.text(MARGIN, footer, size=8, gray=MUTED, y=FOOTER_Y)
            label = language.t(lang, "Page {number} of {count}").format(number=number, count=count)
            here.text(PAGE_WIDTH - MARGIN - text_width(label, REGULAR, 8), label, size=8, gray=MUTED, y=FOOTER_Y)

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"",  # the page tree, filled in once the page numbers are known
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding " + _ENCODING + b" >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding " + _ENCODING + b" >>",
            b"<< /Title " + _info_string(title) + b" /Producer (suwgit) >>",
        ]
        kids = []
        for ops in self.pages:
            stream = b"\n".join(ops)
            objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
            objects.append(
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>"
                % (PAGE_WIDTH, PAGE_HEIGHT, len(objects))
            )
            kids.append(b"%d 0 R" % len(objects))
        objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % count

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
        out += b"trailer\n<< /Size %d /Root 1 0 R /Info 5 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
        return bytes(out)


def _period(entry: Entry, with_time: bool) -> str:
    """The day, or the span of days; with `with_time`, down to the minute."""
    start, end = entry.start.astimezone(), entry.end.astimezone()
    if not with_time:
        if start.date() == end.date():
            return f"{start:%Y-%m-%d}"
        return f"{start:%Y-%m-%d} – {end:%Y-%m-%d}"
    if start.date() == end.date():
        return f"{start:%Y-%m-%d}  {start:%H:%M} – {end:%H:%M}"
    return f"{start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M}"


def _entry_rows(entry: Entry, width: float, with_time: bool, lang: str = language.ENGLISH) -> list[tuple[str, list[str], str]]:
    """(label, wrapped lines, font) for one commit block."""
    body = width - LABEL_WIDTH
    scope = []
    for item in entry.work_scope:
        wrapped = wrap(item, REGULAR, BODY_SIZE, body - 12)
        scope += [f"•  {wrapped[0]}"] + [f"    {line}" for line in wrapped[1:]]
    t = lambda text: language.t(lang, text)
    rows = [
        (t("Work period"), [_period(entry, with_time)], BOLD),
        # The client-facing summary; the technical title only stands in for a
        # commit written before summaries existed.
        (t("Summary"), wrap(entry.summary or entry.title, REGULAR, BODY_SIZE, body), REGULAR),
        (t("Work scope"), scope, REGULAR),
        (t("Commit"), [f"{entry.commit[:10]}  ·  {entry.repo.name}"], REGULAR),
    ]
    if entry.reverted:
        rows.insert(3, (t("Reverted"), [f"{entry.reverted:%Y-%m-%d} — {t('this work was undone later')}"], BOLD))
    return rows


def by_project(entries: list[Entry]) -> dict[str, list[Entry]]:
    """Entries grouped by project, in the order they come (sorted by project, then time)."""
    projects: dict[str, list[Entry]] = {}
    for entry in entries:
        projects.setdefault(entry.project, []).append(entry)
    return projects


def project_languages(entries: list[Entry]) -> dict[str, str]:
    """Each project's language: Polish only when every one of its commits is."""
    return {name: language.common(e.language for e in items) for name, items in by_project(entries).items()}


def report_language(entries: list[Entry]) -> str:
    """The language of the report's own words: Polish only when every project is."""
    return language.common(project_languages(entries).values())


def build(
    entries: list[Entry],
    first: date,
    last: date,
    generated: datetime,
    with_time: bool = False,
    author: str = "",
    summaries: dict[str, str] | None = None,
) -> bytes:
    """The whole report as PDF bytes. `entries` come sorted by project, then time.

    Days only by default; `with_time` adds the hours and minutes of every work
    period, and the timezone they are in. `author` is the Prepared by line;
    empty, it lists the commits' author names. `summaries` maps a project to
    the paragraph that opens its section; a project without one has none.
    """
    summaries = summaries or {}
    lang = report_language(entries)
    t = lambda text: language.t(lang, text)
    doc = _Document()
    width = PAGE_WIDTH - 2 * MARGIN
    span = f"{first:%Y-%m-%d} – {last:%Y-%m-%d}"
    authors = sorted({e.author for e in entries if e.author})
    offset = f"{generated:%z}" or "+0000"

    doc.y -= 8
    doc.text(MARGIN, t("Work report"), BOLD, 22)
    doc.y -= 26
    header = [
        (t("Period"), span),
        (t("Prepared by"), author or ", ".join(authors) or "—"),
        (t("Generated"), f"{generated:%Y-%m-%d %H:%M}" if with_time else f"{generated:%Y-%m-%d}"),
    ]
    if with_time:
        header.append((t("Times"), f"{t('local time, UTC')}{offset[:3]}:{offset[3:]}"))
    for label, value in header:
        doc.text(MARGIN, label, gray=MUTED)
        doc.text(MARGIN + LABEL_WIDTH, value)
        doc.y -= LEADING

    projects = by_project(entries)

    doc.y -= 10
    doc.text(MARGIN, t("Projects"), BOLD, 12)
    doc.y -= LEADING + 2
    for name, items in projects.items():
        days = len({e.start.astimezone().date() for e in items})
        doc.text(MARGIN, name)
        doc.text(
            MARGIN + width * 0.55,
            language.count(lang, len(items), "entry", "entries", ("wpis", "wpisy", "wpisów"))
            + ", "
            + language.count(lang, days, "day", "days", ("dzień", "dni", "dni")),
            gray=MUTED,
        )
        doc.y -= LEADING

    for number, (name, items) in enumerate(projects.items()):
        summary = wrap(summaries[name], REGULAR, BODY_SIZE, width) if summaries.get(name) else []
        # Every project after the first starts a page of its own: it never
        # begins as an orphaned heading, and the pages can be handed on per project.
        if number:
            doc.pages.append([])
            doc.y = PAGE_HEIGHT - MARGIN - 8
        else:
            doc.y -= 24
            # The heading, its summary and the first entry stay together.
            doc.room(110 + len(summary) * LEADING)
        doc.text(MARGIN, f"{t('Project')}: {name}", BOLD, 15)
        doc.y -= 10
        doc.rule(INK)
        doc.y -= 20

        if summary:
            for line in summary:
                doc.text(MARGIN, line)
                doc.y -= LEADING
            doc.y -= 12

        for entry in items:
            rows = _entry_rows(entry, width, with_time, lang)
            height = sum(len(lines) for _, lines, _ in rows) * LEADING + 16
            doc.room(height)
            for label, lines, font in rows:
                doc.text(MARGIN, label, gray=MUTED)
                for line in lines:
                    doc.text(MARGIN + LABEL_WIDTH, line, font)
                    doc.y -= LEADING
            doc.y += 4
            doc.rule()
            doc.y -= 18

    return doc.render(title=f"{t('Work report')} {span}", footer=f"{t('Work report')}  ·  {span}", lang=lang)


def as_json(
    entries: list[Entry],
    tenant: str,
    first: date,
    last: date,
    generated: datetime,
    author: str = "",
    summaries: dict[str, str] | None = None,
) -> dict:
    """The same report as data, for `--format json` — everything the PDF shows, plus
    what tooling needs and a client does not: the tenant, the technical title, full
    commit hashes, repository paths and exact times."""
    projects = by_project(entries)
    languages = project_languages(entries)
    return {
        "tenant": tenant,
        "language": report_language(entries),
        "period": {"from": first.isoformat(), "to": last.isoformat()},
        "prepared_by": author or ", ".join(sorted({e.author for e in entries if e.author})),
        "generated": generated.isoformat(timespec="seconds"),
        "projects": [
            {
                "name": name,
                "language": languages[name],
                "summary": (summaries or {}).get(name, ""),
                "entries": [
                    {
                        "work_period": {
                            "start": e.start.isoformat(timespec="minutes"),
                            "end": e.end.isoformat(timespec="minutes"),
                        },
                        "summary": e.summary,
                        "title": e.title,
                        "work_scope": e.work_scope,
                        "reverted": e.reverted.isoformat() if e.reverted else None,
                        "work_id": e.work_id,
                        "language": e.language,
                        "commit": e.commit,
                        "repository": str(e.repo),
                        "author": e.author,
                    }
                    for e in items
                ],
            }
            for name, items in projects.items()
        ],
    }


def default_filename(tenant: str, today: date) -> str:
    return f"{tenant}--{today:%Y-%m-%d}.pdf"
