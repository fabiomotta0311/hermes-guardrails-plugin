"""PII Guard: detect privacy-shield/privacy-gateway plugins and defer to
them when present, else fall back to a simple regex-based PII redactor
for audit logging (pre_llm_call hook).

Issue #8.

Detection strategy (best-effort)
---------------------------------
Hermes does not (as of this writing) expose a stable, documented API to
query "is plugin X enabled" from inside another plugin's module code.
There is no public registry this module could safely import without
risking a hard dependency on internal/undocumented Hermes plugin-loader
internals that may change. So detection here uses two independent,
deliberately loose heuristics, either of which is enough to call a
known plugin "present":

1. Import probe: try to `importlib.import_module()` a handful of likely
   module/package names for each plugin (e.g. "privacy_shield",
   "privacy-shield" normalized to underscore, "hermes_privacy_shield").
   If any import succeeds, we treat the plugin as present.
2. Env var convention: Hermes plugin enablement is commonly surfaced to
   subprocess/plugin code via an env var named
   HERMES_PLUGIN_<NAME>_ENABLED=1 (uppercased, non-alnum -> "_"). If that
   var is truthy, we trust it even if the import probe fails (e.g. the
   plugin runs out-of-process).

IMPORTANT / HONESTY NOTE: this has NOT been verified against a real
Hermes installation that actually ships "privacy-shield" or
"privacy-gateway". The module names, the env var convention, and even
the existence of those plugins under exactly those identifiers are
assumptions based on the issue text and general Hermes plugin
conventions seen elsewhere in this repo (see docs/module-contract.md).
Treat `detect_pii_plugin()` as a best-effort stub: when this module is
actually run inside an environment with one of these plugins installed,
re-check the real module/package name and adjust `_CANDIDATE_IMPORTS`
and/or `_ENV_VAR_NAMES` accordingly. Until then, this will safely fall
through to the own-fallback path, which is correct and safe (fail open
to "do our own redaction", never fail open to "do nothing").
"""

import importlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from hermes_guardrails.policy import PolicyError, load_policy

# Known/likely PII-handling plugins we defer to when present. Keys are
# human-readable names used in logging; values are lists of module/
# package names we try to import as evidence of presence.
_KNOWN_PII_PLUGINS = {
    "privacy-shield": ["privacy_shield", "privacy-shield", "hermes_privacy_shield"],
    "privacy-gateway": ["privacy_gateway", "privacy-gateway", "hermes_privacy_gateway"],
}


def _env_var_name(plugin_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", plugin_name).upper()
    return f"HERMES_PLUGIN_{normalized}_ENABLED"


def _env_says_enabled(plugin_name: str) -> bool:
    value = os.environ.get(_env_var_name(plugin_name), "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def _import_probe(candidates: "list[str]") -> "str | None":
    for candidate in candidates:
        module_name = candidate.replace("-", "_")
        try:
            importlib.import_module(module_name)
            return module_name
        except ImportError:
            continue
        except Exception:
            # Defensive: a broken/partially-installed plugin module should
            # not crash our detection; treat as "not usably present".
            continue
    return None


def detect_pii_plugin() -> "dict | None":
    """Return {"name": ..., "via": "import"|"env"} for the first known PII
    plugin detected present/enabled, or None if neither is detected.

    Best-effort only — see module docstring.
    """
    for plugin_name, candidates in _KNOWN_PII_PLUGINS.items():
        imported_as = _import_probe(candidates)
        if imported_as:
            return {"name": plugin_name, "via": "import", "module": imported_as}
        if _env_says_enabled(plugin_name):
            return {"name": plugin_name, "via": "env", "module": None}
    return None


# --- Own fallback: simple regex-based PII detector/redactor -----------

_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "cpf": re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    # Loose phone pattern: optional country code, groups of digits with
    # separators, 8-13 digits total. Deliberately permissive for v1.
    "phone": re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,3}\)?[\s.-]?)?\d{4,5}[\s.-]?\d{4}\b"),
    # 16-digit credit-card-like sequence, optionally grouped in 4s with
    # spaces or dashes.
    "credit_card": re.compile(r"\b(?:\d[ -]?){15}\d\b"),
}

_REDACTION_ORDER = ("email", "cpf", "credit_card", "phone")


