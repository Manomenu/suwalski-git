"""The log is the only file that grows with time, so it gets a hard cap —
and it has two writers, so the cap has to survive concurrency."""

import logging
import multiprocessing
import os

from suwgit import paths
from suwgit.logs import CappedFileHandler


def _handler(cap):
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    handler = CappedFileHandler(paths.LOG_FILE, max_bytes=cap)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _log(handler, message):
    handler.emit(logging.LogRecord("suwgit", logging.INFO, __file__, 1, message, None, None))


def test_the_file_never_exceeds_the_cap(sandbox):
    handler = _handler(20_000)
    for i in range(5_000):
        _log(handler, f"line {i:06d} " + "x" * 80)

    assert paths.LOG_FILE.stat().st_size <= 20_000


def test_the_newest_lines_survive_and_the_oldest_go(sandbox):
    handler = _handler(20_000)
    for i in range(5_000):
        _log(handler, f"line {i:06d} " + "x" * 80)

    text = paths.LOG_FILE.read_text()
    assert "line 004999" in text  # the newest is kept
    assert "line 000000" not in text  # the oldest is gone
    assert "older entries dropped" in text  # and the gap is announced


def test_a_trim_never_leaves_a_half_line(sandbox):
    handler = _handler(20_000)
    for i in range(5_000):
        _log(handler, f"line {i:06d} " + "x" * 80)

    body = [line for line in paths.LOG_FILE.read_text().splitlines() if line.startswith("line ")]
    assert body, "expected surviving log lines"
    for line in body:
        assert len(line) == len("line 000000 ") + 80


def _writer(count):
    from suwgit import paths as child_paths

    handler = CappedFileHandler(child_paths.LOG_FILE, max_bytes=20_000)
    handler.setFormatter(logging.Formatter("%(message)s"))
    for i in range(count):
        _log(handler, f"pid{os.getpid()} line {i:04d} " + "y" * 60)


def test_two_processes_writing_at_once_keep_the_cap(sandbox):
    """The daemon and an interactive `suwgit commit` do exactly this."""
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    ctx = multiprocessing.get_context("fork")
    workers = [ctx.Process(target=_writer, args=(400,)) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)

    assert all(worker.exitcode == 0 for worker in workers)
    assert paths.LOG_FILE.stat().st_size <= 20_000
    # No sibling files: one capped log, not a rotation set.
    assert sorted(p.name for p in paths.STATE_DIR.iterdir()) == ["suwgit.log", "suwgit.log.lock"]
