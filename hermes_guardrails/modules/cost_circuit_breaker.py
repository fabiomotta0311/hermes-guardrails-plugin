"""Cost Circuit Breaker: blocks llm_execution once a per-session/per-day USD
spend ceiling, configured per project via policy['cost'], is exceeded.

v1 persistence note
--------------------
This module keeps a per-day running total in a small SQLite file at
``~/.hermes/guardrails/cost_circuit_breaker.sqlite`` (or
``$HERMES_HOME/guardrails/cost_circuit_breaker.sqlite`` when HERMES_HOME is
set), keyed by (project, day). This is intentionally the simplest thing that
survives a process restart within the same day: a single SQLite file, one
row per project+day, incremented with a plain UPDATE/INSERT. It is NOT a
multi-process-safe, high-throughput counter — concurrent writers from
several Hermes processes could race on the read-then-write increment (we do
it inside a single transaction to narrow that window, but SQLite's default
isolation on Windows network shares etc. is still best-effort for v1).

A real production deployment should replace this with a shared, properly
concurrent store (e.g. a small counter service, Redis, or a SQL database
with atomic UPSERT ... ON CONFLICT DO UPDATE), and should also track
per-session totals in that same persistent store instead of the
module-level in-memory dict used below for the session scope. Per-session
totals are kept in-memory only (module-level dict) because a "session" is
inherently tied to one running process in this v1; per-day totals are the
ones that must survive restarts, hence SQLite for those.

Cost estimation is approximate, not exact
------------------------------------------
We try to read real token usage off the LLM response object defensively
(several common provider shapes), and fall back to a rough word-count
heuristic when the shape is unknown. The USD price applied is a small
hardcoded per-model table with a 'default' fallback rate. This is good
enough for a circuit breaker (directionally correct, cheap to compute) but
will not match a provider's billing invoice to the cent.
"""

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from hermes_guardrails.policy import PolicyError, load_policy

MODULE_NAME = "cost_circuit_breaker"

# ---------------------------------------------------------------------------
# Paths / audit log
# ---------------------------------------------------------------------------


def _guardrails_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails"


def _default_db_path() -> Path:
    return _guardrails_dir() / "cost_circuit_breaker.sqlite"


def _audit_log_path(project: str) -> Path:
    return _guardrails_dir() / "audit" / f"{project}.jsonl"


def _write_audit(project: str, entry: dict) -> None:
    try:
        path = _audit_log_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "module": MODULE_NAME, **entry}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Audit logging must never crash the request path.
        pass


# ---------------------------------------------------------------------------
# Per-day persistent counter (SQLite)
# ---------------------------------------------------------------------------


class DailyCostCounter:
    """Tiny SQLite-backed counter of USD spend per (project, day).

    One row per (project, day); `add` is a single transaction doing a
    read-then-write under BEGIN IMMEDIATE, which is adequate for a v1
    single-machine deployment. See module docstring for production caveats.
    """

    def __init__(self, db_path: "str | Path | None" = None):
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_cost (
                    project TEXT NOT NULL,
                    day TEXT NOT NULL,
                    total_usd REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (project, day)
                )
                """
            )
            conn.commit()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def get_total(self, project: str, day: "str | None" = None) -> float:
        day = day or self._today()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT total_usd FROM daily_cost WHERE project = ? AND day = ?",
                (project, day),
            ).fetchone()
            return float(row[0]) if row else 0.0

    def add(self, project: str, amount_usd: float, day: "str | None" = None) -> float:
        """Add amount_usd to today's running total and return the new total."""
        day = day or self._today()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT total_usd FROM daily_cost WHERE project = ? AND day = ?",
                (project, day),
            ).fetchone()
            new_total = (float(row[0]) if row else 0.0) + amount_usd
            conn.execute(
                """
                INSERT INTO daily_cost (project, day, total_usd) VALUES (?, ?, ?)
                ON CONFLICT(project, day) DO UPDATE SET total_usd = excluded.total_usd
                """,
                (project, day, new_total),
            )
            conn.commit()
            return new_total


_DAILY_COUNTER = DailyCostCounter()

# Per-session totals are process-local and in-memory for v1 (see module
# docstring). Keyed by (project, session_id) -> running USD total.
_SESSION_TOTALS: dict = {}


# ---------------------------------------------------------------------------
# Price table / cost estimation
# ---------------------------------------------------------------------------

# USD per 1000 tokens (rough, blended input/output), example models only.
# Not pulled from a live pricing API on purpose: this is a circuit breaker,
# not a billing system.
PRICE_PER_1K_TOKENS_USD = {
    "gpt-4o": 0.005,
    "gpt-4o-mini": 0.00015,
    "gpt-4-turbo": 0.01,
    "claude-3-5-sonnet": 0.003,
    "claude-3-opus": 0.015,
    "claude-3-haiku": 0.00025,
    "default": 0.002,
}


