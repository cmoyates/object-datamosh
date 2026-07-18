# Object Datamosh

Object Datamosh is a modern Blender extension targeting Blender 5.0.0. The installable extension
package lives in `src/object_datamosh`.

## Development

Install the development environment from the repository root:

```bash
uv sync
```

Run the project's static type checker from the repository root so `ty` discovers the uv `.venv`
and `pyproject.toml`:

```bash
uv run ty check
```

Run the test suite with:

```bash
uv run pytest
```

The `ty` configuration checks authored Python sources and tests while excluding generated files,
build output, virtual environments, and Blender extension distribution artifacts.

Blender provides `bpy` only inside its Python runtime. Blender-facing modules must remain separate
from the pure Python processing core under `src/object_datamosh/core`. The project does not
globally suppress unresolved imports; when Blender integration code is added, use compatible
Blender 5.0 development stubs if available, or document and narrowly suppress only unsupported
dynamic `bpy` APIs.
