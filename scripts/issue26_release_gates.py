from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO / "docs" / "evidence"
EVIDENCE = EVIDENCE_DIR / "issue-26-release-gates.json"
MAX_RETAINED_OUTPUT_BYTES = 64 * 1024
REAL_ESCAPE_GIT_HEAD = "e6628a8a595aaa53416fc205c15f82836c3819ae"
REAL_ESCAPE_PROBE_SHA256 = "576e3252a7244f6144234477879d678cdde550125eab882745991363505600d8"
REAL_ESCAPE_RUNNER_SHA256 = "106be8a7ea82d05f8d17b545432f4a6e96cedd8f660bb4d51b5da36180b770ed"


@dataclass(frozen=True)
class SourceIdentity:
    dirty: str
    evidence_helper_sha256: str
    git_head: str
    probe_sha256: str
    pyproject_sha256: str
    release_gate_sha256: str
    runner_sha256: str
    source_tree: str
    uv_lock_sha256: str


@dataclass(frozen=True)
class GateResult:
    name: str
    command: str
    exit_code: int
    launch_error: str | None
    output_error: str | None
    output_head: str
    output_retained_bytes: int
    output_sha256: str
    output_tail: str
    output_total_bytes: int
    output_truncated: bool
    timed_out: bool
    timeout_seconds: float
    tracked_changes: str


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


def atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as source_stream, temporary.open("xb") as destination_stream:
        shutil.copyfileobj(source_stream, destination_stream)
        destination_stream.flush()
        os.fsync(destination_stream.fileno())
    if sha256_bytes(temporary.read_bytes()) != sha256_bytes(source.read_bytes()):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Published archive verification failed: {destination}")
    temporary.replace(destination)


