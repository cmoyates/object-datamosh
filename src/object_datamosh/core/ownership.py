"""Naming and ownership tags for data created by Object Datamosh."""

from collections.abc import Mapping, MutableMapping

OWNED_PREFIX = "ODM_"
OWNERSHIP_TAG = "object_datamosh_owned"


def owned_name(name: str) -> str:
    """Return ``name`` with the deterministic extension prefix."""
    return name if name.startswith(OWNED_PREFIX) else f"{OWNED_PREFIX}{name}"


def mark_owned(properties: MutableMapping[str, object]) -> None:
    """Tag an extension-created custom-property container."""
    properties[OWNERSHIP_TAG] = True


def is_owned(properties: Mapping[str, object]) -> bool:
    """Whether a custom-property container carries the extension's tag."""
    return properties.get(OWNERSHIP_TAG) is True
