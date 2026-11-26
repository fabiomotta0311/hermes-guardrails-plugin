"""
Module auto-discovery.

Every file in this directory (except __init__.py and files starting with
"_" or "test_") is imported automatically and must expose a module-level
`MODULE` dict with this shape:

    MODULE = {
        "name": "scope_intent_guard",
        "hooks": {
            "pre_llm_call": some_function,       # optional
            "post_llm_call": some_function,      # optional
            "pre_tool_call": some_function,       # optional
            "post_tool_call": some_function,      # optional
        },
        "middleware": {
            "llm_request": some_function,         # optional
            "llm_execution": some_function,        # optional
            "tool_request": some_function,         # optional
            "tool_execution": some_function,       # optional
        },
    }

Only declare the hooks/middleware your module actually implements — omit
the rest. This file is the ONLY shared import point; it never needs
editing when a new module is added, which is what keeps parallel
development across modules conflict-free.
"""

import importlib
import logging
import pkgutil

logger = logging.getLogger("motta_guardrails.modules")

_SKIP_PREFIXES = ("_", "test_")


def load_modules():
    modules = []
    package = __name__
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith(_SKIP_PREFIXES):
            continue
        try:
            mod = importlib.import_module(f"{package}.{info.name}")
        except Exception:
            logger.exception("motta-guardrails: failed to import module file %s", info.name)
            continue
        declared = getattr(mod, "MODULE", None)
        if not isinstance(declared, dict) or "name" not in declared:
            logger.warning("motta-guardrails: %s has no valid MODULE dict, skipping", info.name)
            continue
        modules.append(declared)
    return modules
