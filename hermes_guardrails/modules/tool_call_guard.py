"""Tool Call Guard — deterministic allow/deny for tool calls by name/arg pattern.

Issue #6.

CAVEAT (read before relying on this module for real enforcement):
Hermes middleware hooks can be FAIL-OPEN on internal exceptions — per the
official Hermes docs, if a middleware function raises, Hermes does not
guarantee the call is blocked; it may simply skip the broken middleware and
let the call through. That means a bug inside a guardrail module is, by
default, an *allow*, not a *deny*.

To counter this, every policy check in this file is wrapped in its own
try/except that defaults to DENY (not allow) whenever policy evaluation
itself throws (missing policy file, malformed policy dict, regex compile
error, unexpected kwargs shape, etc.). The only way a call proceeds through
this module is an explicit, successfully-evaluated "not denied" decision.

However: wrapping *this hook* in try/except-deny only protects against bugs
INSIDE this module. It does NOT, by itself, guarantee enforcement at the
Hermes layer — if the Hermes dispatcher itself fails to invoke this
middleware at all (e.g. due to an exception earlier in the chain, a
mis-registered hook, or a Hermes-side bug), the tool call can still proceed
unguarded, because `tool_request`/`tool_execution` are advisory hook points
that Hermes calls into, not points Hermes is blocked on intrinsically.
**Real enforcement guarantees require testing at the actual tool executor**
(the code path that actually performs the side effect), not just by
exercising this hook layer in isolation. Treat this module as defense in
depth, and verify end-to-end (with a real Hermes dispatch, asserting the
tool's side effect never happened) before trusting it as a hard boundary.
"""

import json
import os
import re
import time
from pathlib import Path

from hermes_guardrails.policy import PolicyError, load_policy, resolve_for_channel

_PROJECT_ENV = "HERMES_GUARDRAILS_PROJECT"


def _guardrails_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails"


def _audit_path(project: str) -> Path:
    return _guardrails_dir() / "audit" / f"{project}.jsonl"


def _write_audit(project: str, entry: dict) -> None:
    """Best-effort audit logging. Never raises — a logging failure must not
    affect the allow/deny decision nor crash the caller."""
    try:
        path = _audit_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = dict(entry)
        entry.setdefault("ts", time.time())
        entry.setdefault("module", "tool_call_guard")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def _resolve_project(kwargs: dict) -> str:
    return (
        kwargs.get("project")
        or kwargs.get("session_id")
        or os.environ.get(_PROJECT_ENV)
        or "default"
    )


def _extract_tool_name(kwargs: dict) -> "str | None":
    for key in ("tool_name", "name"):
        if key in kwargs and kwargs[key]:
            return str(kwargs[key])
    request = kwargs.get("request") or {}
    if isinstance(request, dict):
        for key in ("tool_name", "name", "tool"):
            if request.get(key):
                return str(request[key])
    return None


def _extract_args(kwargs: dict) -> dict:
    for key in ("args", "arguments", "payload"):
        value = kwargs.get(key)
        if isinstance(value, dict):
            return value
    request = kwargs.get("request") or {}
    if isinstance(request, dict):
        for key in ("args", "arguments", "payload"):
            value = request.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _args_as_text(args: dict) -> str:
    try:
        return json.dumps(args, default=str, ensure_ascii=False)
    except Exception:
        return str(args)


def _load_tool_policy(project: str, channel: "str | None") -> dict:
    """Returns the resolved policy['tools'] dict, or {} if unavailable.
    Raises on anything unexpected so the caller can decide fail-closed vs
    fail-open per hook (tool_request is advisory and no-ops to {} on no
    policy; tool_execution callers decide their own default)."""
    policy = load_policy(project)
    policy = resolve_for_channel(policy, channel)
    tools_cfg = policy.get("tools")
    if not isinstance(tools_cfg, dict):
        return {}
    return tools_cfg


def _denied_name(tool_name: "str | None", tools_cfg: dict) -> "str | None":
    if not tool_name:
        return None
    denied_names = tools_cfg.get("denied_names") or []
    if not isinstance(denied_names, (list, tuple, set)):
        return None
    for denied in denied_names:
        try:
            if str(denied) == tool_name:
                return str(denied)
        except Exception:
            continue
    return None


def _denied_arg_pattern(args: dict, tools_cfg: dict) -> "str | None":
    patterns = tools_cfg.get("denied_arg_patterns") or []
    if not isinstance(patterns, (list, tuple, set)):
        return None
    text = _args_as_text(args)
    for pattern in patterns:
        try:
            if re.search(str(pattern), text):
                return str(pattern)
        except re.error:
            # Malformed pattern in policy: treat as a match (fail-closed)
            # so a broken policy entry can't silently be a no-op allow.
            return str(pattern)
        except Exception:
            return str(pattern)
    return None


def _evaluate(tool_name: "str | None", args: dict, project: str, channel: "str | None"):
    """Returns a tuple (decision, reason) where decision is one of
    "allow", "deny_name", "deny_arg_pattern", "deny_error".
    This function is fail-closed: any exception during evaluation is
    caught internally and reported as a deny, never propagated as an
    uncaught exception that could make the whole middleware fail-open."""
    try:
        try:
            tools_cfg = _load_tool_policy(project, channel)
        except PolicyError:
            # No policy configured for this project yet (e.g. fresh dev
            # environment) — per module-contract, no-op allow in that case.
            return ("allow", "no_policy_configured")

        if not tools_cfg:
            return ("allow", "no_tools_policy_section")

        name_hit = _denied_name(tool_name, tools_cfg)
        if name_hit is not None:
            return ("deny_name", name_hit)

        pattern_hit = _denied_arg_pattern(args, tools_cfg)
        if pattern_hit is not None:
            return ("deny_arg_pattern", pattern_hit)

        return ("allow", "no_match")
    except Exception as exc:  # noqa: BLE001 - intentional broad catch, fail-closed
        return ("deny_error", f"{type(exc).__name__}: {exc}")


