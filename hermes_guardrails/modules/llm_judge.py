"""LLM-as-Judge: configurable quality/compliance scoring of LLM responses.

Runs AFTER the real LLM call (llm_execution middleware, post-`next_call`).
If `policy['judge']['enabled']` is true, sends the produced response text
plus a rubric prompt to a *separate* judge call (independent model/provider
from the main conversation model) and records a score + reason for every
decision. Two modes, read from `policy['judge']['mode']`:

  - "audit"       -> never blocks; just logs score+reason to the audit jsonl.
  - "enforcement" -> if score < policy['judge']['threshold'], sets
                      `short_circuit_response` to a fallback response and
                      logs the block decision.

Closes GitHub issue #11.

---
## Calibration

The judge is only as good as its rubric + threshold, and both WILL need
tuning once real traffic flows through it. Use the audit log
(`~/.hermes/guardrails/audit/<project>.jsonl`, entries with
`"module": "llm_judge"`) as the feedback loop:

1. **Read the `reason` field on every logged decision**, not just the score.
   The reason is free text explaining *why* the judge scored the way it did.
   Group reasons by theme (e.g. "flagged as too short", "missing citation",
   "off-topic") to find which rubric clause is actually driving decisions.

2. **False positives** (judge blocks/flags a response a human reviewer
   would accept): the reason usually points at a rubric clause that is
   *too strict* for a legitimate class of answers (e.g. short-but-correct
   answers being penalized for length). Fix by either:
   - loosening/removing that clause's wording in the rubric text, or
   - lowering `policy['judge']['threshold']` slightly, or
   - routing that case class through a rubric variant (per-channel policy
     override) instead of the global one.

3. **False negatives** (judge passes a response a human reviewer would
   reject): the reason will typically show the rubric never asked about the
   failure mode at all (e.g. no clause about citing sources). Fix by adding
   an explicit clause to the rubric describing the missing requirement, or
   raising the threshold if scores cluster just above the current cutoff.

4. **Track the false-positive/false-negative rate over time** by sampling
   the audit log periodically (e.g. weekly), manually re-labeling a sample
   of entries as "agree" / "false positive" / "false negative", and
   tracking what fraction falls into each bucket release over release. A
   rising false-positive rate after a rubric change means you tightened too
   far; a rising false-negative rate means you loosened too far. Treat the
   rubric text and threshold as a single tunable unit — changing one without
   checking the other's effect on the rate is how calibration drifts.

5. Keep rubric edits small and dated in commit messages (e.g. "tighten
   judge rubric: penalize missing citations after FN spike in week 34") so
   the audit log history can be correlated with rubric revisions when
   investigating a regression.
---
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

try:
    from hermes_guardrails.policy import load_policy, resolve_for_channel, PolicyError
except ImportError:
    # If this file is executed directly (`python .../llm_judge.py`) rather
    # than imported as part of the package, the repo root is not on
    # sys.path yet. Add it and retry once before falling back to the
    # defensive no-op stubs described in docs/module-contract.md.
    _repo_root = Path(__file__).resolve().parents[2]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    try:
        from hermes_guardrails.policy import load_policy, resolve_for_channel, PolicyError
    except ImportError:  # pragma: no cover - defensive, see module-contract.md
        load_policy = None
        resolve_for_channel = None

        class PolicyError(ValueError):
            pass


DEFAULT_FALLBACK_RESPONSE = (
    "I'm not confident this response meets quality requirements, so I'm "
    "withholding it. Please rephrase your request or try again."
)


def _guardrails_audit_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails" / "audit"


def _write_audit_entry(project: str, entry: dict) -> None:
    """Append a JSON line to ~/.hermes/guardrails/audit/<project>.jsonl.

    Best-effort: audit logging must never crash the real request path, so
    any filesystem error here is swallowed after a best attempt.
    """
    try:
        audit_dir = _guardrails_audit_dir()
        audit_dir.mkdir(parents=True, exist_ok=True)
        path = audit_dir / f"{project}.jsonl"
        entry = dict(entry)
        entry.setdefault("ts", time.time())
        entry.setdefault("module", "llm_judge")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _default_rubric() -> str:
    return (
        "Evaluate the response for: (1) relevance to the request, "
        "(2) internal coherence/no contradictions, "
        "(3) no obviously fabricated specifics, "
        "(4) non-trivial length (not a one-word non-answer). "
        "Score from 0.0 (fails badly) to 1.0 (fully compliant)."
    )


def stub_judge_fn(response_text: str, rubric: str) -> "tuple[float, str]":
    """PLACEHOLDER judge implementation — NOT a real LLM call.

    This exists purely so `llm_judge.py` is self-testable without any live
    Hermes session or LLM API available to a standalone module. It applies
    two trivial heuristics against the rubric text itself:

      - length: a response under 10 characters is treated as a likely
        non-answer and penalized heavily.
      - keyword presence: if the rubric mentions specific expected keywords
        (comma-separated after "keywords:" in the rubric string), a response
        missing all of them loses points.

    Once this module is wired into a live Hermes session, REPLACE this
    function with a real call to the judge model/provider configured at
    `policy['judge']['model']` / `policy['judge']['provider']` — e.g. a
    chat completion asking that model to score `response_text` against
    `rubric` and return a float + short justification. Pass that real
    function in as the `judge_fn` parameter of `judge_response()` /
    `llm_execution()` instead of relying on this stub.
    """
    text = (response_text or "").strip()
    score = 1.0
    reasons = []

    if len(text) < 10:
        score -= 0.6
        reasons.append("response is suspiciously short (<10 chars) — likely a non-answer")

    marker = "keywords:"
    if marker in rubric.lower():
        idx = rubric.lower().index(marker)
        kw_part = rubric[idx + len(marker):]
        keywords = [kw.strip().lower() for kw in kw_part.split(",") if kw.strip()]
        if keywords:
            lowered = text.lower()
            hits = [kw for kw in keywords if kw in lowered]
            if not hits:
                score -= 0.3
                reasons.append(f"none of the expected keywords {keywords} appear in the response")

    score = max(0.0, min(1.0, score))
    if not reasons:
        reasons.append("no rubric heuristic was triggered; stub judge has no objection")

    return score, "; ".join(reasons)


def judge_response(
    response_text: str,
    policy: dict,
    judge_fn: Optional[Callable[[str, str], "tuple[float, str]"]] = None,
) -> dict:
    """Run the judge against `response_text` per the policy's judge config.

    Returns a dict with keys: enabled, mode, score, reason, threshold,
    blocked. `blocked` is only meaningful/True when mode == "enforcement"
    and score < threshold.
    """
    judge_cfg = (policy or {}).get("judge") or {}
    enabled = bool(judge_cfg.get("enabled", False))

    result = {
        "enabled": enabled,
        "mode": judge_cfg.get("mode", "audit"),
        "score": None,
        "reason": None,
        "threshold": judge_cfg.get("threshold", 0.5),
        "judge_model": judge_cfg.get("model"),
        "judge_provider": judge_cfg.get("provider"),
        "blocked": False,
    }

    if not enabled:
        return result

    rubric = judge_cfg.get("rubric") or _default_rubric()
    fn = judge_fn or stub_judge_fn

    score, reason = fn(response_text, rubric)
    result["score"] = score
    result["reason"] = reason

    if result["mode"] == "enforcement" and score < result["threshold"]:
        result["blocked"] = True

    return result


def llm_execution(**kwargs):
    """Middleware entry point: llm_execution hook.

    Expected kwargs (per docs/module-contract.md):
      - next_call: Callable[[], Any] that performs the real LLM call and
        returns its response (string or an object with a `.text`/`.content`
        attribute — we handle both leniently).
      - project: str, which policy file to load.
      - channel: optional str, per-channel policy override.
      - judge_fn: optional Callable override for tests/real wiring,
        signature (response_text, rubric) -> (score, reason).

    Returns a dict. If the judge blocks the response, that dict contains
    "short_circuit_response" per the module contract so Hermes substitutes
    the fallback instead of returning the real (sub-threshold) response.
    """
    next_call = kwargs.get("next_call")
    project = kwargs.get("project", "default")
    channel = kwargs.get("channel")
    judge_fn = kwargs.get("judge_fn")

    # Always perform the real call first — the judge evaluates the actual
    # response, it does not replace calling the main model.
    response = next_call() if callable(next_call) else None
    response_text = _extract_text(response)

    policy = {}
    if load_policy is not None:
        try:
            policy = load_policy(project)
            if resolve_for_channel is not None:
                policy = resolve_for_channel(policy, channel)
        except PolicyError:
            # No policy file yet in this dev environment — no-op per
            # docs/module-contract.md ("write defensively").
            policy = {}

    verdict = judge_response(response_text, policy, judge_fn=judge_fn)

    if not verdict["enabled"]:
        return {"response": response}

    audit_entry = {
        "project": project,
        "channel": channel,
        "mode": verdict["mode"],
        "score": verdict["score"],
        "reason": verdict["reason"],
        "threshold": verdict["threshold"],
        "judge_model": verdict["judge_model"],
        "judge_provider": verdict["judge_provider"],
        "blocked": verdict["blocked"],
        "response_preview": (response_text or "")[:200],
    }
    _write_audit_entry(project, audit_entry)

    if verdict["blocked"]:
        fallback = (policy.get("scope") or {}).get("canonical_fallback") or DEFAULT_FALLBACK_RESPONSE
        return {
            "response": response,
            "short_circuit_response": fallback,
            "judge_score": verdict["score"],
            "judge_reason": verdict["reason"],
        }

    return {
        "response": response,
        "judge_score": verdict["score"],
        "judge_reason": verdict["reason"],
    }


def _extract_text(response) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    for attr in ("text", "content", "message"):
        value = getattr(response, attr, None)
        if isinstance(value, str):
            return value
    return str(response)


MODULE = {
    "name": "llm_judge",
    "hooks": {},
    "middleware": {
        "llm_execution": llm_execution,
    },
}


if __name__ == "__main__":
    # ---- Manual self-test (no live Hermes/LLM infra required) ----

    print("== stub_judge_fn basic heuristics ==")
    rubric_with_keywords = _default_rubric() + " keywords: refund, order"
    score, reason = stub_judge_fn("ok", rubric_with_keywords)
    print(f"short answer -> score={score:.2f} reason={reason!r}")
    assert score < 0.5, "short non-answer should score low"

    score, reason = stub_judge_fn(
        "Your refund for order #1234 has been processed and will arrive in 3-5 days.",
        rubric_with_keywords,
    )
    print(f"good answer -> score={score:.2f} reason={reason!r}")
    assert score == 1.0, "compliant answer with keywords should score full marks"

    score, reason = stub_judge_fn(
        "This is a reasonably long sentence but mentions nothing relevant at all here.",
        rubric_with_keywords,
    )
    print(f"missing-keywords answer -> score={score:.2f} reason={reason!r}")
    assert 0.0 < score < 1.0, "missing keywords should be penalized but not zeroed"

    print("\n== judge_response: disabled policy is a no-op ==")
    v = judge_response("anything", {"judge": {"enabled": False}})
    print(v)
    assert v["enabled"] is False and v["blocked"] is False

    print("\n== judge_response: audit mode never blocks ==")
    v = judge_response(
        "ok",
        {"judge": {"enabled": True, "mode": "audit", "threshold": 0.9}},
    )
    print(v)
    assert v["blocked"] is False, "audit mode must never set blocked=True"
    assert v["score"] is not None and v["reason"]

    print("\n== judge_response: enforcement mode blocks under threshold ==")
    v = judge_response(
        "ok",
        {"judge": {"enabled": True, "mode": "enforcement", "threshold": 0.9}},
    )
    print(v)
    assert v["blocked"] is True

    print("\n== judge_response: enforcement mode passes above threshold ==")
    v = judge_response(
        "A sufficiently long and reasonable-looking answer here.",
        {"judge": {"enabled": True, "mode": "enforcement", "threshold": 0.3}},
    )
    print(v)
    assert v["blocked"] is False

    print("\n== llm_execution: full middleware flow, enforcement blocks ==")

    def fake_next_call():
        return "ok"

    def fake_fail_judge(response_text, rubric):
        return 0.1, "test-forced low score"

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HERMES_HOME"] = tmp
        result = llm_execution(
            next_call=fake_next_call,
            project="selftest",
            judge_fn=fake_fail_judge,
            # Force enabled/enforcement without needing a real policy file
            # by also monkeypatching load_policy for this run:
        )
        # Since no policy file exists for "selftest", load_policy raises
        # PolicyError and the module no-ops (per contract) — verify that
        # defensive path explicitly:
        print("no-policy-file result:", result)
        assert "short_circuit_response" not in result

        # Now test the enabled/enforcement path by writing a real policy file.
        guardrails_dir = Path(tmp) / "guardrails"
        guardrails_dir.mkdir(parents=True, exist_ok=True)
        (guardrails_dir / "selftest.yaml").write_text(
            "project: selftest\n"
            "judge:\n"
            "  enabled: true\n"
            "  mode: enforcement\n"
            "  threshold: 0.5\n"
            "  model: gpt-judge\n"
            "  provider: openai\n",
            encoding="utf-8",
        )
        result = llm_execution(
            next_call=fake_next_call,
            project="selftest",
            judge_fn=fake_fail_judge,
        )
        print("enforcement-block result:", result)
        assert result.get("short_circuit_response") == DEFAULT_FALLBACK_RESPONSE
        assert result["judge_score"] == 0.1

        audit_path = guardrails_dir / "audit" / "selftest.jsonl"
        assert audit_path.exists(), "audit log entry was not written"
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        print(f"audit log has {len(lines)} line(s):")
        for line in lines:
            print(" ", line)
        assert len(lines) == 1
        logged = json.loads(lines[0])
        assert logged["blocked"] is True
        assert logged["reason"] == "test-forced low score"

    print("\nAll self-tests passed.")
