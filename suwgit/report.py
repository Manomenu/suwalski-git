"""`suwgit report`: the B2B commits of a date range, as a PDF for the client.

The PDF is written by hand rather than by a library, to keep suwgit free of
dependencies. A work report is text and a few rules, which is exactly what the
14 fonts every PDF reader carries are for: Helvetica in WinAnsi encoding, so the
file embeds nothing and stays a few kilobytes. That encoding covers English and
Western European text, which is what the report is written in.

Layout: a title block, a summary of projects, then one section per project with
one block per commit — Work period, Summary, Work scope, Commit — never split
across pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

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
_WIDTHS.update({"•": 350, "–": 556, "—": 1000, "…": 1000, "·": 278})
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


def _pdf_string(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


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

    def render(self, title: str, footer: str) -> bytes:
        count = len(self.pages)
        for number, ops in enumerate(self.pages, 1):
            here = _Document(pages=[ops])
            here.text(MARGIN, footer, size=8, gray=MUTED, y=FOOTER_Y)
            label = f"Page {number} of {count}"
            here.text(PAGE_WIDTH - MARGIN - text_width(label, REGULAR, 8), label, size=8, gray=MUTED, y=FOOTER_Y)

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"",  # the page tree, filled in once the page numbers are known
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
            b"<< /Title " + _pdf_string(title) + b" /Producer (suwgit) >>",
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


def _entry_rows(entry: Entry, width: float, with_time: bool) -> list[tuple[str, list[str], str]]:
    """(label, wrapped lines, font) for one commit block."""
    body = width - LABEL_WIDTH
    scope = []
    for item in entry.work_scope:
        wrapped = wrap(item, REGULAR, BODY_SIZE, body - 12)
        scope += [f"•  {wrapped[0]}"] + [f"    {line}" for line in wrapped[1:]]
    return [
        ("Work period", [_period(entry, with_time)], BOLD),
        # The client-facing summary; the technical title only stands in for a
        # commit written before summaries existed.
        ("Summary", wrap(entry.summary or entry.title, REGULAR, BODY_SIZE, body), REGULAR),
        ("Work scope", scope, REGULAR),
        ("Commit", [f"{entry.commit[:10]}  ·  {entry.repo.name}"], REGULAR),
    ]


def build(entries: list[Entry], first: date, last: date, generated: datetime, with_time: bool = False, author: str = "") -> bytes:
    """The whole report as PDF bytes. `entries` come sorted by project, then time.

    Days only by default; `with_time` adds the hours and minutes of every work
    period, and the timezone they are in. `author` is the Prepared by line;
    empty, it lists the commits' author names.
    """
    doc = _Document()
    width = PAGE_WIDTH - 2 * MARGIN
    span = f"{first:%Y-%m-%d} – {last:%Y-%m-%d}"
    authors = sorted({e.author for e in entries if e.author})
    offset = f"{generated:%z}" or "+0000"

    doc.y -= 8
    doc.text(MARGIN, "Work report", BOLD, 22)
    doc.y -= 26
    header = [
        ("Period", span),
        ("Prepared by", author or ", ".join(authors) or "—"),
        ("Generated", f"{generated:%Y-%m-%d %H:%M}" if with_time else f"{generated:%Y-%m-%d}"),
    ]
    if with_time:
        header.append(("Times", f"local time, UTC{offset[:3]}:{offset[3:]}"))
    for label, value in header:
        doc.text(MARGIN, label, gray=MUTED)
        doc.text(MARGIN + LABEL_WIDTH, value)
        doc.y -= LEADING

    projects: dict[str, list[Entry]] = {}
    for entry in entries:
        projects.setdefault(entry.project, []).append(entry)

    doc.y -= 10
    doc.text(MARGIN, "Projects", BOLD, 12)
    doc.y -= LEADING + 2
    for name, items in projects.items():
        days = len({e.start.astimezone().date() for e in items})
        doc.text(MARGIN, name)
        doc.text(
            MARGIN + width * 0.55,
            f"{len(items)} {'entry' if len(items) == 1 else 'entries'}, {days} {'day' if days == 1 else 'days'}",
            gray=MUTED,
        )
        doc.y -= LEADING

    for number, (name, items) in enumerate(projects.items()):
        # Every project after the first starts a page of its own: it never
        # begins as an orphaned heading, and the pages can be handed on per project.
        if number:
            doc.pages.append([])
            doc.y = PAGE_HEIGHT - MARGIN - 8
        else:
            doc.y -= 24
        doc.text(MARGIN, f"Project: {name}", BOLD, 15)
        doc.y -= 10
        doc.rule(INK)
        doc.y -= 20

        for entry in items:
            rows = _entry_rows(entry, width, with_time)
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

    return doc.render(title=f"Work report {span}", footer=f"Work report  ·  {span}")


def as_json(entries: list[Entry], tenant: str, first: date, last: date, generated: datetime, author: str = "") -> dict:
    """The same report as data, for `--format json` — everything the PDF shows, plus
    what tooling needs and a client does not: the tenant, the technical title, full
    commit hashes, repository paths and exact times."""
    projects: dict[str, list[Entry]] = {}
    for entry in entries:
        projects.setdefault(entry.project, []).append(entry)
    return {
        "tenant": tenant,
        "period": {"from": first.isoformat(), "to": last.isoformat()},
        "prepared_by": author or ", ".join(sorted({e.author for e in entries if e.author})),
        "generated": generated.isoformat(timespec="seconds"),
        "projects": [
            {
                "name": name,
                "entries": [
                    {
                        "work_period": {
                            "start": e.start.isoformat(timespec="minutes"),
                            "end": e.end.isoformat(timespec="minutes"),
                        },
                        "summary": e.summary,
                        "title": e.title,
                        "work_scope": e.work_scope,
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
