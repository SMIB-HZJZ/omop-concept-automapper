"""Discovery of workflow plugins and extra pipeline definitions.

Plugins are Python modules that register steps with ``@node``/``@router``. They are loaded from
- the ``WORKFLOW_PLUGINS`` setting (comma-separated module paths), and
- installed packages exposing the ``omop_mapper.workflow_nodes`` entry-point group.
"""

import importlib
import logging
from importlib.metadata import entry_points

from app.core.config import get_settings
from app.workflows.registry import current_plugin

logger = logging.getLogger(__name__)
ENTRY_POINT_GROUP = "omop_mapper.workflow_nodes"

_loaded = False
load_errors: dict[str, str] = {}


def load_builtin_nodes() -> None:
    import app.workflows.nodes  # noqa: F401  (registers the built-in steps)


def load_plugins(force: bool = False) -> dict[str, str]:
    global _loaded
    if _loaded and not force:
        return load_errors
    load_builtin_nodes()
    load_errors.clear()
    for module in get_settings().workflow_plugin_list:
        token = current_plugin.set(module)
        try:
            importlib.import_module(module)
            logger.info("Loaded workflow plugin %s", module)
        except Exception as exc:
            logger.exception("Could not load workflow plugin %s", module)
            load_errors[module] = f"{type(exc).__name__}: {exc}"
        finally:
            current_plugin.reset(token)
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        token = current_plugin.set(ep.value)
        try:
            ep.load()
            logger.info("Loaded workflow plugin entry point %s", ep.name)
        except Exception as exc:
            logger.exception("Could not load workflow entry point %s", ep.name)
            load_errors[ep.value] = f"{type(exc).__name__}: {exc}"
        finally:
            current_plugin.reset(token)
    _loaded = True
    return load_errors
