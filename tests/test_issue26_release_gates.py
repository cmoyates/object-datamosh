from __future__ import annotations

import runpy
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "issue26_release_gates.py"
_NAMESPACE = runpy.run_path(str(_SCRIPT), run_name="issue26_release_gates_test")
SourceIdentity = _NAMESPACE["SourceIdentity"]
capture_identity = cast(Callable[[], Any], _NAMESPACE["capture_identity"])
_capture_identity_globals = cast(dict[str, Any], cast(Any, capture_identity).__globals__)
require_unchanged_identity = cast(
    Callable[[Any, Any], None], _NAMESPACE["require_unchanged_identity"]
)
run_gate = _NAMESPACE["run_gate"]
write_gate_result = _NAMESPACE["write_gate_result"]


def identity(*, git_head: str = "abc", dirty: str = "") -> Any:
    return SourceIdentity(
        dirty=dirty,
        git_head=git_head,
        probe_sha256="probe",
        release_gate_sha256="gate",
        runner_sha256="runner",
        source_tree="tree",
    )


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
