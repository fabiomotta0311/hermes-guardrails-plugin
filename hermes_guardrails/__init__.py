"""
Motta Guardrails — Hermes plugin entry point.

Registers the core hook/middleware wiring points and dispatches to every
module discovered in `hermes_guardrails.modules`. Individual modules never
touch this file — they live as one file each under modules/ and declare
which hook/middleware points they implement via a MODULE dict. This keeps
parallel development conflict-free: N modules, N independent files.
"""

import logging

from hermes_guardrails.modules import load_modules
from hermes_guardrails import policy as policy_mod

logger = logging.getLogger("motta_guardrails")

_MODULES = None  # lazy-loaded list of module objects, populated on register()


def _modules():
    global _MODULES
    if _MODULES is None:
        _MODULES = load_modules()
        logger.debug("motta-guardrails: loaded modules: %s", [m.get("name") for m in _MODULES])
    return _MODULES


def _dispatch_hook(hook_name, **kwargs):
    """Call every module's handler for this hook, in module list order.
    A module that doesn't implement this hook is skipped. Exceptions in one
    module are logged and do not block the others (fail-soft at the
    dispatcher level; individual modules decide their own fail-open/closed
    behavior for middleware return values).
    """
    results = []
    for mod in _modules():
        handler = (mod.get("hooks") or {}).get(hook_name)
        if handler is None:
            continue
        try:
            results.append(handler(**kwargs))
        except Exception:
            logger.exception("motta-guardrails: module %s failed on hook %s", mod.get("name"), hook_name)
    return results


def _dispatch_middleware(mw_name, **kwargs):
    """Call every module's middleware handler for this point, in order.
    Each handler receives the current kwargs (which may already have been
    mutated by an earlier module) and may return a dict to merge into the
    effective payload for the next module / for Hermes itself.
    """
    current = dict(kwargs)
    for mod in _modules():
        handler = (mod.get("middleware") or {}).get(mw_name)
        if handler is None:
            continue
        try:
            result = handler(**current)
            if isinstance(result, dict):
                current.update(result)
        except Exception:
            logger.exception("motta-guardrails: module %s failed on middleware %s", mod.get("name"), mw_name)
    return current


# --- Hooks (observers) -----------------------------------------------------

def _pre_llm_call(**kwargs):
    logger.debug("motta-guardrails: pre_llm_call reached")
    return _dispatch_hook("pre_llm_call", **kwargs)


def _post_llm_call(**kwargs):
    logger.debug("motta-guardrails: post_llm_call reached")
    return _dispatch_hook("post_llm_call", **kwargs)


def _pre_tool_call(**kwargs):
    logger.debug("motta-guardrails: pre_tool_call reached")
    return _dispatch_hook("pre_tool_call", **kwargs)


def _post_tool_call(**kwargs):
    logger.debug("motta-guardrails: post_tool_call reached")
    return _dispatch_hook("post_tool_call", **kwargs)


# --- Middleware (can rewrite / short-circuit) ------------------------------

def _llm_request(**kwargs):
    logger.debug("motta-guardrails: llm_request middleware reached")
    return {"request": _dispatch_middleware("llm_request", **kwargs).get("request", kwargs.get("request"))}


def _llm_execution(**kwargs):
    logger.debug("motta-guardrails: llm_execution middleware reached")
    merged = _dispatch_middleware("llm_execution", **kwargs)
    next_call = kwargs["next_call"]
    request = merged.get("request", kwargs.get("request"))
    # If a module set a "short_circuit_response", skip the real call entirely.
    if merged.get("short_circuit_response") is not None:
        return merged["short_circuit_response"]
    return next_call(request)


def _tool_request(**kwargs):
    logger.debug("motta-guardrails: tool_request middleware reached")
    merged = _dispatch_middleware("tool_request", **kwargs)
    return {"args": merged.get("args", kwargs.get("args"))}


def _tool_execution(**kwargs):
    logger.debug("motta-guardrails: tool_execution middleware reached")
    merged = _dispatch_middleware("tool_execution", **kwargs)
    next_call = kwargs["next_call"]
    payload = merged.get("payload", kwargs.get("payload"))
    if merged.get("short_circuit_response") is not None:
        return merged["short_circuit_response"]
    return next_call(payload)


def register(ctx):
    """Entry point called by Hermes when the plugin is enabled."""
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_llm_call", _post_llm_call)
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)

    ctx.register_middleware("llm_request", _llm_request)
    ctx.register_middleware("llm_execution", _llm_execution)
    ctx.register_middleware("tool_request", _tool_request)
    ctx.register_middleware("tool_execution", _tool_execution)

    logger.info("motta-guardrails: registered, %d module(s) loaded", len(_modules()))
