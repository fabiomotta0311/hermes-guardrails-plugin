"""Scope & Intent Guard v1 — blocks out-of-scope messages before the LLM call."""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from hermes_guardrails.policy import PolicyError, load_policy, resolve_for_channel

logger = logging.getLogger("motta_guardrails.modules.scope_intent_guard")

DEFAULT_CANONICAL_FALLBACK = (
    "I can't help with that — it's outside the scope of this project. "
    "Please rephrase your request to stay within the project's supported topics."
)

_KWARG_PROJECT_KEYS = ("project", "project_name", "project_id")
_KWARG_CHANNEL_KEYS = ("channel", "channel_name")
_KWARG_MESSAGE_KEYS = (
    "user_message",
    "message",
    "prompt",
    "text",
    "input",
    "content",
)


def _audit_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails" / "audit"


def _write_audit_log(project: str, reason: str, matched_topic: str, message_excerpt: str) -> None:
    """Append one JSON line per block decision to the project's audit log."""
    try:
        audit_dir = _audit_dir()
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / f"{project or 'unknown'}.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "module": "scope_intent_guard",
            "project": project,
            "reason": reason,
            "matched_topic": matched_topic,
            "message_excerpt": message_excerpt[:500],
        }
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("scope_intent_guard: failed to write audit log for project %s", project)


def _first_kwarg(kwargs: dict, keys) -> "str | None":
    for key in keys:
        value = kwargs.get(key)
        if value:
            return value
    return None


def _extract_message_text(kwargs: dict) -> str:
    """Defensively pull the user message text out of whatever shape Hermes
    passes. May be a plain string, or a dict/object with common fields, or
    nested under 'request'/'payload'.
    """
    candidate = _first_kwarg(kwargs, _KWARG_MESSAGE_KEYS)

    if candidate is None:
        for container_key in ("request", "payload", "args"):
            container = kwargs.get(container_key)
            if isinstance(container, dict):
                candidate = _first_kwarg(container, _KWARG_MESSAGE_KEYS)
                if candidate:
                    break

    if candidate is None:
        return ""

    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, dict):
        return str(candidate.get("text") or candidate.get("content") or candidate)
    # list of message dicts (e.g. chat history) — take the last entry's text
    if isinstance(candidate, list) and candidate:
        last = candidate[-1]
        if isinstance(last, dict):
            return str(last.get("content") or last.get("text") or "")
        return str(last)
    return str(candidate)


def _extract_project(kwargs: dict) -> "str | None":
    project = _first_kwarg(kwargs, _KWARG_PROJECT_KEYS)
    if project:
        return project
    for container_key in ("request", "payload", "args", "context", "session"):
        container = kwargs.get(container_key)
        if isinstance(container, dict):
            project = _first_kwarg(container, _KWARG_PROJECT_KEYS)
            if project:
                return project
    return None


def _extract_channel(kwargs: dict) -> "str | None":
    channel = _first_kwarg(kwargs, _KWARG_CHANNEL_KEYS)
    if channel:
        return channel
    for container_key in ("request", "payload", "args", "context", "session"):
        container = kwargs.get(container_key)
        if isinstance(container, dict):
            channel = _first_kwarg(container, _KWARG_CHANNEL_KEYS)
            if channel:
                return channel
    return None


def find_forbidden_match(message: str, forbidden_topics, allowed_topics=None):
    """Case-insensitive keyword/regex match of `message` against
    `forbidden_topics`. Each entry may be a plain keyword/phrase or a regex
    pattern (tried as regex first, falling back to literal substring match
    if it's not a valid pattern). If `allowed_topics` is given and the
    message also matches one of them, the forbidden match is suppressed
    (allow-list takes precedence — e.g. "forbidden: finance" but
    "allowed: finance report for project X").

    Returns the matched topic string, or None if nothing matched / message
    is blank / forbidden_topics is empty.
    """
    if not message or not forbidden_topics:
        return None

    for topic in forbidden_topics:
        if not topic:
            continue
        matched = False
        try:
            if re.search(topic, message, re.IGNORECASE):
                matched = True
        except re.error:
            if topic.lower() in message.lower():
                matched = True

        if matched:
            if allowed_topics:
                for allowed in allowed_topics:
                    if not allowed:
                        continue
                    try:
                        if re.search(allowed, message, re.IGNORECASE):
                            return None
                    except re.error:
                        if allowed.lower() in message.lower():
                            return None
            return topic

    return None


def pre_llm_call(**kwargs):
    """Observer-only hook: logs that a pre-LLM check point was reached for
    this project/message. Does not block — blocking happens in the
    llm_execution middleware below.
    """
    project = _extract_project(kwargs)
    message = _extract_message_text(kwargs)
    logger.debug(
        "scope_intent_guard: pre_llm_call observed project=%s message_len=%d",
        project,
        len(message),
    )
    return None


