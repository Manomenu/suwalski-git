"""The scheduled task and the launchers carry the interpreter that ran the install,
so neither depends on which `python` comes first in some PATH — a scheduled task
does not even see Git Bash's."""

import datetime as dt
import xml.etree.ElementTree as ET

from suwgit import install, paths

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
    assert action.find("t:Arguments", NS).text == f'-X utf8 "{paths.ENTRYPOINT}" sweep'
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
    assert f'"{install.python_exe()}" -X utf8 "{paths.ENTRYPOINT}" "$@"' in bash.decode()
    assert f'"{install.python_exe()}" -X utf8 "{paths.ENTRYPOINT}" %*' in cmd


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
