"""Choosing one of a few options in the terminal: arrow keys and Enter.

Used where a question has a handful of named answers, such as which mailbox a
report is sent from. It reads keys straight from the terminal — `termios` on
Linux and macOS, `msvcrt` on Windows — and falls back to a numbered list typed
as a number where keys cannot be read one at a time (Git Bash's mintty, for
one). With no terminal at all it gives up, and the caller says which flag
chooses instead.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator

UP, DOWN, ENTER, CANCEL = "up", "down", "enter", "cancel"


class NoTerminal(Exception):
    """There is nobody to ask."""


def _keys_posix() -> Iterator[str]:
    import termios
    import tty

    fd = sys.stdin.fileno()
    while True:
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            char = sys.stdin.read(1)
            if char == "\x1b":
                char += sys.stdin.read(2)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        yield {"\x1b[A": UP, "\x1b[B": DOWN, "k": UP, "j": DOWN, "\r": ENTER, "\n": ENTER, "\x03": CANCEL, "\x1b": CANCEL}.get(char, char)


def _keys_windows() -> Iterator[str]:
    import msvcrt

    while True:
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            char = {"H": UP, "P": DOWN}.get(msvcrt.getwch(), "")
        yield {"k": UP, "j": DOWN, "\r": ENTER, "\x03": CANCEL, "\x1b": CANCEL}.get(char, char)


def _can_read_keys() -> bool:
    if not sys.stdin.isatty():
        return False
    if os.name == "nt":
        # mintty (Git Bash) is not a Windows console: msvcrt would never see a key.
        return not os.environ.get("MSYSTEM") or bool(os.environ.get("WT_SESSION"))
    return True


def choose(title: str, options: list[str], default: int = 0, keys: Iterator[str] | None = None, out: Callable = print) -> int | None:
    """The index the user picked, or None if they cancelled. NoTerminal if nobody can answer."""
    if keys is None:
        if not _can_read_keys():
            return _choose_by_number(title, options, default)
        keys = _keys_windows() if os.name == "nt" else _keys_posix()

    current = min(max(default, 0), len(options) - 1)
    out(f"{title}  \033[2m(↑/↓, Enter)\033[0m")

    def draw(first: bool) -> None:
        if not first:
            sys.stdout.write(f"\033[{len(options)}A")
        for i, option in enumerate(options):
            mark = "\033[1m❯ " if i == current else "  "
            sys.stdout.write(f"\r\033[2K  {mark}{option}\033[0m\n")
        sys.stdout.flush()

    draw(True)
    for key in keys:
        if key == UP:
            current = (current - 1) % len(options)
        elif key == DOWN:
            current = (current + 1) % len(options)
        elif key == ENTER:
            return current
        elif key == CANCEL:
            return None
        elif key.isdigit() and 1 <= int(key) <= len(options):
            current = int(key) - 1
        else:
            continue
        draw(False)
    return None


def _choose_by_number(title: str, options: list[str], default: int) -> int | None:
    if not sys.stdin.isatty():
        raise NoTerminal
    print(title)
    for i, option in enumerate(options, 1):
        print(f"  {i}) {option}")
    while True:
        try:
            raw = input(f"  Choose [1-{len(options)}, Enter = {default + 1}]: ").strip()
        except EOFError:
            return None
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"  pick a number from 1 to {len(options)}")
