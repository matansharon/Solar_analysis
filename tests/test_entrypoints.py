"""The two root entry points must run under .venv.

Under a non-venv interpreter the installed anthropic SDK is old enough that the
Claude narrative calls fail *silently*: the run exits 0 and still emails, with
no narrative in the report. A scheduled task that got this wrong would look
healthy for weeks.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import _venv                                          # noqa: E402
import app                                            # noqa: E402
import daily_pipeline                                 # noqa: E402


def test_app_still_delegates_to_the_shared_relaunch(monkeypatch):
    # `python app.py --help` cannot catch a broken delegation: usage prints
    # either way whenever the system interpreter has the deps installed, which
    # on this machine it does (just an older anthropic). Pin the wiring here.
    assert callable(app.main) and callable(app._relaunch_in_venv)
    assert app.ROOT == _venv.ROOT and app.VENV_PY == _venv.VENV_PY
    seen = {}
    monkeypatch.setattr(_venv, "relaunch",
                        lambda path, **kw: seen.update(path=path) or 3)
    assert app._relaunch_in_venv() == 3
    assert os.path.basename(seen["path"]) == "app.py"


def test_relaunch_is_a_noop_once_the_guard_is_set(monkeypatch):
    monkeypatch.setenv("SOLAR_APP_IN_VENV", "1")
    assert _venv.relaunch(str(ROOT / "app.py")) is None


def test_relaunch_is_a_noop_when_no_venv_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("SOLAR_APP_IN_VENV", raising=False)
    monkeypatch.setattr(_venv, "VENV_PY", str(tmp_path / "nope" / "python.exe"))
    assert _venv.relaunch(str(ROOT / "app.py")) is None


def test_relaunch_reexecutes_under_the_venv_and_sets_the_guard(monkeypatch,
                                                              tmp_path):
    monkeypatch.delenv("SOLAR_APP_IN_VENV", raising=False)
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("", encoding="utf-8")
    monkeypatch.setattr(_venv, "VENV_PY", str(fake_py))
    seen = {}

    class Done:
        returncode = 7
    monkeypatch.setattr(_venv.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd, env=kw.get("env"))
                        or Done())
    assert _venv.relaunch(str(ROOT / "daily_pipeline.py")) == 7
    assert seen["cmd"][0] == str(fake_py)
    assert seen["env"]["SOLAR_APP_IN_VENV"] == "1"


def test_daily_pipeline_defaults_both_dirs_to_the_repo(monkeypatch):
    seen = {}
    monkeypatch.setattr(daily_pipeline, "_relaunch", lambda: None)
    monkeypatch.setattr(daily_pipeline, "_orchestrator_main",
                        lambda argv: seen.update(argv=argv) or 0)
    assert daily_pipeline.main([]) == 0
    argv = seen["argv"]
    assert argv[argv.index("--data-dir") + 1] == os.path.join(str(ROOT), "data")
    assert argv[argv.index("--app-dir") + 1] == str(ROOT)


def test_daily_pipeline_does_not_override_explicit_dirs(monkeypatch):
    seen = {}
    monkeypatch.setattr(daily_pipeline, "_relaunch", lambda: None)
    monkeypatch.setattr(daily_pipeline, "_orchestrator_main",
                        lambda argv: seen.update(argv=argv) or 0)
    daily_pipeline.main(["--data-dir", "D:\\d", "--app-dir", "D:\\a", "--only",
                         "strings"])
    argv = seen["argv"]
    assert argv.count("--data-dir") == 1 and "D:\\d" in argv
    assert argv.count("--app-dir") == 1 and "D:\\a" in argv
    assert "--only" in argv and "strings" in argv


def test_daily_pipeline_does_not_override_explicit_dirs_equals_form(monkeypatch):
    # `_with_default` has a second recognition path — `a.startswith(flag +
    # "=")` — for the `--flag=value` spelling. Only the space-separated form
    # was covered above; this pins the `=` form too.
    seen = {}
    monkeypatch.setattr(daily_pipeline, "_relaunch", lambda: None)
    monkeypatch.setattr(daily_pipeline, "_orchestrator_main",
                        lambda argv: seen.update(argv=argv) or 0)
    daily_pipeline.main(["--data-dir=D:\\d", "--app-dir=D:\\a", "--only",
                         "strings"])
    argv = seen["argv"]
    assert argv.count("--data-dir") == 0 and "--data-dir=D:\\d" in argv
    assert argv.count("--app-dir") == 0 and "--app-dir=D:\\a" in argv
    assert "--only" in argv and "strings" in argv


def test_relaunch_is_a_noop_when_already_the_venv_interpreter(monkeypatch):
    # The third early-return in `relaunch`: VENV_PY exists AND is the running
    # interpreter. This is the ordinary production case (the Scheduled Task
    # invokes the venv's own python.exe directly), so it must not spawn a
    # subprocess — if it ever did, that's a silent extra process layer, not a
    # crash, so make a regression loud instead.
    monkeypatch.delenv("SOLAR_APP_IN_VENV", raising=False)
    monkeypatch.setattr(_venv, "VENV_PY", sys.executable)

    def _fail_if_called(cmd, **kw):
        raise AssertionError(
            "subprocess.run must not be called when already the venv interpreter")
    monkeypatch.setattr(_venv.subprocess, "run", _fail_if_called)
    assert _venv.relaunch(str(ROOT / "app.py")) is None


def test_same_interpreter_falls_back_when_samefile_raises(monkeypatch):
    # `same_interpreter`'s except-OSError branch (realpath + normcase) exists
    # for paths `os.path.samefile` cannot stat. Force that branch directly and
    # check both outcomes it must still get right.
    def _raise(a, b):
        raise OSError("cannot stat")
    monkeypatch.setattr(os.path, "samefile", _raise)
    assert _venv.same_interpreter("C:\\same\\python.exe",
                                  "C:\\same\\python.exe") is True
    assert _venv.same_interpreter("C:\\same\\python.exe",
                                  "C:\\other\\python.exe") is False
