"""The OS primitives: the Linux ones run here, the Windows ones are checked as far as they can be without Windows."""

import subprocess
import sys

import pytest

from suwgit import host, locking

ON_WINDOWS = sys.platform == "win32"


def test_no_console_window_is_asked_for_only_on_windows(monkeypatch):
    monkeypatch.setattr(host, "is_windows", lambda: False)
    assert host.no_window() == {}
    monkeypatch.setattr(host, "is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert host.no_window() == {"creationflags": 0x08000000}


def test_secrets_are_made_private_only_where_chmod_means_something(tmp_path, monkeypatch):
    secret = tmp_path / "key"
    secret.write_text("x")
    secret.chmod(0o644)
    monkeypatch.setattr(host, "is_windows", lambda: True)
    host.keep_private(secret)
    assert secret.stat().st_mode & 0o777 == 0o644 or ON_WINDOWS
    monkeypatch.setattr(host, "is_windows", lambda: False)
    host.keep_private(secret)
    assert secret.stat().st_mode & 0o777 == 0o600 or ON_WINDOWS


def test_a_second_lock_on_the_same_file_is_refused_not_waited_for(tmp_path):
    target = tmp_path / "a.lock"
    with locking.file_lock(target, wait=False), pytest.raises(locking.Busy), locking.file_lock(target, wait=False):
        pass
    with locking.file_lock(target, wait=False):  # released again
        pass


def test_a_waiting_lock_gets_the_file_once_it_is_free(tmp_path):
    import threading

    target = tmp_path / "b.lock"
    order = []
    holder_in = threading.Event()

    def holder():
        with locking.file_lock(target, wait=False):
            holder_in.set()
            threading.Event().wait(0.2)
            order.append("holder done")

    thread = threading.Thread(target=holder)
    thread.start()
    holder_in.wait()
    with locking.file_lock(target, wait=True):
        order.append("waiter in")
    thread.join()
    assert order == ["holder done", "waiter in"]


@pytest.mark.skipif(not ON_WINDOWS, reason="msvcrt byte-range locks exist only on Windows")
def test_the_windows_lock_is_a_byte_range_lock(tmp_path):
    """Same two tests as above, kept apart so a Windows run says which implementation it used."""
    target = tmp_path / "c.lock"
    assert host.is_windows()
    with locking.file_lock(target, wait=False), pytest.raises(locking.Busy), locking.file_lock(target, wait=False):
        pass


def test_sweep_runs_one_pass_and_says_what_it_did(sandbox, monkeypatch, capsys):
    """What the Windows task runs: no loop, a summary on the console and a line in the log."""
    from suwgit import daemon, main

    monkeypatch.setattr(daemon, "run_once", lambda: (2, 1, 3))
    assert main.main(["sweep"]) == 0
    assert "2 committed, 1 pushed, 3 still being edited" in capsys.readouterr().out
    assert "sweep: done (2 committed, 1 pushed, 3 still being edited)" in main.paths.LOG_FILE.read_text()


def test_every_git_call_is_windowless_on_windows(monkeypatch):
    """The sweep runs under pythonw: a git call without the flag would open a console window each time."""
    from suwgit import gitops

    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(gitops.subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(host, "is_windows", lambda: True)
    gitops._git(None, "--version")
    monkeypatch.setattr(host, "is_windows", lambda: False)
    gitops._git(None, "--version")

    assert seen[0]["creationflags"] == 0x08000000
    assert "creationflags" not in seen[1]
