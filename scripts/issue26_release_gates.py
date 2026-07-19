from __future__ import annotations

import argparse
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
    result = GateResult(
        command=display,
        exit_code=process.returncode,
        output_sha256=sha256_bytes(output.encode("utf-8")),
        output_tail=output.splitlines()[-20:],
    )
    if process.returncode != 0:
        raise RuntimeError(f"Release gate failed ({process.returncode}): {display}")
    return result


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
    foreground_receipt = REPO / "docs" / "evidence" / "issue-26-foreground-result.json"
    receipt = {
        "archive": {
            "path": str(archive.relative_to(REPO)),
            "sha256": sha256_bytes(archive.read_bytes()),
            "size_bytes": archive.stat().st_size,
        },
        "blender_bin": str(blender_bin),
        "foreground_receipt_sha256": sha256_bytes(foreground_receipt.read_bytes()),
        "gates": [asdict(gate) for gate in gates],
        "git_head": git_output("rev-parse", "HEAD"),
        "release_gate_script_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "source_tree": git_output("rev-parse", "HEAD:src/object_datamosh"),
        "success": True,
    }

    target = (
        EVIDENCE
        if arguments.update_evidence
        else Path(tempfile.mkdtemp(prefix="object-datamosh-issue26-gates-")) / EVIDENCE.name
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    print(f"Release-gate receipt: {target}")


if __name__ == "__main__":
    main()
