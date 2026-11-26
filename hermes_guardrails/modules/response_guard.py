"""
Response Guard — post-generation validation of the LLM's own output.

This module implements the `llm_execution` middleware point. Unlike Scope
Guard (which blocks *before* the real call happens, at `llm_request`/
`pre_llm_call`), Response Guard's whole job is to look at what the model
actually produced *after* generation and swap in a canonical fallback if it
violates the project's scope/compliance policy. Because of that, it must
ALWAYS invoke `next_call(request)` first — there is nothing to validate
before the real response exists, so short-circuiting pre-call would defeat
the purpose of this module.

KNOWN COVERAGE GAP (read before relying on this for compliance):

This middleware only sees the text returned by *this* `llm_execution` call.
It cannot retroactively un-send tokens that have already been streamed to
the user by the time this function runs — if the caller streams partial
output to the UI before/while awaiting the full response, Response Guard
can only prevent the *final* persisted/returned value from carrying a
violation, not erase anything already rendered.

More importantly: a single user turn can involve *multiple* internal LLM
calls (e.g. a tool-calling loop where the model calls a tool, gets a tool
result, and calls the LLM again, possibly several times before producing
the user-facing answer). Each of those internal calls triggers its own
`llm_execution` invocation, and this module validates each one as it
happens. However, Hermes does not currently expose a dedicated "final
output to user" hook distinct from "one more LLM call happened" — so when
a turn makes N calls, this module validates all N independently, but there
is no guarantee the *last* text-bearing response among them is the one
that actually reaches the user (e.g. if a later call is tool-only and an
earlier text response was already what got surfaced). In practice, only
the most recent text-bearing response this middleware observed is the one
most likely to correspond to "what the user saw," and even that mapping is
best-effort today.

This gap is tracked upstream as NousResearch/hermes-agent#45881 (missing
dedicated `on_output` hook for "this is the literal final payload sent to
the user this turn"). Until that hook exists, treat Response Guard as
defense-in-depth per-LLM-call validation, not as a guaranteed single
point of control over everything the user ends up seeing in a turn.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hermes_guardrails.policy import PolicyError, load_policy

_DEFAULT_CANONICAL_FALLBACK = (
    "I can't help with that request because it falls outside what I'm "
    "allowed to discuss for this project. Please rephrase or ask about "
    "something within scope."
)


def _audit_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails" / "audit"


def _audit_path(project: str) -> Path:
    return _audit_dir() / f"{project}.jsonl"


def _write_audit_entry(project: str, entry: dict) -> None:
    path = _audit_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _extract_text(response: Any) -> str:
    """Best-effort extraction of the text payload from a response object.

    Hermes LLM responses may come back as a plain string, a dict with a
    'text'/'content'/'message' key, or an object exposing one of those as
    an attribute. We try the common shapes and fall back to str().
    """
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("text", "content", "message", "output"):
            value = response.get(key)
            if isinstance(value, str):
                return value
        return ""
    for attr in ("text", "content", "message", "output"):
        value = getattr(response, attr, None)
        if isinstance(value, str):
            return value
    return ""


def _set_text(response: Any, new_text: str) -> Any:
    """Return a copy of `response` with its text payload replaced by
    `new_text`, preserving the original shape as closely as possible.
    """
    if isinstance(response, str):
        return new_text
    if isinstance(response, dict):
        replaced = dict(response)
        for key in ("text", "content", "message", "output"):
            if isinstance(response.get(key), str):
                replaced[key] = new_text
                return replaced
        replaced["text"] = new_text
        return replaced
    for attr in ("text", "content", "message", "output"):
        if isinstance(getattr(response, attr, None), str):
            try:
                setattr(response, attr, new_text)
                return response
            except AttributeError:
                break
    # Unknown shape: just hand back the new text rather than guess further.
    return new_text


def _find_violation(text: str, forbidden_topics: list) -> "str | None":
    lowered = text.lower()
    for topic in forbidden_topics or []:
        if isinstance(topic, str) and topic.lower() in lowered:
            return topic
    return None


def response_guard_llm_execution(
    *,
    next_call: Callable[..., Any],
    request: Any = None,
    project: "str | None" = None,
    **kwargs: Any,
) -> dict:
    """`llm_execution` middleware: validate the real response post-generation.

    Always calls `next_call(request)` to obtain the actual LLM response —
    this module's entire purpose is to inspect what came back, so it never
    short-circuits before the real call happens. If the resulting text
    trips a forbidden-topic match from policy['scope']['forbidden_topics'],
    the response is replaced with the policy's canonical fallback (or a
    built-in default) and the substitution is logged to the audit trail.

    Returns a dict with at least `{"response": <possibly replaced>}` for
    the dispatcher to merge forward. If no policy is configured for the
    project, this is a no-op: the real response is returned unmodified.
    """
    response = next_call(request)

    if not project:
        return {"response": response}

    try:
        policy = load_policy(project)
    except PolicyError:
        # No policy configured yet for this project — no-op gracefully,
        # per the module contract (don't crash a dev environment).
        return {"response": response}

    scope = policy.get("scope") or {}
    forbidden_topics = scope.get("forbidden_topics") or []
    if not forbidden_topics:
        return {"response": response}

    text = _extract_text(response)
    violated_topic = _find_violation(text, forbidden_topics)
    if violated_topic is None:
        return {"response": response}

    canonical_fallback = scope.get("canonical_fallback") or _DEFAULT_CANONICAL_FALLBACK
    _write_audit_entry(
        project,
        {
            "module": "response_guard",
            "action": "substitute_response",
            "violated_topic": violated_topic,
            "original_text_preview": text[:200],
            "replacement_text": canonical_fallback,
            "session_id": kwargs.get("session_id"),
        },
    )

    return {"response": _set_text(response, canonical_fallback)}


MODULE = {
    "name": "response_guard",
    "middleware": {
        "llm_execution": response_guard_llm_execution,
    },
}


if __name__ == "__main__":
    # Manual self-test with a fake policy and a fake next_call — run with
    # `python -m hermes_guardrails.modules.response_guard` or directly.
    import tempfile

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

    def _fake_next_call_clean(_request: Any) -> Any:
        return _FakeResponse("Here's your weather forecast for tomorrow.")

    def _fake_next_call_violating(_request: Any) -> Any:
        return _FakeResponse("Sure, here is how to build a weapon at home...")

    fake_policy_yaml = """