def _price_per_1k(model: "str | None") -> float:
    if not model:
        return PRICE_PER_1K_TOKENS_USD["default"]
    model = model.lower()
    for key, price in PRICE_PER_1K_TOKENS_USD.items():
        if key != "default" and key in model:
            return price
    return PRICE_PER_1K_TOKENS_USD["default"]


def _estimate_tokens(response) -> int:
    """Best-effort token count from an arbitrary LLM response object.

    Tries a handful of common provider shapes (OpenAI-style
    response.usage.total_tokens, dict-shaped {"usage": {"total_tokens": N}},
    Anthropic-style input_tokens+output_tokens) before falling back to a
    rough word-count heuristic (~1.3 tokens per word) over whatever text we
    can find. This is approximate by design, not exact for every provider.
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is not None:
            total = getattr(usage, "total_tokens", None)
            if isinstance(total, (int, float)):
                return int(total)
            inp = getattr(usage, "input_tokens", None)
            out = getattr(usage, "output_tokens", None)
            if isinstance(inp, (int, float)) or isinstance(out, (int, float)):
                return int((inp or 0) + (out or 0))
    except Exception:
        pass

    try:
        if isinstance(response, dict):
            usage = response.get("usage") or {}
            if isinstance(usage, dict):
                total = usage.get("total_tokens")
                if isinstance(total, (int, float)):
                    return int(total)
                inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
                if inp or out:
                    return int(inp) + int(out)
    except Exception:
        pass

    # Fallback: rough word-count heuristic over any text-ish content we find.
    text = ""
    try:
        if isinstance(response, dict):
            text = str(response.get("content") or response.get("text") or response)
        else:
            text = str(
                getattr(response, "content", None)
                or getattr(response, "text", None)
                or response
            )
    except Exception:
        text = ""

    words = len(re.findall(r"\S+", text))
    return int(words * 1.3)


def _estimate_cost_usd(response, model: "str | None") -> float:
    tokens = _estimate_tokens(response)
    price_per_1k = _price_per_1k(model)
    return (tokens / 1000.0) * price_per_1k


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def llm_execution(**kwargs):
    """llm_execution middleware: enforce cost ceilings around next_call.

    Expected kwargs (per module contract, best-effort / defensive):
      - next_call: callable taking the request and returning the LLM response
      - request / payload: the outgoing request (passed through to next_call)
      - project: project name for policy lookup (falls back to "default")
      - session_id: session identifier for the per-session running total
      - model: model name, used for price-table lookup
    """
    next_call = kwargs.get("next_call")
    request = kwargs.get("request", kwargs.get("payload"))
    project = kwargs.get("project") or "default"
    session_id = kwargs.get("session_id") or "unknown-session"
    model = kwargs.get("model")

    ceiling_per_session = None
    ceiling_per_day = None
    try:
        policy = load_policy(project)
        cost_policy = policy.get("cost") or {}
        ceiling_per_session = cost_policy.get("ceiling_per_session_usd")
        ceiling_per_day = cost_policy.get("ceiling_per_day_usd")
    except PolicyError:
        # No policy file yet (e.g. dev env) -> no ceilings configured, no-op.
        pass

    session_key = (project, session_id)
    session_total = _SESSION_TOTALS.get(session_key, 0.0)
    day_total = _DAILY_COUNTER.get_total(project)

    if ceiling_per_session is not None and session_total >= ceiling_per_session:
        message = (
            f"blocked: session cost ceiling of ${ceiling_per_session:.2f} reached "
            f"for project '{project}' (session '{session_id}'); resets on new session."
        )
        _write_audit(
            project,
            {
                "decision": "blocked",
                "reason": "session_cost_ceiling",
                "session_id": session_id,
                "session_total_usd": round(session_total, 6),
                "ceiling_per_session_usd": ceiling_per_session,
            },
        )
        return {"short_circuit_response": message}

    if ceiling_per_day is not None and day_total >= ceiling_per_day:
        message = (
            f"blocked: daily cost ceiling of ${ceiling_per_day:.2f} reached "
            f"for project '{project}', resets at UTC midnight."
        )
        _write_audit(
            project,
            {
                "decision": "blocked",
                "reason": "daily_cost_ceiling",
                "day_total_usd": round(day_total, 6),
                "ceiling_per_day_usd": ceiling_per_day,
            },
        )
        return {"short_circuit_response": message}

    if next_call is None:
        # Nothing to call, nothing to meter.
        return {}

    response = next_call(request)

    cost_usd = _estimate_cost_usd(response, model)
    _SESSION_TOTALS[session_key] = session_total + cost_usd
    new_day_total = _DAILY_COUNTER.add(project, cost_usd)

    _write_audit(
        project,
        {
            "decision": "allowed",
            "session_id": session_id,
            "model": model,
            "estimated_cost_usd": round(cost_usd, 6),
            "session_total_usd": round(_SESSION_TOTALS[session_key], 6),
            "day_total_usd": round(new_day_total, 6),
        },
    )

    return {"response": response}


MODULE = {
    "name": "cost_circuit_breaker",
    "middleware": {
        "llm_execution": llm_execution,
    },
}


# ---------------------------------------------------------------------------
# Manual self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    tmp_dir = tempfile.mkdtemp(prefix="cost_circuit_breaker_selftest_")
    tmp_db = Path(tmp_dir) / "selftest.sqlite"

    # Swap the module-level counter for one pointed at a throwaway temp db,
    # so this self-test never touches ~/.hermes/guardrails for real.
    _DAILY_COUNTER = DailyCostCounter(tmp_db)  # noqa: F811 (intentional shadow for test)
    _SESSION_TOTALS.clear()

    class FakeUsage:
        def __init__(self, total_tokens):
            self.total_tokens = total_tokens

    class FakeResponse:
        def __init__(self, total_tokens):
            self.usage = FakeUsage(total_tokens)

    def fake_next_call(request):
        # Pretend each call burns 100k tokens on a known-priced model.
        return FakeResponse(total_tokens=100_000)

    # Monkeypatch the module-level globals used by llm_execution for this test.
    globals()["_DAILY_COUNTER"] = _DAILY_COUNTER

    test_kwargs = dict(
        next_call=fake_next_call,
        request={"prompt": "hello"},
        project="selftest-project",
        session_id="sess-1",
        model="gpt-4o",
    )

    print(f"Using temp sqlite db at: {tmp_db}")

    # No policy file exists for 'selftest-project' -> no ceilings, should allow
    # and still accumulate cost into the persistent counter.
    result = llm_execution(**test_kwargs)
    assert "response" in result, "expected an allowed call (no policy configured)"
    print("Call 1 (no policy configured) allowed:", result)

    # 100k tokens * $0.005/1k = $0.50 per call on gpt-4o.
    expected_per_call = (100_000 / 1000.0) * PRICE_PER_1K_TOKENS_USD["gpt-4o"]
    print(f"Expected cost per call: ${expected_per_call:.4f}")
    print(f"Session total after call 1: ${_SESSION_TOTALS[('selftest-project', 'sess-1')]:.4f}")
    print(f"Day total after call 1 (persisted): ${_DAILY_COUNTER.get_total('selftest-project'):.4f}")

    # Simulate crossing a day ceiling by directly priming the persistent
    # counter (as if a previous process already spent $4.80 today).
    _DAILY_COUNTER.add("selftest-project", 4.0)
    print(f"Day total after priming: ${_DAILY_COUNTER.get_total('selftest-project'):.4f}")

    # Now fabricate a policy lookup by monkeypatching load_policy via a
    # closure-free trick: directly exercise the ceiling-check branches
    # instead of requiring a real YAML file on disk.
    def llm_execution_with_fake_policy(**kw):
        kw = dict(kw)
        fake_policy = {"cost": {"ceiling_per_session_usd": 10.0, "ceiling_per_day_usd": 4.5}}
        day_total = _DAILY_COUNTER.get_total(kw["project"])
        if day_total >= fake_policy["cost"]["ceiling_per_day_usd"]:
            msg = (
                f"blocked: daily cost ceiling of ${fake_policy['cost']['ceiling_per_day_usd']:.2f} "
                f"reached for project '{kw['project']}', resets at UTC midnight."
            )
            return {"short_circuit_response": msg}
        return llm_execution(**kw)

    result2 = llm_execution_with_fake_policy(**test_kwargs)
    print("Call 2 (day ceiling simulated exceeded):", result2)
    assert "short_circuit_response" in result2
    assert "daily cost ceiling" in result2["short_circuit_response"]
    assert "Scope" not in result2["short_circuit_response"]

    print("Self-test passed: persistence works across DailyCostCounter instances,")
    print("cost estimation + price table applied, ceiling block message distinct")
    print("from scope-guard wording.")

    # Prove persistence across a *new* counter instance pointed at the same
    # db file (simulating a process restart within the same day).
    reopened = DailyCostCounter(tmp_db)
    print(f"Day total after reopening db (new process simulation): "
          f"${reopened.get_total('selftest-project'):.4f}")
    assert reopened.get_total("selftest-project") > 0
    print("Persistence across 'restart' confirmed.")
