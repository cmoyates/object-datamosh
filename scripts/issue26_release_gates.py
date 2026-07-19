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
    output: str
    output_sha256: str
    output_tail: list[str]


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


def run_gate(arguments: list[str], display: str) -> GateResult:
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
        output=output,
        output_sha256=output_sha256,
        output_tail=output.splitlines()[-20:],
    )
    if process.returncode != 0:
        raise RuntimeError(f"Release gate failed ({process.returncode}): {display}")
    return result


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    temporary.write_bytes(content)
    temporary.replace(path)


def require_unchanged_identity(expected: dict[str, str], actual: dict[str, str]) -> None:
    if actual != expected:
        raise RuntimeError(
            "Release-gate source identity changed during execution: "
            f"expected {expected!r}, got {actual!r}"
        )


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
    lock_path = Path(f"/tmp/object-datamosh-issue26-evidence-{os.getuid()}.lock")
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

    source_scope = ("src", "tests", "scripts", "pyproject.toml", "uv.lock")
    dirty = git_output("status", "--porcelain", "--untracked-files=all", "--", *source_scope)
    if dirty:
        raise RuntimeError(f"Release-gate source is dirty:\n{dirty}")

    current_head = git_output("rev-parse", "HEAD")
    current_source_tree = git_output("rev-parse", "HEAD:src/object_datamosh")
    probe_path = REPO / "scripts" / "issue26_foreground_probe.py"
    runner_path = REPO / "scripts" / "run_issue26_foreground_probe.sh"
    release_gate_path = Path(__file__)
    probe_sha256 = sha256_bytes(probe_path.read_bytes())
    runner_sha256 = sha256_bytes(runner_path.read_bytes())
    release_gate_sha256 = sha256_bytes(release_gate_path.read_bytes())
    foreground_receipt_path = REPO / "docs" / "evidence" / "issue-26-foreground-result.json"
    foreground_receipt_content = foreground_receipt_path.read_bytes()
    foreground = json.loads(foreground_receipt_content)
    if foreground.get("success") is not True:
        raise RuntimeError("Foreground receipt is not successful")
    expected_foreground_fields = {
        "git_head": current_head,
        "extension_source_tree": current_source_tree,
        "probe_sha256": probe_sha256,
        "runner_sha256": runner_sha256,
    }
    for field, expected in expected_foreground_fields.items():
        if foreground.get(field) != expected:
            raise RuntimeError(
                f"Foreground receipt {field} is stale: "
                f"expected {expected!r}, got {foreground.get(field)!r}"
            )
    event_log_jsonl = foreground.get("event_log_jsonl")
    if not isinstance(event_log_jsonl, str):
        raise RuntimeError("Foreground receipt does not embed its event log")
    if sha256_bytes(event_log_jsonl.encode("utf-8")) != foreground.get(
        "event_log_sha256_before_completion"
    ):
        raise RuntimeError("Embedded foreground event-log digest does not match its receipt")

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
            "event_log_sha256": foreground["event_log_sha256_before_completion"],
            "git_head": foreground["git_head"],
            "receipt_sha256": sha256_bytes(foreground_receipt_content),
        },
        "gates": [asdict(gate) for gate in gates],
        "git_head": current_head,
        "release_gate_script_sha256": release_gate_sha256,
        "source_tree": current_source_tree,
        "success": True,
    }

    target = (
        EVIDENCE
        if arguments.update_evidence
        else Path(tempfile.mkdtemp(prefix="object-datamosh-issue26-gates-")) / EVIDENCE.name
    )
    if foreground_receipt_path.read_bytes() != foreground_receipt_content:
        raise RuntimeError("Foreground receipt changed during the release-gate run")
    final_identity = {
        "dirty": git_output("status", "--porcelain", "--untracked-files=all", "--", *source_scope),
        "git_head": git_output("rev-parse", "HEAD"),
        "source_tree": git_output("rev-parse", "HEAD:src/object_datamosh"),
        "probe_sha256": sha256_bytes(probe_path.read_bytes()),
        "runner_sha256": sha256_bytes(runner_path.read_bytes()),
        "release_gate_sha256": sha256_bytes(release_gate_path.read_bytes()),
    }
    expected_final_identity = {
        "dirty": "",
        "git_head": current_head,
        "source_tree": current_source_tree,
        "probe_sha256": probe_sha256,
        "runner_sha256": runner_sha256,
        "release_gate_sha256": release_gate_sha256,
    }
    require_unchanged_identity(expected_final_identity, final_identity)

    target.parent.mkdir(parents=True, exist_ok=True)
    receipt_content = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(target, receipt_content)
    print(f"Release-gate receipt: {target}")


if __name__ == "__main__":
    main()