def redact_pii(text: str) -> "tuple[str, list[dict]]":
    """Redact known PII patterns in `text`.

    Returns (redacted_text, matches) where matches is a list of
    {"type": ..., "value": ...} for every match found (pre-redaction
    value — only ever written to the local audit log, never echoed back
    to the model or the user). Order matters: email/cpf/credit_card are
    checked before the loose phone pattern so an email address or CPF
    isn't partially re-matched as a phone number.
    """
    matches = []
    redacted = text
    for kind in _REDACTION_ORDER:
        pattern = _PATTERNS[kind]

        def _sub(m, kind=kind):
            matches.append({"type": kind, "value": m.group(0)})
            return f"[REDACTED_{kind.upper()}]"

        redacted = pattern.sub(_sub, redacted)
    return redacted, matches


# --- Audit logging -----------------------------------------------------


def _audit_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails" / "audit"


def _write_audit(project: str, entry: dict) -> None:
    try:
        audit_dir = _audit_dir()
        audit_dir.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "module": MODULE["name"], **entry}
        with open(audit_dir / f"{project}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Audit logging must never crash the hook path.
        pass


# --- Hook ---------------------------------------------------------------


def pre_llm_call(**kwargs):
    """pre_llm_call hook.

    If a known PII-handling plugin is detected, this module no-ops on
    the actual message content (defers entirely to that plugin) and
    just logs which path is active, once per call.

    If no such plugin is detected, runs the own regex-based fallback:
    detect and redact common PII patterns (email, Brazilian CPF, phone,
    16-digit card-like sequences) in the user message, and write the
    redacted text + match types to the audit log. This v1 fallback does
    NOT block the message and does NOT rewrite what is actually sent to
    the LLM — it only redacts what lands in our own audit trail. A full
    tokenize-and-restore round trip is explicitly out of scope per the
    issue.
    """
    project = kwargs.get("project") or kwargs.get("session_id") or "default"
    user_message = kwargs.get("user_message") or kwargs.get("message") or ""

    plugin = detect_pii_plugin()
    if plugin:
        _write_audit(
            project,
            {
                "event": "pii_guard_deferred",
                "active_path": plugin["name"],
                "detected_via": plugin["via"],
            },
        )
        return

    if not isinstance(user_message, str) or not user_message:
        _write_audit(project, {"event": "pii_guard_fallback_active", "active_path": "own_regex_fallback", "note": "no text to scan"})
        return

    redacted, matches = redact_pii(user_message)
    if matches:
        _write_audit(
            project,
            {
                "event": "pii_guard_redacted",
                "active_path": "own_regex_fallback",
                "redacted_text": redacted,
                "pii_types_found": sorted({m["type"] for m in matches}),
                "match_count": len(matches),
            },
        )
    else:
        _write_audit(
            project,
            {"event": "pii_guard_fallback_active", "active_path": "own_regex_fallback", "note": "no PII patterns matched"},
        )


# Touch load_policy defensively per module-contract.md, even though this
# module doesn't currently read any policy keys of its own — this keeps
# the pattern consistent and future policy keys (e.g. an on/off switch)
# cheap to add without changing the hook signature.
def _policy_noop_check(project: str) -> None:
    try:
        load_policy(project)
    except PolicyError:
        pass


MODULE = {
    "name": "pii_guard",
    "hooks": {
        "pre_llm_call": pre_llm_call,
    },
}


if __name__ == "__main__":
    # Manual self-test.
    sample = (
        "Contact me at jane.doe@example.com or call (11) 98765-4321. "
        "My CPF is 123.456.789-09 and card 4111 1111 1111 1111."
    )
    redacted_text, found = redact_pii(sample)
    print("original :", sample)
    print("redacted :", redacted_text)
    print("matches  :", found)
    assert "jane.doe@example.com" not in redacted_text
    assert "123.456.789-09" not in redacted_text
    assert any(m["type"] == "email" for m in found)
    assert any(m["type"] == "cpf" for m in found)
    assert any(m["type"] == "credit_card" for m in found)

    detected = detect_pii_plugin()
    print("plugin detection result:", detected)

    # Exercise the hook end-to-end (writes to the real audit dir under
    # HERMES_HOME/.hermes if set, harmless for a manual run).
    pre_llm_call(project="pii_guard_selftest", user_message=sample)
    print("self-test OK")
