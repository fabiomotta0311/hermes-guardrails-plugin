# Channel Policy Overrides

Guardrails policy files support a `channels` top-level key that lets a
project apply **stricter or looser rules depending on the delivery
channel** — e.g. a public Telegram bot should be held to a tighter leash
than an internal CLI session, even though both run against the same
project policy file.

This is implemented today in `hermes_guardrails/policy.py` via
`resolve_for_channel(policy, channel)`. This document is **not** adding
new mechanism — it just shows how to use what's there.

## How merging works

`resolve_for_channel` takes the loaded policy dict and a channel name and:

1. Returns the policy unchanged if `channel` is falsy (`None`/`""`) or if
   there is no `channels.<channel>` entry — this is the fallback-to-default
   behavior.
2. Otherwise, for each top-level key present in `channels.<channel>`:
   - If both the project default and the override value for that key are
     dicts (e.g. `scope`, `cost`, `judge`), they are **shallow-merged** —
     keys you specify in the override replace the matching key under the
     project default; keys you don't mention are inherited unchanged.
   - Otherwise the override value replaces the project default outright.

Practical implication: you only need to specify the *fields that change*
for a channel, not the whole block. See Example 1 below.

## Example 1 — Strict Telegram, looser internal CLI

A single project with a relaxed baseline, a hardened public Telegram
channel, and a slightly looser internal CLI channel.

```yaml
project: my-assistant

scope:
  allowed_topics:
    - product-support
    - billing
  forbidden_topics:
    - legal-advice
  canonical_fallback: "I can help with product support and billing questions."

cost:
  ceiling_per_session_usd: 2.00
  ceiling_per_day_usd: 20.00

judge:
  enabled: true
  model: gpt-4o-mini
  provider: openai
  threshold: 0.7

channels:
  telegram:
    # Public-facing: narrow the topics further and cut spend hard.
    scope:
      forbidden_topics:
        - legal-advice
        - medical-advice
        - financial-advice
      canonical_fallback: "Sorry, I can only help with product support on this channel."
    cost:
      ceiling_per_session_usd: 0.50
      ceiling_per_day_usd: 5.00
    judge:
      threshold: 0.85   # stricter judge gate; model/provider/enabled inherited

  cli:
    # Internal/trusted use: relax the ceiling, skip the LLM judge entirely.
    cost:
      ceiling_per_session_usd: 10.00
      ceiling_per_day_usd: 100.00
    judge:
      enabled: false
```

Notes:
- `telegram.judge` only sets `threshold`; `model`, `provider`, and
  `enabled: true` are inherited from the project default via the
  shallow dict merge.
- `cli.scope` is omitted entirely, so CLI sessions get the exact same
  `allowed_topics`/`forbidden_topics` as the project default — only cost
  and judge behavior differ for that channel.
- Any channel not listed (e.g. `discord`) silently falls back to the
  project default policy — no extra code needed for that fallback.

## Example 2 — Channel with a fully different scope block

If a channel's allowed/forbidden topics need to look nothing like the
default (rather than just adding one or two forbidden topics), replace
the whole `scope` block for that channel. Because `scope` is a dict in
both the default and the override, the merge is still per-key inside
`scope` — so to truly replace `allowed_topics` you just list the new
value; you don't need to repeat unrelated keys like `canonical_fallback`
unless you also want to change it.

```yaml
project: support-bot

scope:
  allowed_topics: [orders, returns, shipping]
  forbidden_topics: [legal-advice]
  canonical_fallback: "I can help with orders, returns, and shipping."

cost:
  ceiling_per_session_usd: 1.00

channels:
  internal-slack:
    scope:
      allowed_topics: [orders, returns, shipping, refund-approvals, vendor-escalations]
      # forbidden_topics and canonical_fallback inherited unchanged
    cost:
      ceiling_per_session_usd: 5.00
```

## Example 3 — Minimal override (single field)

The smallest possible channel override: everything is inherited except
one cost ceiling.

```yaml
project: docs-helper

scope:
  allowed_topics: [documentation]
  forbidden_topics: []

cost:
  ceiling_per_session_usd: 1.50
  ceiling_per_day_usd: 15.00

channels:
  telegram:
    cost:
      ceiling_per_day_usd: 3.00   # only the daily ceiling tightens
```

Here `telegram` sessions keep `ceiling_per_session_usd: 1.50` from the
project default and the same `scope`, but the daily ceiling drops to 3.00.

## Calling it from your module

Any module that reads policy config should resolve the channel **once**,
right after loading the policy, then read its own keys off the resolved
dict exactly as if there were no channels at all — the channel plumbing
is invisible past this point.

```python
from hermes_guardrails.policy import load_policy, resolve_for_channel

def get_cost_ceiling(project: str, channel: str | None, period: str) -> float:
    """period is 'session' or 'day'."""
    policy = load_policy(project)
    effective = resolve_for_channel(policy, channel)

    cost_cfg = effective.get("cost", {})
    key = f"ceiling_per_{period}_usd"
    return cost_cfg.get(key, float("inf"))


def get_forbidden_topics(project: str, channel: str | None) -> list[str]:
    policy = load_policy(project)
    effective = resolve_for_channel(policy, channel)
    return effective.get("scope", {}).get("forbidden_topics", [])
```

Key points for implementers:
- Call `resolve_for_channel` **before** pulling any module-specific keys
  — never read `policy["scope"]` / `policy["cost"]` / etc. directly from
  the raw `load_policy()` result once a channel is known, or channel
  overrides will silently be ignored.
- Pass `channel=None` (or omit it) for contexts with no channel concept
  (e.g. a one-off script); `resolve_for_channel` returns the project
  default unchanged in that case.
- The resolved dict is a new dict for overridden top-level keys, but
  unmodified nested structures are shared with the original `policy`
  object — treat the returned dict as read-only, don't mutate it in
  place.
