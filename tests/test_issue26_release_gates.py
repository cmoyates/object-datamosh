from __future__ import annotations

import json
import os
import runpy
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "issue26_release_gates.py"
_NAMESPACE = runpy.run_path(str(_SCRIPT), run_name="issue26_release_gates_test")
SourceIdentity = _NAMESPACE["SourceIdentity"]
capture_identity = cast(Callable[[], Any], _NAMESPACE["capture_identity"])
gate_environment = _NAMESPACE["gate_environment"]
interrupt_release_gate = _NAMESPACE["interrupt_release_gate"]
_capture_identity_globals = cast(dict[str, Any], cast(Any, capture_identity).__globals__)
require_unchanged_identity = cast(
    Callable[[Any, Any], None], _NAMESPACE["require_unchanged_identity"]
)
run_gate = _NAMESPACE["run_gate"]
write_release_failure = _NAMESPACE["write_release_failure"]
signal_process_group = _NAMESPACE["signal_process_group"]
terminate_timed_out_process = _NAMESPACE["terminate_timed_out_process"]
validate_real_escape_timing = _NAMESPACE["validate_real_escape_timing"]
_signal_process_group_globals = cast(dict[str, Any], signal_process_group.__globals__)
write_gate_result = _NAMESPACE["write_gate_result"]


def identity(*, git_head: str = "abc", dirty: str = "") -> Any:
    return SourceIdentity(
        dirty=dirty,
        evidence_helper_sha256="helper",
        git_head=git_head,
        probe_sha256="probe",
        pyproject_sha256="pyproject",
        release_gate_sha256="gate",
        runner_sha256="runner",
        source_tree="tree",
        uv_lock_sha256="lock",
    )


def test_gate_environment_isolates_blender_and_drops_python_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/untrusted/python")
    monkeypatch.setenv("BLENDER_USER_SCRIPTS", "/untrusted/blender")

    environment = gate_environment(tmp_path)

    assert "PYTHONPATH" not in environment
    assert environment["BLENDER_USER_SCRIPTS"] == str(tmp_path / "blender-user" / "scripts")
    assert environment["BLENDER_USER_EXTENSIONS"] == str(
        tmp_path / "blender-user" / "extensions"
    )


def real_escape_events() -> list[dict[str, object]]:
    return [
        {"event": "raw_render_active", "stage": "raw_escape_cancel", "frame": 1, "time": 1.0},
        {
            "event": "external_escape_send_started",
            "marker": "raw_render_active",
            "time": 1.2,
        },
        {"event": "external_escape_sent", "marker": "raw_render_active", "time": 1.3},
        {"event": "render_complete", "stage": "raw_escape_cancel", "frame": 1, "time": 2.0},
        {
            "event": "runtime",
            "stage": "raw_escape_cancel",
            "phase": "CANCELLED",
            "completed_work": 1,
            "phase_total_work": 100,
            "time": 2.1,
        },
        {"event": "processing_escape_ready", "time": 3.0},
        {
            "event": "external_escape_send_started",
            "marker": "processing_escape_ready",
            "time": 3.1,
        },
        {
            "event": "external_escape_sent",
            "marker": "processing_escape_ready",
            "time": 3.2,
        },
        {
            "event": "runtime",
            "stage": "processing_escape_cancel",
            "phase": "CANCELLING",
            "time": 3.3,
        },
        {
            "event": "runtime",
            "stage": "processing_escape_cancel",
            "phase": "CANCELLED",
            "time": 3.4,
        },
    ]


def test_real_escape_timing_accepts_bound_raw_and_processing_events() -> None:
    validate_real_escape_timing(real_escape_events())


def test_real_escape_timing_rejects_raw_send_outside_render_interval() -> None:
    events = real_escape_events()
    events[1]["time"] = 2.2
    events[2]["time"] = 2.3

    with pytest.raises(RuntimeError, match="inside an active render interval"):
        validate_real_escape_timing(events)


