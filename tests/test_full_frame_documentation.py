from __future__ import annotations

from pathlib import Path

from object_datamosh.core.presets import extreme_full_frame_feedback_settings

ROOT = Path(__file__).parents[1]
README = ROOT / "README.md"
MIGRATION_GUIDE = ROOT / "docs" / "extreme-workflow-migration.md"
RELEASE_NOTES = ROOT / "docs" / "release-notes-0.2.0.md"


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


def test_readme_documents_preflight_and_post_run_evidence() -> None:
    readme = read_readme()

    assert "Active: Full Frame / Trail" in readme
    assert "Full-frame history is OFF" in readme
    assert "effective_settings" in readme
    assert "ODM_processing_report.json" in readme
    assert "schema-v2 manifest" in readme
    assert "top-level `TARGET_ONLY`" in readme


def test_readme_has_the_required_extreme_troubleshooting_rows() -> None:
    readme = README.read_text(encoding="utf-8")

    for symptom in (
        "Output upside down",
        "Object looks almost clean",
        "Manifest says `TARGET_ONLY`",
        "Most primary history samples are out of bounds",
        "Trail follows object rather than remaining behind",
        "Output is unchanged outside Hard matte",
    ):
        row = next(line for line in readme.splitlines() if line.startswith(f"| {symptom} |"))
        assert row.count("|") == 4


def test_schema_v2_migration_guide_requires_explicit_safe_reprocessing() -> None:
    guide = " ".join(MIGRATION_GUIDE.read_text(encoding="utf-8").split())

    assert "never guesses missing schema-v2 settings" in guide
    assert "Resume" in guide
    assert "Reprocess" in guide
    assert "Overwrite Processed Frames" in guide
    assert "raw beauty, Vector, and matte" in guide
    assert "does not rerender the 3D scene" in guide
    assert "Target Only remains the global default" in guide


def test_release_notes_match_the_public_extreme_preset() -> None:
    notes = " ".join(RELEASE_NOTES.read_text(encoding="utf-8").split())
    preset = extreme_full_frame_feedback_settings()

    assert "display_top_left_v1" in notes
    assert "effective_settings" in notes
    assert "Same Screen Position" in notes
    assert "screen-space/mixed Trail" in notes
    assert "processing diagnostics" in notes
    for value in (
        preset.persistence,
        preset.trail_decay,
        preset.trail_motion_mix,
        preset.refresh_probability,
        preset.block_size,
        preset.motion_quantization,
        preset.diffusion,
    ):
        assert f"`{value}`" in notes
