from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "issue26_release_gates.py"
require_unchanged_identity = cast(
    Callable[[dict[str, str], dict[str, str]], None],
    runpy.run_path(str(_SCRIPT), run_name="issue26_release_gates_test")[
        "require_unchanged_identity"
    ],
)


def test_release_gate_identity_accepts_an_unchanged_snapshot() -> None:
    identity = {"git_head": "abc", "dirty": ""}

    require_unchanged_identity(identity, identity.copy())


def test_release_gate_identity_rejects_mid_run_drift() -> None:
    expected = {"git_head": "abc", "dirty": ""}
    changed = {"git_head": "def", "dirty": " M scripts/gate.py"}

    with pytest.raises(RuntimeError, match="identity changed during execution"):
        require_unchanged_identity(expected, changed)
