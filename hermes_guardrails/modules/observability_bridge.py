"""Observability Bridge — emits guardrail decisions as OTel span events,
falling back to a local .jsonl audit log when OpenTelemetry isn't present
or there's no recording span (e.g. traceverse_hermes_otel not enabled).

This module is a standalone, self-testable utility (see issue #17). It does
NOT wire itself into any other module's hooks/middleware — per
docs/module-contract.md, parallel sibling modules own their own files and
their own audit-log calls. Importing `emit_guardrail_event` from this file
into another module's decision path (e.g. scope_intent_guard, cost_guard,
judge_guard) and calling it alongside/instead of a raw jsonl write is a
natural follow-up PR once all modules exist.

Usage from any other module (future wiring, not done here):

    from hermes_guardrails.modules.observability_bridge import emit_guardrail_event

    emit_guardrail_event(
        "guardrail.scope_intent_guard.block",
        {"gen_ai.request.model": model, "guardrail.reason": "forbidden_topic"},
    )
"""

import json
import os
import time
from pathlib import Path

try:
    from opentelemetry import trace  # type: ignore
except ImportError:  # pragma: no cover - OTel is an optional dependency
    trace = None

MODULE = {
    "name": "observability_bridge",
    # No hooks/middleware declared: this module exposes a utility function
    # for other modules to call, not a decision point of its own. Returning
    # an empty "hooks"/"middleware" dict keeps it a well-formed MODULE entry
    # under auto-discovery without making it act as a guardrail itself.
    "hooks": {},
    "middleware": {},
}


def _fallback_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails" / "audit"


def _fallback_path(project: str) -> Path:
    return _fallback_dir() / f"{project}-otel-fallback.jsonl"


def _write_fallback(project: str, event_name: str, attributes: dict) -> None:
    """Append one JSON line to the local fallback audit log.

    Best-effort: any I/O failure here (permissions, disk full, read-only
    filesystem) is swallowed. This helper must never be the reason a
    guardrail decision crashes the calling module — observability is
    secondary to the guardrail's actual block/allow decision.
    """
    try:
        path = _fallback_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "event": event_name,
            "attributes": attributes,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def emit_guardrail_event(event_name: str, attributes: dict, project: str = "default") -> str:
    """Emit a guardrail decision as an OTel span event, or fall back to jsonl.

    Tries, in order:
      1. `from opentelemetry import trace` — if the package isn't installed
         (ImportError, handled at module import time above, `trace is
         None`), skip straight to the fallback.
      2. `trace.get_current_span()` — this call is SAFE even with no OTel
         SDK/provider configured: the public OTel API guarantees it always
         returns a valid span object (a no-op `INVALID_SPAN` when there is
         no active context), never raises, and never returns None. This is
         an explicit design property of the OTel Python API so that
         instrumented code can run unmodified whether or not a provider is
         configured.
      3. `span.is_recording()` — True only when a real SDK span is active
         (e.g. traceverse_hermes_otel has started a trace for this
         request). The no-op span from step 2 always returns False here,
         which is how we detect "OTel present but nothing recording" and
         route to the fallback instead of silently dropping the event.
      4. `span.add_event(event_name, attributes=attributes)` — recorded on
         the live span. Attribute values must be OTel-primitive (str, bool,
         int, float, or homogeneous sequences of those); callers should
         stringify anything else before calling this function.

    Returns "otel" if the event was recorded on a live span, "fallback" if
    it was written to the local jsonl log instead. Never raises: any
    unexpected error from the OTel API itself is caught and treated as a
    reason to fall back, so a guardrail module calling this can never be
    crashed by its observability plumbing.

    `project` selects the fallback file name
    (~/.hermes/guardrails/audit/<project>-otel-fallback.jsonl) and is only
    used when the fallback path is taken.
    """
    if trace is not None:
        try:
            span = trace.get_current_span()
            if span is not None and span.is_recording():
                span.add_event(event_name, attributes=attributes)
                return "otel"
        except Exception:
            # Any unexpected failure in the OTel API itself (malformed
            # attributes, SDK internals, etc.) — fall through to the local
            # fallback rather than lose the event or raise into the caller.
            pass

    _write_fallback(project, event_name, attributes)
    return "fallback"


if __name__ == "__main__":
    # Manual self-test. Only the "OTel absent/not recording" path is
    # actually exercised in this isolated environment (no live Hermes
    # session, no traceverse_hermes_otel, likely no `opentelemetry`
    # package installed at all). Documented behavior for the "OTel
    # present and recording" path:
    #
    #   If `opentelemetry-api`/`opentelemetry-sdk` are installed AND a
    #   TracerProvider with an active, started span is current (e.g.
    #   traceverse_hermes_otel has wrapped the current Hermes turn in a
    #   span), `emit_guardrail_event` returns "otel" and the event shows
    #   up as a span event with the given attributes in whatever exporter
    #   backend that provider is configured to send to (console, OTLP
    #   collector, Langfuse, etc.). This is NOT verified by this script —
    #   it requires a real OTel SDK + exporter + traceverse_hermes_otel
    #   wiring, none of which exist in this isolated task environment.
    import shutil
    import tempfile

    print(f"opentelemetry import available: {trace is not None}")

    test_project = "selftest-observability-bridge"
    fallback_file = _fallback_path(test_project)
    if fallback_file.exists():
        fallback_file.unlink()

    result = emit_guardrail_event(
        "guardrail.selftest.block",
        {"gen_ai.request.model": "test-model", "guardrail.reason": "selftest"},
        project=test_project,
    )
    print(f"emit_guardrail_event() -> {result!r}")

    if result == "fallback":
        assert fallback_file.exists(), "fallback file was not created"
        lines = fallback_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "guardrail.selftest.block"
        assert record["attributes"]["guardrail.reason"] == "selftest"
        print(f"fallback jsonl verified at {fallback_file}: {record}")
        fallback_file.unlink()
    else:
        print("event was recorded on a live OTel span (not the fallback path).")

    print("self-test OK")
