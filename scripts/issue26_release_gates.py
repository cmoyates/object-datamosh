from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO / "docs" / "evidence"
EVIDENCE = EVIDENCE_DIR / "issue-26-release-gates.json"
MAX_RETAINED_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class SourceIdentity:
    dirty: str
    git_head: str
    probe_sha256: str
    release_gate_sha256: str
    runner_sha256: str
    source_tree: str


@dataclass(frozen=True)
class GateResult:
    name: str
    command: str
    exit_code: int
    output: str
    output_retained_bytes: int
    output_sha256: str
    output_total_bytes: int
    output_truncated: bool


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def git_output(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    temporary.write_bytes(content)
    temporary.replace(path)


def require_unchanged_identity(expected: SourceIdentity, actual: SourceIdentity) -> None:
    if actual != expected:
        raise RuntimeError(
            "Release-gate source identity changed during execution: "
            f"expected {expected!r}, got {actual!r}"
        )


def capture_identity() -> SourceIdentity:
    scope = ("src", "tests", "scripts", "pyproject.toml", "uv.lock")
    return SourceIdentity(
        dirty=git_output(REPO, "status", "--porcelain", "--untracked-files=all", "--", *scope),
        git_head=git_output(REPO, "rev-parse", "HEAD"),
        probe_sha256=sha256_bytes((REPO / "scripts" / "issue26_foreground_probe.py").read_bytes()),
        release_gate_sha256=sha256_bytes(Path(__file__).read_bytes()),
        runner_sha256=sha256_bytes(
            (REPO / "scripts" / "run_issue26_foreground_probe.sh").read_bytes()
        ),
        source_tree=git_output(REPO, "rev-parse", "HEAD:src/object_datamosh"),
    )


def run_gate(
    name: str,
    arguments: list[str],
    display: str,
    *,
    worktree: Path,
    environment: dict[str, str],
) -> GateResult:
    print(f"$ {display}")
    process = subprocess.Popen(
        arguments,
        cwd=worktree,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    digest = hashlib.sha256()
    retained = bytearray()
    total_bytes = 0
    while chunk := process.stdout.read(8192):
        total_bytes += len(chunk)
        digest.update(chunk)
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        remaining = MAX_RETAINED_OUTPUT_BYTES - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
    exit_code = process.wait()
    result = GateResult(
        name=name,
        command=display,
        exit_code=exit_code,
        output=retained.decode("utf-8", errors="replace"),
        output_retained_bytes=len(retained),
        output_sha256=digest.hexdigest(),
        output_total_bytes=total_bytes,
        output_truncated=total_bytes > len(retained),
    )
    if git_output(worktree, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError(f"Gate modified tracked files: {display}")
    return result


def write_gate_result(
    result: GateResult,
    *,
    identity: SourceIdentity,
    directory: Path,
) -> Path:
    path = directory / f"issue-26-gate-{result.name}.json"
    payload = {
        "gate": asdict(result),
        "git_head": identity.git_head,
        "source_tree": identity.source_tree,
    }
    atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())
    return path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run and receipt the issue #26 release gates")
    parser.add_argument(
        "--update-evidence",
        action="store_true",
        help="atomically replace the tracked gate receipts",
    )
    return parser.parse_args()


def validate_foreground_receipt(identity: SourceIdentity) -> tuple[bytes, dict[str, object]]:
    path = EVIDENCE_DIR / "issue-26-foreground-result.json"
    content = path.read_bytes()
    payload = json.loads(content)
    if payload.get("success") is not True:
        raise RuntimeError("Foreground receipt is not successful")
    expected = {
        "git_head": identity.git_head,
        "extension_source_tree": identity.source_tree,
        "probe_sha256": identity.probe_sha256,
        "runner_sha256": identity.runner_sha256,
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise RuntimeError(
                f"Foreground receipt {field} is stale: "
                f"expected {value!r}, got {payload.get(field)!r}"
            )
    event_log = payload.get("event_log_jsonl")
    if not isinstance(event_log, str):
        raise RuntimeError("Foreground receipt does not embed its event log")
    if sha256_bytes(event_log.encode()) != payload.get("event_log_sha256_before_completion"):
        raise RuntimeError("Embedded foreground event-log digest does not match its receipt")
    return content, payload


def main() -> None:
    arguments = parse_arguments()
    lock_path = Path(f"/tmp/object-datamosh-issue26-evidence-{os.getuid()}.lock")
    lock_stream = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("Another issue #26 evidence run is active") from error

    blender_value = os.environ.get("BLENDER_BIN")
    if not blender_value:
        raise RuntimeError("Set BLENDER_BIN to the tested Blender executable")
    blender_bin = Path(blender_value).expanduser().resolve()
    if not blender_bin.is_file():
        raise RuntimeError(f"BLENDER_BIN is not a file: {blender_bin}")

    identity = capture_identity()
    if identity.dirty:
        raise RuntimeError(f"Release-gate source is dirty:\n{identity.dirty}")
    foreground_content, foreground = validate_foreground_receipt(identity)

    run_root = Path(tempfile.mkdtemp(prefix="object-datamosh-issue26-gates-"))
    worktree = run_root / "worktree"
    build_output = run_root / "build"
    receipt_directory = EVIDENCE_DIR if arguments.update_evidence else run_root / "receipts"
    receipt_directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), identity.git_head],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["UV_NO_SYNC"] = "1"
    environment["UV_PROJECT_ENVIRONMENT"] = str(REPO / ".venv")
    quoted_blender = shlex.quote(str(blender_bin))
    specifications = [
        ("ty", ["uv", "run", "ty", "check"], "uv run ty check"),
        ("pytest", ["uv", "run", "pytest", "-q"], "uv run pytest -q"),
        ("ruff", ["uv", "run", "ruff", "check", "."], "uv run ruff check ."),
        (
            "blender-smoke",
            [
                str(blender_bin),
                "--background",
                "--factory-startup",
                "--python",
                "tests/blender_smoke_test.py",
            ],
            f"{quoted_blender} --background --factory-startup --python tests/blender_smoke_test.py",
        ),
        (
            "extension-validate",
            [str(blender_bin), "--command", "extension", "validate", "src/object_datamosh"],
            f"{quoted_blender} --command extension validate src/object_datamosh",
        ),
        (
            "extension-build",
            [
                str(blender_bin),
                "--command",
                "extension",
                "build",
                "--source-dir",
                "src/object_datamosh",
                "--output-dir",
                str(build_output),
            ],
            f"{quoted_blender} --command extension build "
            f"--source-dir src/object_datamosh --output-dir {shlex.quote(str(build_output))}",
        ),
    ]

    results: list[GateResult] = []
    gate_receipts: list[Path] = []
    try:
        for name, command, display in specifications:
            result = run_gate(
                name,
                command,
                display,
                worktree=worktree,
                environment=environment,
            )
            results.append(result)
            gate_receipts.append(
                write_gate_result(result, identity=identity, directory=receipt_directory)
            )
            if result.exit_code != 0:
                raise RuntimeError(f"Release gate failed ({result.exit_code}): {display}")

        archives = sorted(build_output.glob("object_datamosh-*.zip"))
        if len(archives) != 1:
            raise RuntimeError(f"Expected one newly built ZIP, found: {archives}")
        built_archive = archives[0]
        published_archive = REPO / "dist" / built_archive.name
        published_archive.parent.mkdir(exist_ok=True)
        if published_archive.exists():
            if published_archive.read_bytes() != built_archive.read_bytes():
                raise RuntimeError(
                    f"Refusing to replace different existing archive: {published_archive}"
                )
        else:
            shutil.copy2(built_archive, published_archive)

        require_unchanged_identity(identity, capture_identity())
        latest_foreground_content, _ = validate_foreground_receipt(identity)
        if latest_foreground_content != foreground_content:
            raise RuntimeError("Foreground receipt changed during release gates")

        gate_receipt_entries = [
            {
                "path": str(path.relative_to(REPO if arguments.update_evidence else run_root)),
                "sha256": sha256_bytes(path.read_bytes()),
            }
            for path in gate_receipts
        ]
        receipt = {
            "archive": {
                "path": str(published_archive.relative_to(REPO)),
                "sha256": sha256_bytes(published_archive.read_bytes()),
                "size_bytes": published_archive.stat().st_size,
            },
            "blender_bin": str(blender_bin),
            "foreground": {
                "event_log_sha256": foreground["event_log_sha256_before_completion"],
                "git_head": foreground["git_head"],
                "receipt_sha256": sha256_bytes(foreground_content),
            },
            "gate_receipts": gate_receipt_entries,
            "git_head": identity.git_head,
            "release_gate_script_sha256": identity.release_gate_sha256,
            "source_tree": identity.source_tree,
            "success": True,
        }
        aggregate = EVIDENCE if arguments.update_evidence else receipt_directory / EVIDENCE.name
        atomic_write(
            aggregate,
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
        )
        print(f"Release-gate receipt: {aggregate}")
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
        )
        if arguments.update_evidence:
            shutil.rmtree(run_root, ignore_errors=True)
        else:
            print(f"Run artifacts retained at {run_root}")


if __name__ == "__main__":
    main()