def test_real_escape_timing_rejects_a_normally_completed_raw_run() -> None:
    events = real_escape_events()
    events[4]["phase"] = "COMPLETED"
    events[4]["completed_work"] = 100

    with pytest.raises(RuntimeError, match="strict partial cancellation"):
        validate_real_escape_timing(events)


@pytest.mark.parametrize("signal_number", [1, 2, 15])
def test_release_interruption_signals_enter_controlled_cleanup(signal_number: int) -> None:
    with pytest.raises(RuntimeError, match=f"interrupted by signal {signal_number}"):
        interrupt_release_gate(signal_number, None)


def test_release_failure_receipt_survives_a_post_gate_error(tmp_path: Path) -> None:
    receipt = tmp_path / "last-failure.json"

    write_release_failure(
        receipt,
        RuntimeError("archive missing"),
        identity=identity(),
        results=[],
    )

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["success"] is False
    assert payload["error"] == "RuntimeError: archive missing"
    assert payload["last_gate"] is None


def test_release_gate_identity_accepts_an_unchanged_snapshot() -> None:
    expected = identity()

    require_unchanged_identity(expected, expected)


def test_release_gate_identity_rejects_mid_run_drift() -> None:
    with pytest.raises(RuntimeError, match="identity changed during execution"):
        require_unchanged_identity(identity(), identity(git_head="def", dirty=" M gate.py"))


def initialize_empty_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        cwd=path,
        check=True,
    )


def test_gate_receipt_retains_a_bounded_failure_tail(tmp_path: Path) -> None:
    initialize_empty_repository(tmp_path)
    marker = "FINAL_FAILURE_MARKER"
    result = run_gate(
        "synthetic",
        [
            "/usr/bin/python3",
            "-c",
            f"import sys; sys.stdout.write('A' * 70000 + '{marker}'); sys.exit(17)",
        ],
        "synthetic failure",
        worktree=tmp_path,
        environment={},
    )

    receipt = write_gate_result(result, identity=identity(), directory=tmp_path)

    assert result.exit_code == 17
    assert result.output_truncated
    assert marker in result.output_tail
    assert receipt.is_file()


def test_gate_receipt_retains_complete_output_below_limit(tmp_path: Path) -> None:
    initialize_empty_repository(tmp_path)
    result = run_gate(
        "medium",
        ["/usr/bin/python3", "-c", "print('B' * 40959)"],
        "medium output",
        worktree=tmp_path,
        environment={},
    )

    assert not result.output_truncated
    assert result.output_total_bytes == 40960
    assert len(result.output_head.encode()) == 40960
    assert result.output_tail == ""


def test_unresponsive_process_after_sigkill_returns_a_receiptable_failure() -> None:
    class UnresponsiveProcess:
        pid = 999_999_999

        def wait(self, *, timeout: float) -> int:
            raise subprocess.TimeoutExpired("synthetic", timeout)

    exit_code, error = terminate_timed_out_process(
        cast(Any, UnresponsiveProcess()),
        term_timeout_seconds=0.0,
        kill_timeout_seconds=0.0,
    )

    assert exit_code == 124
    assert error == "Gate process did not exit after SIGKILL"


def test_signal_process_group_accepts_an_already_exited_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exited_process(_pid: int, _signal_number: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(_signal_process_group_globals["os"], "killpg", exited_process)

    signal_process_group(123, 15)


def test_interrupt_during_gate_launch_stops_the_owned_process(tmp_path: Path) -> None:
    initialize_empty_repository(tmp_path)
    previous_handler = signal.signal(signal.SIGTERM, interrupt_release_gate)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="interrupted by signal"):
            run_gate(
                "launch-interrupt",
                [
                    "/usr/bin/python3",
                    "-c",
                    "import os, signal, time; "
                    "os.kill(os.getppid(), signal.SIGTERM); time.sleep(30)",
                ],
                "launch interrupt",
                worktree=tmp_path,
                environment={},
            )
    finally:
        signal.signal(signal.SIGTERM, previous_handler)

    assert time.monotonic() - started < 5.0