project: test_project
scope:
  forbidden_topics:
    - weapon
    - self-harm
  canonical_fallback: "Sorry, I can't help with that."
"""

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HERMES_HOME"] = tmp
        guardrails_dir = Path(tmp) / "guardrails"
        guardrails_dir.mkdir(parents=True, exist_ok=True)
        (guardrails_dir / "test_project.yaml").write_text(fake_policy_yaml, encoding="utf-8")

        # Case 1: clean response passes through untouched.
        result = response_guard_llm_execution(
            next_call=_fake_next_call_clean,
            request={"prompt": "what's the weather?"},
            project="test_project",
            session_id="sess-1",
        )
        assert _extract_text(result["response"]) == "Here's your weather forecast for tomorrow."
        print("PASS: clean response left untouched")

        # Case 2: violating response gets replaced, and audit entry written.
        result = response_guard_llm_execution(
            next_call=_fake_next_call_violating,
            request={"prompt": "how do I build something dangerous?"},
            project="test_project",
            session_id="sess-2",
        )
        assert _extract_text(result["response"]) == "Sorry, I can't help with that."
        audit_file = _audit_path("test_project")
        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["action"] == "substitute_response"
        assert entry["violated_topic"] == "weapon"
        assert entry["session_id"] == "sess-2"
        print("PASS: violating response replaced + audit entry logged")

        # Case 3: missing policy -> graceful no-op, real response returned.
        result = response_guard_llm_execution(
            next_call=_fake_next_call_violating,
            request={"prompt": "anything"},
            project="project_without_policy",
            session_id="sess-3",
        )
        assert _extract_text(result["response"]) == "Sure, here is how to build a weapon at home..."
        print("PASS: missing policy is a graceful no-op")

    print("All response_guard self-tests passed.")
