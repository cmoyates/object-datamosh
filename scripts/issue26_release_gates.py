from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "evidence" / "issue-26-release-gates.json"


@dataclass(frozen=True)
class GateResult:
    command: str
    exit_code: int
    output_file: str
    output_sha256: str
    output_tail: list[str]


@dataclass(frozen=True)
class GateExecution:
    result: GateResult
    output: bytes


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def git_output(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run_gate(arguments: list[str], display: str) -> GateExecution:
    process = subprocess.run(
        arguments,
        cwd=REPO,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    output = process.stdout + process.stderr
    print(f"$ {display}")
    print(output, end="" if output.endswith("\n") or not output else "\n")
    output_bytes = output.encode("utf-8")
    output_sha256 = sha256_bytes(output_bytes)
    result = GateResult(
        command=display,
        exit_code=process.returncode,
        output_file=f"issue-26-gate-output-{output_sha256}.log",
        output_sha256=output_sha256,
        output_tail=output.splitlines()[-20:],
    )
    if process.returncode != 0:
        raise RuntimeError(f"Release gate failed ({process.returncode}): {display}")
    return GateExecution(result=result, output=output_bytes)


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    temporary.write_bytes(content)
    temporary.replace(path)


def prune_gate_outputs(directory: Path, keep: set[str]) -> None:
    for output in directory.glob("issue-26-gate-output-*.log"):
        if output.name not in keep:
            output.unlink()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run and receipt the issue #26 release gates")
    parser.add_argument(
        "--update-evidence",
        action="store_true",
        help="atomically replace the tracked successful gate receipt",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    lock_path = Path(f"/tmp/object-datamosh-issue26-gates-{os.getuid()}.lock")
    lock_stream = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("Another issue #26 release-gate run is active") from error

    blender_bin_value = os.environ.get("BLENDER_BIN")
    if not blender_bin_value:
        raise RuntimeError("Set BLENDER_BIN to the tested Blender executable")
    blender_bin = Path(blender_bin_value).expanduser().resolve()
    if not blender_bin.is_file():
        raise RuntimeError(f"BLENDER_BIN is not a file: {blender_bin}")

    dirty = git_output(
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        "src",
        "tests",
        "scripts",
        "pyproject.toml",
        "uv.lock",
    )
    if dirty:
        raise RuntimeError(f"Release-gate source is dirty:\n{dirty}")

    current_head = git_output("rev-parse", "HEAD")
    current_source_tree = git_output("rev-parse", "HEAD:src/object_datamosh")
    foreground_receipt_path = REPO / "docs" / "evidence" / "issue-26-foreground-result.json"
    foreground = json.loads(foreground_receipt_path.read_text(encoding="utf-8"))
    if foreground.get("success") is not True:
        raise RuntimeError("Foreground receipt is not successful")
    expected_foreground_fields = {
        "git_head": current_head,
        "extension_source_tree": current_source_tree,
        "probe_sha256": sha256_bytes(
            (REPO / "scripts" / "issue26_foreground_probe.py").read_bytes()
        ),
        "runner_sha256": sha256_bytes(
            (REPO / "scripts" / "run_issue26_foreground_probe.sh").read_bytes()
        ),
    }
    for field, expected in expected_foreground_fields.items():
        if foreground.get(field) != expected:
            raise RuntimeError(
                f"Foreground receipt {field} is stale: "
                f"expected {expected!r}, got {foreground.get(field)!r}"
            )
    trace_name = foreground.get("event_log_file")
    if not isinstance(trace_name, str) or Path(trace_name).name != trace_name:
        raise RuntimeError(f"Unsafe foreground trace name: {trace_name!r}")
    foreground_trace = foreground_receipt_path.parent / trace_name
    if not foreground_trace.is_file():
        raise RuntimeError(f"Foreground trace is missing: {foreground_trace}")
    if sha256_bytes(foreground_trace.read_bytes()) != foreground.get(
        "event_log_sha256_before_completion"
    ):
        raise RuntimeError("Foreground trace digest does not match its receipt")

    dist = REPO / "dist"
    dist.mkdir(exist_ok=True)
    quoted_blender = shlex.quote(str(blender_bin))
    gates = [
        run_gate(["uv", "run", "ty", "check"], "uv run ty check"),
        run_gate(["uv", "run", "pytest", "-q"], "uv run pytest -q"),
        run_gate(["uv", "run", "ruff", "check", "."], "uv run ruff check ."),
        run_gate(
            [
                str(blender_bin),
                "--background",
                "--factory-startup",
                "--python",
                "tests/blender_smoke_test.py",
            ],
            f"{quoted_blender} --background --factory-startup --python tests/blender_smoke_test.py",
        ),
        run_gate(
            [
                str(blender_bin),
                "--command",
                "extension",
                "validate",
                "src/object_datamosh",
            ],
            f"{quoted_blender} --command extension validate src/object_datamosh",
        ),
        run_gate(
            [
                str(blender_bin),
                "--command",
                "extension",
                "build",
                "--source-dir",
                "src/object_datamosh",
                "--output-dir",
                "dist",
            ],
            f"{quoted_blender} --command extension build "
            "--source-dir src/object_datamosh --output-dir dist",
        ),
    ]

    archives = sorted(dist.glob("object_datamosh-*.zip"))
    if len(archives) != 1:
        raise RuntimeError(f"Expected one Object Datamosh ZIP in dist, found: {archives}")
    archive = archives[0]
    receipt = {
        "archive": {
            "path": str(archive.relative_to(REPO)),
            "sha256": sha256_bytes(archive.read_bytes()),
            "size_bytes": archive.stat().st_size,
        },
        "blender_bin": str(blender_bin),
        "foreground": {
            "git_head": foreground["git_head"],
            "receipt_sha256": sha256_bytes(foreground_receipt_path.read_bytes()),
            "trace_file": trace_name,
            "trace_sha256": foreground["event_log_sha256_before_completion"],
        },
        "gates": [asdict(execution.result) for execution in gates],
        "git_head": current_head,
        "release_gate_script_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "source_tree": current_source_tree,
        "success": True,
    }

    target = (
        EVIDENCE
        if arguments.update_evidence
        else Path(tempfile.mkdtemp(prefix="object-datamosh-issue26-gates-")) / EVIDENCE.name
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    previous_keep: set[str] = set()
    if target.is_file():
        previous = json.loads(target.read_text(encoding="utf-8"))
        previous_keep = {
            gate["output_file"]
            for gate in previous.get("gates", [])
            if isinstance(gate.get("output_file"), str)
        }
    prune_gate_outputs(target.parent, previous_keep)

    output_names: set[str] = set()
    for execution in gates:
        output_path = target.parent / execution.result.output_file
        output_names.add(output_path.name)
        if output_path.is_file():
            if output_path.read_bytes() != execution.output:
                raise RuntimeError(f"Gate-output digest collision: {output_path}")
        else:
            atomic_write(output_path, execution.output)

    receipt_content = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(target, receipt_content)
    prune_gate_outputs(target.parent, output_names)
    print(f"Release-gate receipt: {target}")


if __name__ == "__main__":
    main()