def llm_execution(**kwargs):
    """Middleware: checks the incoming message against the project's
    forbidden_topics policy. If it matches (and isn't rescued by an
    allowed_topics override), writes an audit log entry and returns a
    short_circuit_response so the real LLM is never called.

    Fails open (no-op, pass through) if the policy can't be loaded, so a
    missing/broken policy file never breaks the chat flow.
    """
    project = _extract_project(kwargs)
    if not project:
        logger.debug("scope_intent_guard: no project identified in kwargs, passing through")
        return None

    try:
        policy = load_policy(project)
    except PolicyError:
        logger.debug("scope_intent_guard: no/invalid policy for project %s, passing through", project)
        return None

    channel = _extract_channel(kwargs)
    policy = resolve_for_channel(policy, channel)

    scope = policy.get("scope") or {}
    forbidden_topics = scope.get("forbidden_topics") or []
    allowed_topics = scope.get("allowed_topics") or []
    canonical_fallback = scope.get("canonical_fallback") or DEFAULT_CANONICAL_FALLBACK

    if not forbidden_topics:
        return None

    message = _extract_message_text(kwargs)
    matched_topic = find_forbidden_match(message, forbidden_topics, allowed_topics)

    if matched_topic is None:
        return None

    _write_audit_log(
        project=project,
        reason=f"message matched forbidden topic '{matched_topic}'",
        matched_topic=matched_topic,
        message_excerpt=message,
    )

    return {"short_circuit_response": canonical_fallback}


MODULE = {
    "name": "scope_intent_guard",
    "hooks": {
        "pre_llm_call": pre_llm_call,
    },
    "middleware": {
        "llm_execution": llm_execution,
    },
}


if __name__ == "__main__":
    # Manual self-test (no real Hermes needed) — exercises the matcher
    # logic directly against fake policy/kwargs shapes.
    passed = 0
    failed = 0

    def check(label, condition):
        global passed, failed
        if condition:
            passed += 1
            print(f"PASS: {label}")
        else:
            failed += 1
            print(f"FAIL: {label}")

    # 1. Basic keyword match (case-insensitive)
    match = find_forbidden_match(
        "Can you help me with my CRYPTO investment portfolio?",
        ["crypto", "politics"],
    )
    check("basic case-insensitive keyword match", match == "crypto")

    # 2. No match when message is clean
    match = find_forbidden_match(
        "Can you help me write a Python unit test?",
        ["crypto", "politics"],
    )
    check("no match on in-scope message", match is None)

    # 3. Regex pattern match
    match = find_forbidden_match(
        "What's your opinion on the election results?",
        [r"\belection(s)?\b", "crypto"],
    )
    check("regex pattern match", match == r"\belection(s)?\b")

    # 4. Allowed topic rescues a forbidden match
    match = find_forbidden_match(
        "Give me the finance report for project Atlas",
        ["finance"],
        allowed_topics=["finance report for project Atlas"],
    )
    check("allowed_topics overrides forbidden match", match is None)

    # 5. Empty forbidden list never matches
    match = find_forbidden_match("anything goes here", [])
    check("empty forbidden_topics list -> no match", match is None)

    # 6. llm_execution middleware end-to-end with a monkeypatched policy loader
    import hermes_guardrails.modules.scope_intent_guard as sig_module

    fake_policy = {
        "project": "demo-project",
        "scope": {
            "forbidden_topics": ["weather forecast"],
            "canonical_fallback": "This assistant only handles demo-project support topics.",
        },
    }

    _orig_load_policy = sig_module.load_policy
    sig_module.load_policy = lambda project: fake_policy
    try:
        result = sig_module.llm_execution(
            project="demo-project",
            user_message="What's the weather forecast for tomorrow?",
        )
        check(
            "llm_execution short-circuits on forbidden topic",
            isinstance(result, dict)
            and result.get("short_circuit_response") == fake_policy["scope"]["canonical_fallback"],
        )

        result = sig_module.llm_execution(
            project="demo-project",
            user_message="How do I reset my demo-project password?",
        )
        check("llm_execution passes through on in-scope message", result is None)

        def _raise_policy_error(project):
            raise sig_module.PolicyError("no policy file")

        sig_module.load_policy = _raise_policy_error
        result = sig_module.llm_execution(project="unknown-project", user_message="anything")
        check("llm_execution no-ops when policy can't be loaded", result is None)
    finally:
        sig_module.load_policy = _orig_load_policy

    # 7. pre_llm_call hook never raises and returns None (observer-only)
    try:
        hook_result = pre_llm_call(project="demo-project", user_message="hi there")
        check("pre_llm_call hook is observer-only (returns None, no crash)", hook_result is None)
    except Exception as exc:  # pragma: no cover
        check(f"pre_llm_call hook raised unexpectedly: {exc}", False)

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)
