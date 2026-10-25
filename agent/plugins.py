"""Finds the files that modules add to agent/commands/ and agent/routes/.

Each module adds features by dropping in a new file, so adding a feature never means
editing the files that load them.
"""

import importlib
import logging
import pkgutil
from types import ModuleType

logger = logging.getLogger("agent")


def load_plugins(package: ModuleType, *, tolerate_errors: bool = False) -> list[ModuleType]:
    """Imports every public module in ``package`` in alphabetical order.

    HTTP deployments can tolerate a broken optional route so core endpoints such
    as the race health check still start. CLI command loading remains strict and
    surfaces import errors immediately.
    """
    names = sorted(m.name for m in pkgutil.iter_modules(package.__path__) if not m.name.startswith("_"))
    loaded: list[ModuleType] = []
    for name in names:
        module_name = f"{package.__name__}.{name}"
        try:
            loaded.append(importlib.import_module(module_name))
        except Exception:
            if not tolerate_errors:
                raise
            logger.exception("Optional plugin %s could not be imported", module_name)
    return loaded
