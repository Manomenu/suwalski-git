"""The scheduled task and the launchers carry the interpreter that ran the install,
so neither depends on which `python` comes first in some PATH — a scheduled task
does not even see Git Bash's."""

import datetime as dt
import subprocess
import xml.etree.ElementTree as ET

from suwgit import install_windows as install
from suwgit import paths

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _task(interval_hours=1.5, enabled=True):
    xml = install.render_task(interval_hours, enabled, now=dt.datetime(2026, 10, 9, 15, 42, 17, tzinfo=dt.UTC))
    return ET.fromstring(xml.split("\n", 1)[1])  # ElementTree refuses the UTF-16 declaration on a str


def test_the_task_repeats_every_interval_starting_a_minute_from_now():
    task = _task(1.5)
    assert task.find(".//t:TimeTrigger/t:StartBoundary", NS).text == "2026-10-09T15:43:00"
    assert task.find(".//t:Repetition/t:Interval", NS).text == "PT90M"


def test_an_interval_rounds_to_whole_minutes_and_never_to_zero():
    assert install.interval_minutes(0.001) == 1
    assert install.interval_minutes(0.26) == 16


def test_the_task_runs_the_sweep_windowless_from_the_clone():
    action = _task().find(".//t:Exec", NS)
    assert action.find("t:Command", NS).text == str(install.pythonw_exe())
    assert action.find("t:Arguments", NS).text == f'-X utf8 "{paths.PYTHON_ENTRYPOINT}" sweep'
    assert action.find("t:WorkingDirectory", NS).text == str(paths.REPO_ROOT)


def test_a_sweep_still_running_is_not_started_twice():
    assert _task().find(".//t:MultipleInstancesPolicy", NS).text == "IgnoreNew"


def test_schedule_off_registers_the_task_disabled():
    assert _task(enabled=False).find("t:Settings/t:Enabled", NS).text == "false"
    assert _task(enabled=True).find("t:Settings/t:Enabled", NS).text == "true"


def test_both_launchers_run_the_entrypoint_with_the_install_python(sandbox):
    assert install.link_into_path()[0]
    bash = paths.BASH_SHIM.read_bytes()
    cmd = paths.CMD_SHIM.read_text()
    assert b"\r" not in bash  # bash refuses a CR in a script
    assert f'"{install.python_exe()}" -X utf8 "{paths.PYTHON_ENTRYPOINT}" "$@"' in bash.decode()
    assert f'"{install.python_exe()}" -X utf8 "{paths.PYTHON_ENTRYPOINT}" %*' in cmd


def test_reinstalling_overwrites_our_launchers(sandbox):
    assert install.link_into_path()[0]
    assert install.link_into_path()[0]


def test_a_foreign_file_on_the_path_is_never_overwritten_or_deleted(sandbox):
    paths.BIN_DIR.mkdir(parents=True)
    paths.BASH_SHIM.write_text("#!/bin/sh\necho not ours\n")

    linked, detail = install.link_into_path()
    assert not linked
    assert "not ours" in detail

    result = install.unlink_from_path()
    assert paths.BASH_SHIM.read_text() == "#!/bin/sh\necho not ours\n"
    assert "left" in result[0]


def test_schtasks_is_called_windowless_with_the_task_name(sandbox, monkeypatch):
    """The command lines the scheduler is given, checked without a scheduler: only subprocess is replaced."""
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(install.host, "is_windows", lambda: True)
    monkeypatch.setattr(install.host.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(install.subprocess, "run", fake_run)
    monkeypatch.setattr(install, "render_task", lambda hours, enabled: "<Task/>")

    ok, detail = install.install_schedule(1.5, True)

    assert ok and "every 90 min" in detail
    create = seen[0][0]
    assert create[:4] == ["schtasks", "/Create", "/TN", paths.TASK_NAME] and create[4] == "/XML" and create[-1] == "/F"
    assert seen[0][1]["creationflags"] == 0x08000000
    assert not (paths.STATE_DIR / "task.xml").exists()  # the definition file is not left behind

    assert install.uninstall_schedule() == [f"deleted scheduled task '{paths.TASK_NAME}'"]
    assert seen[1][0] == ["schtasks", "/Delete", "/TN", paths.TASK_NAME, "/F"]


def test_a_failed_registration_says_why(sandbox, monkeypatch):
    monkeypatch.setattr(install, "_schtasks", lambda *args: (1, "Access is denied."))
    ok, detail = install.install_schedule(1.5, True)
    assert not ok and "Access is denied." in detail


def test_the_status_reads_the_columns_by_position(monkeypatch):
    """Headers are translated on a localised Windows; the row is not."""
    monkeypatch.setattr(install, "_schtasks", lambda *args: (0, '"suwgit","10/9/2026 5:00:00 PM","Ready"'))
    assert install.schedule_status() == "Ready, next run 10/9/2026 5:00:00 PM"
    monkeypatch.setattr(install, "_schtasks", lambda *args: (1, "ERROR: The system cannot find the file specified."))
    assert install.schedule_status() == "not registered"


def test_the_label_for_uninstall_is_empty_without_a_task(monkeypatch):
    monkeypatch.setattr(install, "_schtasks", lambda *args: (1, ""))
    assert install.schedule_label() == ""
    monkeypatch.setattr(install, "_schtasks", lambda *args: (0, ""))
    assert paths.TASK_NAME in install.schedule_label()