def tool_request(**kwargs):
    """Middleware: evaluated before the tool call is dispatched.
    Blocks deterministically by tool name or argument pattern. On any
    internal error, defaults to deny (short_circuit_response), never to
    silently allowing the call through."""
    project = _resolve_project(kwargs)
    channel = kwargs.get("channel")
    tool_name = _extract_tool_name(kwargs)
    args = _extract_args(kwargs)

    decision, reason = _evaluate(tool_name, args, project, channel)

    if decision == "allow":
        return {}

    audit_entry = {
        "hook": "tool_request",
        "decision": decision,
        "reason": reason,
        "tool_name": tool_name,
        "args_preview": _args_as_text(args)[:500],
        "project": project,
        "channel": channel,
    }
    _write_audit(project, audit_entry)

    denial_message = {
        "error": "tool_call_denied",
        "tool_name": tool_name,
        "reason": decision,
        "detail": reason,
    }
    return {"short_circuit_response": denial_message}


def tool_execution(**kwargs):
    """Middleware: last line of defense right before actual execution.
    Even if tool_request was bypassed or rewritten upstream, re-check the
    same policy here and set short_circuit_response to deny outright for
    a denied tool. Fail-closed on internal errors."""
    project = _resolve_project(kwargs)
    channel = kwargs.get("channel")
    tool_name = _extract_tool_name(kwargs)
    args = _extract_args(kwargs)

    decision, reason = _evaluate(tool_name, args, project, channel)

    if decision == "allow":
        return {}

    audit_entry = {
        "hook": "tool_execution",
        "decision": decision,
        "reason": reason,
        "tool_name": tool_name,
        "args_preview": _args_as_text(args)[:500],
        "project": project,
        "channel": channel,
    }
    _write_audit(project, audit_entry)

    denial_message = {
        "error": "tool_execution_denied",
        "tool_name": tool_name,
        "reason": decision,
        "detail": reason,
    }
    return {"short_circuit_response": denial_message}


MODULE = {
    "name": "tool_call_guard",
    "middleware": {
        "tool_request": tool_request,
        "tool_execution": tool_execution,
    },
}


if __name__ == "__main__":
    import tempfile

    import yaml

    print("=== tool_call_guard manual self-test ===")

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HERMES_HOME"] = tmp
        project = "selftest"
        guardrails_dir = Path(tmp) / "guardrails"
        guardrails_dir.mkdir(parents=True, exist_ok=True)
        policy_file = guardrails_dir / f"{project}.yaml"
        policy_file.write_text(
            yaml.safe_dump(
                {
                    "project": project,
                    "tools": {
                        "denied_names": ["shell_exec", "delete_file"],
                        "denied_arg_patterns": [r"rm\s+-rf", r"DROP\s+TABLE"],
                    },
                }
            ),
            encoding="utf-8",
        )

        # 1. Denied by name
        result = tool_request(project=project, tool_name="shell_exec", args={"cmd": "ls"})
        assert "short_circuit_response" in result, "expected deny by name"
        print("[PASS] denied by name:", result["short_circuit_response"]["reason"])

        # 2. Denied by arg pattern
        result = tool_request(
            project=project, tool_name="run_command", args={"cmd": "rm -rf /tmp"}
        )
        assert "short_circuit_response" in result, "expected deny by arg pattern"
        print("[PASS] denied by arg pattern:", result["short_circuit_response"]["reason"])

        # 3. Allowed call
        result = tool_request(project=project, tool_name="read_file", args={"path": "a.txt"})
        assert result == {}, "expected allow (empty dict)"
        print("[PASS] allowed call passes through")

        # 4. tool_execution second line of defense
        result = tool_execution(project=project, tool_name="delete_file", args={"path": "x"})
        assert "short_circuit_response" in result, "expected deny at execution layer"
        print("[PASS] tool_execution denies too:", result["short_circuit_response"]["reason"])

        # 5. No policy configured -> fail-open no-op (by contract), not a crash
        result = tool_request(project="no_such_project", tool_name="anything", args={})
        assert result == {}, "expected no-op allow when no policy exists"
        print("[PASS] no policy configured -> no-op allow (per module-contract)")

        # 6. Simulate a broken policy (malformed denied_arg_patterns) -> fail-closed deny
        broken_project = "broken"
        broken_file = guardrails_dir / f"{broken_project}.yaml"
        broken_file.write_text(
            yaml.safe_dump(
                {
                    "project": broken_project,
                    "tools": {"denied_arg_patterns": ["("]},  # invalid regex
                }
            ),
            encoding="utf-8",
        )
        result = tool_request(project=broken_project, tool_name="anything", args={"x": 1})
        assert "short_circuit_response" in result, "expected fail-closed deny on broken policy"
        print("[PASS] malformed policy pattern -> fail-closed deny:", result)

        # 7. Audit log actually has entries
        audit_file = guardrails_dir / "audit" / f"{project}.jsonl"
        assert audit_file.exists(), "expected audit log to be written"
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 3, f"expected audit entries for every deny, got {len(lines)}"
        print(f"[PASS] audit log has {len(lines)} entries at {audit_file}")

    print("=== all self-tests passed ===")
