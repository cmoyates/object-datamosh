from __future__ import annotations

from pathlib import Path

README = Path(__file__).parents[1] / "README.md"


def read_readme() -> str:
    return " ".join(README.read_text(encoding="utf-8").split())


def test_readme_explains_the_independent_full_frame_feedback_choices() -> None:
    readme = read_readme()

    assert "### History color versus effect coverage" in readme
    assert "History Source chooses the pixels available for history color" in readme
    assert "Mode chooses where that history color can affect the output" in readme
    assert "outside the current matte is always clean current beauty" in readme
    assert "decaying temporal coverage" in readme


def test_readme_explains_full_frame_artistic_tradeoffs_and_pre_roll() -> None:
    readme = read_readme()

    assert "### Choosing Target Only or Full Frame" in readme
    assert "correct object color, texture, and recognizable form" in readme
    assert "background-only pre-roll" in readme
    assert "previous processed frame, not the previous raw beauty frame" in readme
    assert "Accurate motion vectors can preserve coherent structure" in readme
    assert "quantization and diffusion deliberately break motion compensation" in readme
    assert "not literal compressed-video bitstream corruption" in readme


def test_readme_documents_full_frame_recovery_and_reprocessing() -> None:
    readme = read_readme()

    assert "### Full Frame resets, recovery, and reprocessing" in readme
    assert "Every reset starts a new independent history segment" in readme
    assert "multiple reset frames" in readme
    assert "retained raw beauty, vector, and matte passes" in readme
    assert "without rerendering the 3D scene" in readme
    assert "Missing History: Reset" in readme


def test_readme_links_the_full_frame_release_validation_record() -> None:
    readme = read_readme()

    assert "[Full Frame release verification](docs/full-frame-release-verification.md)" in readme
