import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]


def test_editable_project_version_matches_project_metadata() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        project_version = tomllib.load(pyproject_file)["project"]["version"]

    with (PROJECT_ROOT / "uv.lock").open("rb") as lock_file:
        locked_packages = tomllib.load(lock_file)["package"]

    editable_project = next(
        package
        for package in locked_packages
        if package["name"] == "object-datamosh" and package.get("source") == {"editable": "."}
    )

    assert editable_project["version"] == project_version
