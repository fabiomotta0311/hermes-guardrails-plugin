# Module contract (for parallel implementation)

Each module issue (#3–#16) and the observability bridge (#17) is implemented
as exactly **one new file** under `hermes_guardrails/modules/<name>.py`. This
is the only rule that matters for parallel work: as long as every subagent
creates its own new file and does not edit `hermes_guardrails/__init__.py`,
`hermes_guardrails/modules/__init__.py`, or another module's file, there is
zero merge conflict between issues worked on simultaneously.

## Required shape

```python
"""One-line description of what this module does."""

MODULE = {
    "name": "scope_intent_guard",   # unique, snake_case, matches the filename stem
    "hooks": {
        # optional — only declare the ones you implement
        "pre_llm_call": my_pre_llm_call_fn,
        "post_llm_call": my_post_llm_call_fn,
        "pre_tool_call": my_pre_tool_call_fn,
        "post_tool_call": my_post_tool_call_fn,
    },
    "middleware": {
        # optional — only declare the ones you implement
        "llm_request": my_llm_request_fn,
        "llm_execution": my_llm_execution_fn,
        "tool_request": my_tool_request_fn,
        "tool_execution": my_tool_execution_fn,
    },
}
```

- Hook functions receive `**kwargs` from Hermes (session_id, user_message,
  model, etc. depending on the hook) and their return value is ignored by
  Hermes itself — use them for logging/audit only.
- Middleware functions receive `**kwargs` (which may include `request`,
  `args`, `payload`, `next_call`, `original_request`/`original_args`
  depending on the point) and may return a dict. The dispatcher in
  `hermes_guardrails/__init__.py` merges that dict into the kwargs passed to
  the next module. Two special keys short-circuit the chain:
  - `"short_circuit_response"`: if set, Hermes never calls the real
    LLM/tool — this value is returned directly. Use this for deterministic
    blocking (e.g. Scope Guard returning the canonical fallback response).
  - For `llm_request`/`tool_request`, return `{"request": ...}` or
    `{"args": ...}` to rewrite the payload without blocking it.
- Use `hermes_guardrails.policy.load_policy(project)` to read the
  per-project YAML policy. Do not invent a second policy loader.
- Write an audit log entry (JSON line) to
  `~/.hermes/guardrails/audit/<project>.jsonl` for every block/rewrite
  decision your module makes. Use a shared helper if useful, but put that
  helper in your own file or propose it separately — don't block on another
  module's file.
- No shared mutable state between modules. If you need something every
  module could use (e.g. a cost counter), implement it self-contained in
  your own module file; duplication across modules is fine for v1 and can be
  refactored later (this project does not require clean-as-you-go DRY
  between issues worked in parallel).

## What NOT to do

- Do not edit `hermes_guardrails/__init__.py` or
  `hermes_guardrails/modules/__init__.py` — they already dispatch to every
  module automatically via auto-discovery.
- Do not wait for another issue's module to exist before writing yours —
  write defensively (your module should not crash if `load_policy` raises
  `PolicyError` because no policy file exists yet in a dev environment;
  catch it and no-op).
- Do not add end-to-end integration tests that require every other module to
  be present — per-module unit tests (calling your functions directly with
  fake kwargs) are enough for this phase. Full integration testing happens
  once, after every issue is merged, not per issue.
