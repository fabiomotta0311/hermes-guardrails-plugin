"""
Plugin-root loader shim.

Hermes's plugin loader expects an `__init__.py` directly beside `plugin.yaml`
(the plugin root). The actual implementation lives in the `hermes_guardrails`
package one level down (kept as its own importable package so the module
files under `hermes_guardrails/modules/` can use normal package-relative
imports). This file just re-exports `register` so the loader finds it.
"""

import os
import sys

_PLUGIN_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from hermes_guardrails import register  # noqa: E402,F401
