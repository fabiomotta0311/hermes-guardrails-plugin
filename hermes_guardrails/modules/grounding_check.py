"""Grounding / Faithfulness Check for RAG flows.

After the LLM response comes back, this module checks whether the claims in
the response are actually supported by the retrieved context for that turn.
It uses a lightweight, no-dependency word-overlap / Jaccard-similarity
heuristic (no embeddings, no extra libraries) to compare each sentence of
the response against the retrieved context chunks.

IMPORTANT / NOT YET VERIFIED:
The exact kwarg name Hermes uses to pass retrieved RAG context into the
``llm_execution`` middleware has not been verified against a live Hermes
session as of writing this module. We defensively probe several plausible
key names (see ``_CONTEXT_KWARG_CANDIDATES`` below) and no-op gracefully
(returning an empty dict, i.e. doing nothing) if none of them are present
in kwargs. If/when the real kwarg name is confirmed, add it to the front of
that tuple.

Behavior:
- Per acceptance criteria, this module is **observe-only by default**: it
  never blocks the response unless the project policy explicitly opts in
  via ``policy["grounding"]["block_unsupported"] = true``.
- When observing, every turn with retrieved context is logged to the audit
  jsonl with the fraction of response sentences judged "unsupported" and
  the sentences themselves, so a human can review grounding quality without
  anything being blocked.
- When blocking is enabled and ANY sentence is judged unsupported above the
  configured threshold, the middleware short-circuits the response with a
  flagged message instead of releasing the original response.

Policy schema (all keys optional; module no-ops if policy/section absent):

    grounding:
      enabled: bool                 # default True
      block_unsupported: bool       # default False (observe-only)
      overlap_threshold: float      # Jaccard similarity below this => unsupported (default 0.15)
      min_sentence_words: int       # sentences shorter than this are ignored (default 4)
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from hermes_guardrails.policy import PolicyError, load_policy, resolve_for_channel

MODULE_NAME = "grounding_check"

# Plausible kwarg names under which Hermes might pass retrieved RAG context
# into llm_execution. Order = priority if more than one happens to be present.
_CONTEXT_KWARG_CANDIDATES = (
    "retrieved_context",
    "context_chunks",
    "rag_context",
    "retrieved_documents",
    "retrieved_chunks",
    "documents",
    "sources",
)

_DEFAULT_OVERLAP_THRESHOLD = 0.15
_DEFAULT_MIN_SENTENCE_WORDS = 4

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "for",
    "is", "are", "was", "were", "be", "been", "being", "it", "its", "this",
    "that", "these", "those", "as", "at", "by", "with", "from", "about",
    "into", "than", "then", "so", "such", "not", "no", "do", "does", "did",
    "has", "have", "had", "will", "would", "can", "could", "may", "might",
    "i", "you", "he", "she", "we", "they", "them", "his", "her", "their",
    "our", "your", "me", "us",
}


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _split_sentences(text: str):
    if not text:
        return []
    # Simple, dependency-free sentence splitter.
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return intersection / union


def _extract_context_chunks(kwargs: dict):
    """Return (kwarg_name_used, list_of_context_strings) or (None, [])."""
    for key in _CONTEXT_KWARG_CANDIDATES:
        if key in kwargs and kwargs[key]:
            value = kwargs[key]
            chunks = []
            if isinstance(value, str):
                chunks = [value]
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        chunks.append(item)
                    elif isinstance(item, dict):
                        # common shapes: {"text": ...} / {"content": ...} / {"chunk": ...}
                        text = item.get("text") or item.get("content") or item.get("chunk")
                        if text:
                            chunks.append(str(text))
            elif isinstance(value, dict):
                text = value.get("text") or value.get("content") or value.get("chunk")
                if text:
                    chunks.append(str(text))
            if chunks:
                return key, chunks
    return None, []


def _extract_response_text(result) -> str:
    """Best-effort extraction of response text from whatever next_call returned."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("content", "text", "response", "message"):
            value = result.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                nested = value.get("content") or value.get("text")
                if isinstance(nested, str):
                    return nested
    # Fall back to attribute access for response-like objects.
    for attr in ("content", "text"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
    return ""


def _audit_log_path(project: str) -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails" / "audit" / f"{project}.jsonl"


def _write_audit(project, entry: dict) -> None:
    project = project or "unknown"
    try:
        path = _audit_log_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = dict(entry)
        entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        entry.setdefault("module", MODULE_NAME)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Audit logging must never crash the pipeline.
        pass


def _load_grounding_policy(kwargs: dict):
    project = kwargs.get("project") or kwargs.get("project_name")
    channel = kwargs.get("channel")
    if not project:
        return project, {}
    try:
        policy = load_policy(project)
    except PolicyError:
        return project, {}
    policy = resolve_for_channel(policy, channel)
    return project, (policy.get("grounding") or {})


def llm_execution(**kwargs):
    """Post-response grounding/faithfulness check middleware.

    Expected to run AFTER next_call() has produced a result (per the
    llm_execution middleware point described in module-contract.md). Looks
    for the already-produced result under common keys and for retrieved
    context under several plausible kwarg names.
    """
    project, grounding_policy = _load_grounding_policy(kwargs)

    if grounding_policy.get("enabled", True) is False:
        return {}

    context_key, context_chunks = _extract_context_chunks(kwargs)
    if not context_chunks:
        # No retrieved context available this turn (not a RAG turn, or the
        # real kwarg name differs from our candidates) -> no-op gracefully.
        return {}

    # The result produced by next_call() may be under one of several keys
    # depending on how the dispatcher wires llm_execution.
    result = kwargs.get("result")
    if result is None:
        result = kwargs.get("response")
    response_text = _extract_response_text(result)
    if not response_text:
        return {}

    overlap_threshold = float(grounding_policy.get("overlap_threshold", _DEFAULT_OVERLAP_THRESHOLD))
    min_sentence_words = int(grounding_policy.get("min_sentence_words", _DEFAULT_MIN_SENTENCE_WORDS))
    block_unsupported = bool(grounding_policy.get("block_unsupported", False))

    context_tokens_per_chunk = [_tokenize(chunk) for chunk in context_chunks]
    all_context_tokens = set().union(*context_tokens_per_chunk) if context_tokens_per_chunk else set()

    sentences = _split_sentences(response_text)
    unsupported = []
    evaluated = []
    for sentence in sentences:
        tokens = _tokenize(sentence)
        if len(tokens) < min_sentence_words:
            continue  # too short to meaningfully judge, skip
        best_overlap = max((_jaccard(tokens, ctx_tokens) for ctx_tokens in context_tokens_per_chunk), default=0.0)
        # Also consider overlap against the union of all context, in case the
        # supporting words are spread across multiple chunks.
        union_overlap = _jaccard(tokens, all_context_tokens)
        score = max(best_overlap, union_overlap)
        evaluated.append({"sentence": sentence, "overlap": round(score, 4)})
        if score < overlap_threshold:
            unsupported.append({"sentence": sentence, "overlap": round(score, 4)})

    unsupported_ratio = (len(unsupported) / len(evaluated)) if evaluated else 0.0

    audit_entry = {
        "project": project,
        "context_kwarg": context_key,
        "num_context_chunks": len(context_chunks),
        "num_sentences_evaluated": len(evaluated),
        "num_unsupported": len(unsupported),
        "unsupported_ratio": round(unsupported_ratio, 4),
        "unsupported_sentences": unsupported,
        "overlap_threshold": overlap_threshold,
        "block_unsupported": block_unsupported,
        "action": "observe",
    }

    if not unsupported:
        _write_audit(project, audit_entry)
        return {}

    if block_unsupported:
        audit_entry["action"] = "block"
        _write_audit(project, audit_entry)
        flagged_message = (
            "This response could not be fully verified against the retrieved "
            "context and has been withheld. Unsupported claim(s) detected: "
            + "; ".join(item["sentence"] for item in unsupported)
        )
        return {"short_circuit_response": flagged_message}

    # Observe-only (default): annotate via audit log, do not alter the response.
    _write_audit(project, audit_entry)
    return {}


MODULE = {
    "name": "grounding_check",
    "middleware": {
        "llm_execution": llm_execution,
    },
}


if __name__ == "__main__":
    fake_context = [
        "The Eiffel Tower is located in Paris, France, and was completed in 1889.",
        "It was designed by the engineer Gustave Eiffel for the 1889 World's Fair.",
    ]
    fake_response_grounded = (
        "The Eiffel Tower is in Paris, France. It was completed in 1889 for the World's Fair."
    )
    fake_response_ungrounded = (
        "The Eiffel Tower is in Paris, France. It was completed in 1889 for the World's Fair. "
        "The tower is painted bright pink and is made entirely of recycled plastic bottles."
    )

    print("--- Grounded response (observe-only) ---")
    out = llm_execution(
        retrieved_context=fake_context,
        result=fake_response_grounded,
        project=None,  # no policy file in this demo -> audit log is skipped
    )
    print("middleware return:", out)

    print("\n--- Ungrounded response (observe-only, no policy) ---")
    out = llm_execution(
        retrieved_context=fake_context,
        result=fake_response_ungrounded,
        project=None,
    )
    print("middleware return:", out)

    print("\n--- Manual scoring breakdown (no policy needed) ---")
    chunks_tokens = [_tokenize(c) for c in fake_context]
    for sentence in _split_sentences(fake_response_ungrounded):
        tokens = _tokenize(sentence)
        score = max((_jaccard(tokens, ct) for ct in chunks_tokens), default=0.0)
        flag = "UNSUPPORTED" if score < _DEFAULT_OVERLAP_THRESHOLD else "supported"
        print(f"[{flag:11s}] overlap={score:.3f}  {sentence}")

    print("\n--- No context present -> no-op ---")
    out = llm_execution(result=fake_response_grounded, project=None)
    print("middleware return:", out)