def signal_process_group(pid: int, signal_number: int) -> None:
    """Signal a gate process group unless it already exited."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal_number)


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
        evidence_helper_sha256=sha256_bytes(
            (REPO / "scripts" / "issue26_evidence.py").read_bytes()
        ),
        git_head=git_output(REPO, "rev-parse", "HEAD"),
        probe_sha256=sha256_bytes((REPO / "scripts" / "issue26_foreground_probe.py").read_bytes()),
        pyproject_sha256=sha256_bytes((REPO / "pyproject.toml").read_bytes()),
        release_gate_sha256=sha256_bytes(Path(__file__).read_bytes()),
        runner_sha256=sha256_bytes(
            (REPO / "scripts" / "run_issue26_foreground_probe.sh").read_bytes()
        ),
        source_tree=git_output(REPO, "rev-parse", "HEAD:src/object_datamosh"),
        uv_lock_sha256=sha256_bytes((REPO / "uv.lock").read_bytes()),
    )


def run_gate(
    name: str,
    arguments: list[str],
    display: str,
    *,
    worktree: Path,
    environment: dict[str, str],
    timeout_seconds: float = 600.0,
    output_close_timeout_seconds: float = 10.0,
    output_termination_timeout_seconds: float = 1.0,
) -> GateResult:
    print(f"$ {display}")
    try:
        process = subprocess.Popen(
            arguments,
            cwd=worktree,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as error:
        message = f"{type(error).__name__}: {error}"
        return GateResult(
            name=name,
            command=display,
            exit_code=127,
            launch_error=message,
            output_error=None,
            output_head=message,
            output_retained_bytes=len(message.encode()),
            output_sha256=sha256_bytes(message.encode()),
            output_tail="",
            output_total_bytes=len(message.encode()),
            output_truncated=False,
            timed_out=False,
            timeout_seconds=timeout_seconds,
            tracked_changes="",
        )
    assert process.stdout is not None
    stdout = process.stdout
    digest = hashlib.sha256()
    head_limit = MAX_RETAINED_OUTPUT_BYTES // 2
    tail_limit = MAX_RETAINED_OUTPUT_BYTES - head_limit
    retained_full = bytearray()
    retained_head = bytearray()
    retained_tail = bytearray()
    total_bytes = 0
    stop_output_reader = threading.Event()
    output_reader_failures: list[str] = []

    def consume_output() -> None:
        nonlocal total_bytes
        selector = selectors.DefaultSelector()
        try:
            os.set_blocking(stdout.fileno(), False)
            selector.register(stdout, selectors.EVENT_READ)
            while not stop_output_reader.is_set():
                for _key, _mask in selector.select(timeout=0.1):
                    try:
                        chunk = os.read(stdout.fileno(), 8192)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        return
                    total_bytes += len(chunk)
                    digest.update(chunk)
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                    full_remaining = MAX_RETAINED_OUTPUT_BYTES - len(retained_full)
                    if full_remaining > 0:
                        retained_full.extend(chunk[:full_remaining])
                    head_remaining = head_limit - len(retained_head)
                    if head_remaining > 0:
                        retained_head.extend(chunk[:head_remaining])
                    retained_tail.extend(chunk)
                    if len(retained_tail) > tail_limit:
                        del retained_tail[:-tail_limit]
        except OSError as error:
            output_reader_failures.append(f"{type(error).__name__}: {error}")
        finally:
            selector.close()

    output_thread = threading.Thread(target=consume_output, name=f"{name}-output")
    output_thread.start()
    timed_out = False
    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        signal_process_group(process.pid, signal.SIGTERM)
        try:
            exit_code = process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            signal_process_group(process.pid, signal.SIGKILL)
            exit_code = process.wait(timeout=5.0)
    output_thread.join(timeout=output_close_timeout_seconds)
    output_error: str | None = None
    if output_thread.is_alive():
        output_error = f"Output pipe remained open after gate process exited: {display}"
        signal_process_group(process.pid, signal.SIGTERM)
        output_thread.join(timeout=output_termination_timeout_seconds)
    if output_thread.is_alive():
        signal_process_group(process.pid, signal.SIGKILL)
        output_thread.join(timeout=output_termination_timeout_seconds)
    if output_thread.is_alive():
        stop_output_reader.set()
        output_thread.join(timeout=1.0)
        output_error = f"Output reader could not be stopped after gate process exited: {display}"
    elif output_reader_failures:
        output_error = f"Output reader failed for gate {display}: {output_reader_failures[0]}"
    stdout.close()
    truncated = total_bytes > MAX_RETAINED_OUTPUT_BYTES
    if truncated:
        output_head = retained_head.decode("utf-8", errors="replace")
        output_tail = retained_tail.decode("utf-8", errors="replace")
        retained_bytes = len(retained_head) + len(retained_tail)
    else:
        output_head = retained_full.decode("utf-8", errors="replace")
        output_tail = ""
        retained_bytes = len(retained_full)
    return GateResult(
        name=name,
        command=display,
        exit_code=exit_code,
        launch_error=None,
        output_error=output_error,
        output_head=output_head,
        output_retained_bytes=retained_bytes,
        output_sha256=digest.hexdigest(),
        output_tail=output_tail,
        output_total_bytes=total_bytes,
        output_truncated=truncated,
        timed_out=timed_out,
        timeout_seconds=timeout_seconds,
        tracked_changes=git_output(worktree, "status", "--porcelain", "--untracked-files=no"),
    )


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
        "pyproject_sha256": identity.pyproject_sha256,
        "source_tree": identity.source_tree,
        "uv_lock_sha256": identity.uv_lock_sha256,
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


def validate_embedded_event_log(payload: dict[str, object], label: str) -> None:
    event_log = payload.get("event_log_jsonl")
    if not isinstance(event_log, str):
        raise RuntimeError(f"{label} receipt does not embed its event log")
    if sha256_bytes(event_log.encode()) != payload.get("event_log_sha256_before_completion"):
        raise RuntimeError(f"Embedded {label} event-log digest does not match its receipt")


def validate_foreground_receipt(identity: SourceIdentity) -> tuple[bytes, dict[str, object]]:
    path = EVIDENCE_DIR / "issue-26-foreground-result.json"
    content = path.read_bytes()
    payload = json.loads(content)
    if payload.get("success") is not True:
        raise RuntimeError("Foreground receipt is not successful")
    expected = {
        "evidence_helper_sha256": identity.evidence_helper_sha256,
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
    validate_embedded_event_log(payload, "foreground")
    return content, payload


def validate_real_escape_receipt(identity: SourceIdentity) -> tuple[bytes, dict[str, object]]:
    path = EVIDENCE_DIR / "issue-26-real-escape-result.json"
    content = path.read_bytes()
    payload = json.loads(content)
    if payload.get("success") is not True:
        raise RuntimeError("Real-Escape receipt is not successful")
    expected_identity = {
        "extension_source_tree": identity.source_tree,
        "git_head": REAL_ESCAPE_GIT_HEAD,
        "probe_sha256": REAL_ESCAPE_PROBE_SHA256,
        "runner_sha256": REAL_ESCAPE_RUNNER_SHA256,
    }
    for field, expected in expected_identity.items():
        if payload.get(field) != expected:
            raise RuntimeError(
                f"Real-Escape receipt {field} mismatch: "
                f"expected {expected!r}, got {payload.get(field)!r}"
            )
    validate_embedded_event_log(payload, "real-Escape")
    event_log = payload["event_log_jsonl"]
    assert isinstance(event_log, str)
    events = [json.loads(line) for line in event_log.splitlines()]
    for marker in ("raw_render_active", "processing_escape_ready"):
        started = [
            event
            for event in events
            if event.get("event") == "external_escape_send_started"
            and event.get("marker") == marker
        ]
        sent = [
            event
            for event in events
            if event.get("event") == "external_escape_sent" and event.get("marker") == marker
        ]
        if len(started) != 1 or len(sent) != 1 or started[0]["time"] >= sent[0]["time"]:
            raise RuntimeError(f"Real-Escape receipt lacks ordered System Events markers: {marker}")
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise RuntimeError("Real-Escape receipt has no evidence summary")
    for scenario in ("raw_escape_cancel", "processing_escape_cancel"):
        scenario_evidence = evidence.get(scenario)
        if not isinstance(scenario_evidence, dict):
            raise RuntimeError(f"Real-Escape receipt lacks {scenario}")
        if scenario_evidence.get("blender_escape_event_simulated"):
            raise RuntimeError(f"Real-Escape receipt used simulation for {scenario}")
        completed = scenario_evidence.get("completed_frames")
        if not isinstance(completed, list) or completed != list(range(1, len(completed) + 1)):
            raise RuntimeError(f"Real-Escape receipt has invalid prefix for {scenario}")
        if scenario_evidence.get("controller_cleared") is not True:
            raise RuntimeError(f"Real-Escape receipt lacks cleanup for {scenario}")
    raw = evidence["raw_escape_cancel"]
    processing = evidence["processing_escape_cancel"]
    assert isinstance(raw, dict) and isinstance(processing, dict)
    if raw.get("escape_sent_during_render") is not True:
        raise RuntimeError("Real-Escape receipt lacks active-render injection evidence")
    if processing.get("escape_received_by_runtime") is not True:
        raise RuntimeError("Real-Escape receipt lacks processing runtime receipt")
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
    real_escape_content, real_escape = validate_real_escape_receipt(identity)
    if arguments.update_evidence and EVIDENCE.is_file():
        current_aggregate = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        referenced = {
            REPO / entry["path"]
            for entry in current_aggregate.get("gate_receipts", [])
            if isinstance(entry.get("path"), str)
        }
        for candidate in EVIDENCE_DIR.glob("issue-26-gate-*.json"):
            if candidate not in referenced:
                candidate.unlink()

    run_root = Path(tempfile.mkdtemp(prefix="object-datamosh-issue26-gates-"))
    worktree = run_root / "worktree"
    build_output = run_root / "build"
    build_output.mkdir()
    receipt_directory = run_root / "receipts"
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
    environment["UV_FROZEN"] = "1"
    environment["UV_PROJECT_ENVIRONMENT"] = str(run_root / "environment")
    quoted_blender = shlex.quote(str(blender_bin))
    specifications = [
        (
            "environment-sync",
            ["uv", "sync", "--frozen", "--no-install-project"],
            "uv sync --frozen --no-install-project",
        ),
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

    def stop_after_receipting_failure(message: str) -> None:
        if arguments.update_evidence and gate_receipts:
            atomic_write(
                EVIDENCE_DIR / "issue-26-last-failed-gate.json",
                gate_receipts[-1].read_bytes(),
            )
        raise RuntimeError(message)

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
            if result.launch_error is not None:
                stop_after_receipting_failure(
                    f"Release gate could not launch: {display}: {result.launch_error}"
                )
            if result.output_error is not None:
                stop_after_receipting_failure(
                    f"Release gate output capture failed: {result.output_error}"
                )
            if result.timed_out:
                stop_after_receipting_failure(
                    f"Release gate timed out after {result.timeout_seconds}s: {display}"
                )
            if result.tracked_changes:
                stop_after_receipting_failure(
                    f"Release gate modified tracked files: {display}: {result.tracked_changes}"
                )
            if result.exit_code != 0:
                stop_after_receipting_failure(
                    f"Release gate failed ({result.exit_code}): {display}"
                )

        archives = sorted(build_output.glob("object_datamosh-*.zip"))
        if len(archives) != 1:
            raise RuntimeError(f"Expected one newly built ZIP, found: {archives}")
        built_archive = archives[0]
        built_archive_content = built_archive.read_bytes()
        built_archive_sha256 = sha256_bytes(built_archive_content)
        published_archive = REPO / "dist" / built_archive.name
        published_archive.parent.mkdir(exist_ok=True)
        if published_archive.exists() and published_archive.read_bytes() != built_archive_content:
            published_archive = published_archive.with_name(
                f"{published_archive.stem}-{built_archive_sha256[:12]}{published_archive.suffix}"
            )
        if published_archive.exists() and published_archive.read_bytes() != built_archive_content:
            raise RuntimeError(f"Archive-name digest collision: {published_archive}")

        require_unchanged_identity(identity, capture_identity())
        latest_foreground_content, _ = validate_foreground_receipt(identity)
        if latest_foreground_content != foreground_content:
            raise RuntimeError("Foreground receipt changed during release gates")
        latest_real_escape_content, _ = validate_real_escape_receipt(identity)
        if latest_real_escape_content != real_escape_content:
            raise RuntimeError("Real-Escape receipt changed during release gates")

        if not published_archive.exists():
            atomic_copy(built_archive, published_archive)

        gate_receipt_entries: list[dict[str, str]] = []
        promoted_gate_receipts: set[Path] = set()
        for path in gate_receipts:
            content = path.read_bytes()
            digest = sha256_bytes(content)
            if arguments.update_evidence:
                destination = EVIDENCE_DIR / f"{path.stem}-{digest[:12]}{path.suffix}"
                if destination.exists():
                    if destination.read_bytes() != content:
                        raise RuntimeError(f"Gate-receipt digest collision: {destination}")
                else:
                    atomic_write(destination, content)
                promoted_gate_receipts.add(destination)
                recorded_path = destination.relative_to(REPO)
            else:
                recorded_path = path.relative_to(run_root)
            gate_receipt_entries.append({"path": str(recorded_path), "sha256": digest})
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
            "real_escape": {
                "git_head": real_escape["git_head"],
                "receipt_sha256": sha256_bytes(real_escape_content),
            },
            "pyproject_sha256": identity.pyproject_sha256,
            "release_gate_script_sha256": identity.release_gate_sha256,
            "source_tree": identity.source_tree,
            "success": True,
            "uv_lock_sha256": identity.uv_lock_sha256,
        }
        aggregate = EVIDENCE if arguments.update_evidence else receipt_directory / EVIDENCE.name
        atomic_write(
            aggregate,
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
        )
        if arguments.update_evidence:
            (EVIDENCE_DIR / "issue-26-last-failed-gate.json").unlink(missing_ok=True)
            for old_receipt in EVIDENCE_DIR.glob("issue-26-gate-*.json"):
                if old_receipt not in promoted_gate_receipts:
                    old_receipt.unlink()
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