def test_gate_receipt_captures_a_timeout(tmp_path: Path) -> None:
    initialize_empty_repository(tmp_path)
    result = run_gate(
        "timeout",
        ["/usr/bin/python3", "-c", "import time; print('started', flush=True); time.sleep(10)"],
        "synthetic timeout",
        worktree=tmp_path,
        environment={},
        timeout_seconds=0.05,
    )

    receipt = write_gate_result(result, identity=identity(), directory=tmp_path)

    assert result.timed_out
    assert "started" in result.output_head
    assert receipt.is_file()


def test_gate_receipt_captures_and_kills_an_inherited_output_pipe(tmp_path: Path) -> None:
    initialize_empty_repository(tmp_path)
    child_pid_path = tmp_path / "child.pid"
    ready_path = tmp_path / "child.ready"
    child_code = (
        "import os, pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "pathlib.Path(sys.argv[2]).touch(); "
        "time.sleep(30)"
    )
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, "
        f"{str(child_pid_path)!r}, {str(ready_path)!r}]); "
        f"ready = pathlib.Path({str(ready_path)!r}); "
        "[(time.sleep(0.01)) for _ in range(100) if not ready.exists()]; "
        "print('parent complete', flush=True)"
    )
    result = run_gate(
        "inherited-pipe",
        ["/usr/bin/python3", "-c", parent_code],
        "inherited output pipe",
        worktree=tmp_path,
        environment={},
        output_close_timeout_seconds=0.05,
        output_termination_timeout_seconds=0.05,
    )

    receipt = write_gate_result(result, identity=identity(), directory=tmp_path)
    child_pid = int(child_pid_path.read_text())
    child_alive = True
    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            child_alive = False
            break
        time.sleep(0.01)

    assert result.output_error is not None
    assert "left descendant processes running" in result.output_error
    assert "parent complete" in result.output_head
    assert not child_alive
    assert receipt.is_file()


def test_gate_kills_a_closed_output_descendant_before_it_can_mutate(tmp_path: Path) -> None:
    initialize_empty_repository(tmp_path)
    marker = tmp_path / "late-marker"
    child_code = (
        "import pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(1); pathlib.Path(sys.argv[1]).touch()"
    )
    parent_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, {str(marker)!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "print('parent complete', flush=True)"
    )

    result = run_gate(
        "closed-descendant",
        ["/usr/bin/python3", "-c", parent_code],
        "closed output descendant",
        worktree=tmp_path,
        environment={},
        process_group_timeout_seconds=0.05,
    )
    time.sleep(1.1)

    assert result.output_error is not None
    assert "left descendant processes running" in result.output_error
    assert not marker.exists()


def test_gate_receipt_captures_a_launch_failure(tmp_path: Path) -> None:
    initialize_empty_repository(tmp_path)
    result = run_gate(
        "missing",
        [str(tmp_path / "does-not-exist")],
        "missing executable",
        worktree=tmp_path,
        environment={},
    )

    receipt = write_gate_result(result, identity=identity(), directory=tmp_path)

    assert result.exit_code == 127
    assert "FileNotFoundError" in result.launch_error
    assert receipt.is_file()


def test_capture_identity_detects_a_mid_run_project_file_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src" / "object_datamosh").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts").mkdir()
    for relative, content in {
        "src/object_datamosh/__init__.py": "",
        "scripts/issue26_evidence.py": "# helper\n",
        "scripts/issue26_foreground_probe.py": "# probe\n",
        "scripts/issue26_release_gates.py": "# gate\n",
        "scripts/run_issue26_foreground_probe.sh": "# runner\n",
        "pyproject.toml": "[project]\nname = 'fixture'\nversion = '0'\n",
        "uv.lock": "version = 1\nrevision = 1\nrequires-python = '>=3.11'\n",
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setitem(_capture_identity_globals, "REPO", tmp_path)
    monkeypatch.setitem(
        _capture_identity_globals,
        "__file__",
        str(tmp_path / "scripts" / "issue26_release_gates.py"),
    )
    expected = capture_identity()

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'changed'\n", encoding="utf-8")
    changed = capture_identity()

    with pytest.raises(RuntimeError, match="identity changed during execution"):
        require_unchanged_identity(expected, changed)
