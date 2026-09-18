"""OmniAtlas — installable package namespace.

The published wheel nests the implementation under ``omni_atlas.core``
while the repository keeps its ``src/core`` layout (symbol-level
documentation anchors depend on those paths). Registering a ``core``
alias keeps the intra-package imports working in both worlds.

@source layout: pyproject.toml#sources
"""

import sys as _sys

try:  # installed wheel layout
    from . import core as _core
except ImportError:  # editable / repository layout
    import core as _core  # type: ignore[no-redef]

_sys.modules.setdefault("core", _core)
