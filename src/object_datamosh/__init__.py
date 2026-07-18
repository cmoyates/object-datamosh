"""Object Datamosh Blender extension entry point.

Blender integration is imported lazily so the pure core remains importable without ``bpy``.
"""


def register() -> None:
    """Register the extension with Blender."""
    from .ui import register as register_ui

    register_ui()


def unregister() -> None:
    """Unregister the extension from Blender."""
    from .ui import unregister as unregister_ui

    unregister_ui()
