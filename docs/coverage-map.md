# Coverage map — NeMo Guardrails × OWASP LLM Top 10 × OWASP Agentic Applications Top 10

This document is the single, standalone reference for how every module of the
finished Motta Guardrails cockpit maps onto the three reference taxonomies the
project claims coverage against. It describes the **intended full mapping for
the finished product** (per the philosophy in `STATUS.md`) — it is not a
build-progress checklist. Whether a given module/row has shipped code yet is
tracked in `STATUS.md` and the [issue tracker](https://github.com/fabiomotta0311/hermes-guardrails-plugin/issues),
not here.

## Why three separate taxonomies, not two

- **NeMo Guardrails rails** (Input / Dialog / Retrieval / Execution / Output)
  describe *where in the request lifecycle* a control sits. This is an
  architectural axis, not a risk taxonomy.
- **OWASP Top 10 for LLM Applications** describes *model-layer* risks: what
  can go wrong because there is an LLM answering with natural language over a
  prompt/context. This is the taxonomy most people mean by "OWASP LLM Top 10."
- **OWASP Top 10 for Agentic Applications (2026)** describes a *different*
  set of concerns that only exist once the system is an **agent** — i.e. it
  plans, holds memory across turns, calls tools, delegates work, and acts
  with some form of granted authority. Identity/privilege abuse, tool
  misuse, memory/context poisoning, inter-agent communication and rogue
  autonomous behavior are not covered by the LLM Top 10 at all.

These two OWASP lists are **not versions of each other** and must not be
conflated as if "Agentic 2026" were simply "LLM Top 10, new edition." A
module can be relevant to one, the other, both, or neither — hence the two
separate OWASP columns below, each resolved independently per module.

### Edition note (OWASP LLM Top 10)

The OWASP Top 10 for LLM Applications 2023/2024 edition's **LLM09** risk is
named **"Overreliance."** The subsequent OWASP revision **relabels/replaces**
that slot with **"Misinformation."** Because the ID "LLM09" alone is
ambiguous between those two editions, every reference to LLM09 in this
document is written as `LLM09 (2023/2024: Overreliance)` so the specific
edition being cited is explicit rather than implied.

### Edition note (OWASP Agentic Applications)

References below use the `ASI0x` identifiers from the **OWASP Top 10 for
Agentic Applications 2026** (the agent-specific list — Agent Goal Hijack,
Tool Misuse & Exploitation, Identity & Privilege Abuse, Agentic Supply Chain
Vulnerabilities, Unexpected Code Execution, Memory & Context Poisoning,
Insecure Inter-Agent Communication, Cascading Agent Failures, Human-Agent
Trust Exploitation, Rogue Agents). This list is maintained independently of
the LLM Top 10 and is cited here as its own taxonomy, never merged into the
LLM0x numbering.

## Legend

- **NeMo rail**: Input / Dialog / Retrieval / Execution / Output, per NeMo
  Guardrails' five rail categories.
- **OWASP LLM Top 10 ref**: risk ID + name, edition noted per the convention
  above. `n/a` if the module addresses no LLM-Top-10 risk directly.
- **OWASP Agentic 2026 ref**: `ASI0x` ID + name from the 2026 Agentic
  Applications list. `n/a` if the module is not agent-specific (e.g. it
  guards the model layer regardless of whether an agent loop is present).
- **Origin**: `native hook/middleware` (wired directly into Hermes'
  pre/post-call lifecycle per `docs/module-contract.md`), `third-party
  integration` (wraps/calls an external tool or service), or `original
  implementation` (logic authored in this repo with no native hook
  dependency or external engine).
- **Issue(s)**: the GitHub issue(s) that build the module's file(s) under
  `hermes_guardrails/modules/`.

## Coverage table

| Module | NeMo rail | OWASP LLM Top 10 ref | OWASP Agentic 2026 ref | Origin | Issue(s) |
|---|---|---|---|---|---|
| **Scope & Intent Guard** | Input | LLM01 Prompt Injection (2023/2024) | ASI01 Agent Goal Hijack | native hook/middleware (`pre_llm_call` / `llm_request`) | #3 (v1 regex/keywords), #4 (v2 embedding similarity) |
| **Policy Engine** | Dialog | n/a — governs which rails/rules apply per project, not a risk itself | ASI03 Identity & Privilege Abuse (defines what the agent/project is authorized to do) | original implementation (YAML schema + loader, `hermes_guardrails/policy.py`) | #2 |
| **PII Guard** | Input / Output | LLM02 Sensitive Information Disclosure (2023/2024) | n/a | native hook/middleware (`pre_llm_call`, `post_llm_call`) with third-party integration (PII detection engine) fallback | #8 |
| **Injection Guard** | Input | LLM01 Prompt Injection (2023/2024) | ASI01 Agent Goal Hijack | native hook/middleware (`pre_llm_call` / `llm_request`) with third-party integration fallback | #9 |
| **Prompt Leak Guard** | Output | LLM07 System Prompt Leakage (2023/2024) | n/a | native hook/middleware (`post_llm_call` / `llm_execution`) | #7 |
| **Tool Call Guard** | Execution | LLM06 Excessive Agency (2023/2024) | ASI02 Tool Misuse & Exploitation | native hook/middleware (`pre_tool_call` / `tool_request`) | #6 |
| **Response Guard** | Output | LLM05 Improper Output Handling (2023/2024)¹ | n/a | native hook/middleware (`post_llm_call` / `llm_execution`) | #5 |
| **LLM-as-Judge** | Output | LLM09 (2023/2024: Overreliance) — later revision: Misinformation | n/a | third-party plugin integration (configurable judge model) | #11 |
| **Cost Circuit Breaker** | Execution | LLM10 Unbounded Consumption (2023/2024) | ASI08 Cascading Agent Failures (stops runaway spend from cascading across calls/sessions) | native hook/middleware (`pre_llm_call`/`pre_tool_call` + OpenTelemetry cost signal) | #10 |
| **Grounding / Faithfulness Check** | Retrieval | LLM04 Data and Model Poisoning (2023/2024) / LLM08 Vector and Embedding Weaknesses (2023/2024) | n/a | original implementation (claim-vs-context comparison) | #12 |
| **Adversarial Self-Test** | n/a — offline/scheduled evaluation, not a live rail | LLM01 Prompt Injection (2023/2024) (attack bank exercises this risk) | ASI01 Agent Goal Hijack, ASI09 Human-Agent Trust Exploitation | original implementation (categorized attack bank + scoring) | #13 |
| **Red-Team Bridge (Promptfoo/DeepTeam/Garak)** | n/a — offline/scheduled evaluation, extends Adversarial Self-Test | LLM01 Prompt Injection (2023/2024) | ASI01 Agent Goal Hijack, ASI09 Human-Agent Trust Exploitation | third-party plugin integration (Promptfoo / DeepTeam / Garak runners) | #14 |
| **Compliance Report Exporter** | n/a — reporting layer over audit logs, not a rail | n/a — aggregates evidence, not a control for a single risk | n/a | original implementation (report generation over the audit log) | #15 |
| **Observability Bridge** | n/a — cross-cutting telemetry, not a rail | n/a — observability, not a control for a single risk | ASI08 Cascading Agent Failures (surfaces drift/cost signals before failures cascade) | third-party plugin integration (OpenTelemetry exporter) | #17 |
| **Channel Policy** | Dialog | n/a — adjusts strictness of other rails per channel, not a risk itself | n/a | original implementation (per-channel policy overlay on the Policy Engine) | #16 |

¹ LLM05 "Improper Output Handling" is the 2023/2024-edition risk most
directly addressed by validating the LLM's raw output before it propagates
downstream; Response Guard's final-response validation is this module's
primary OWASP LLM anchor.

## Rail coverage summary (NeMo Guardrails)

| Rail | Modules |
|---|---|
| Input | Scope & Intent Guard, PII Guard (inbound), Injection Guard |
| Dialog | Policy Engine, Channel Policy |
| Retrieval | Grounding / Faithfulness Check |
| Execution | Tool Call Guard, Cost Circuit Breaker |
| Output | PII Guard (outbound), Prompt Leak Guard, Response Guard, LLM-as-Judge |
| Cross-cutting (no single rail) | Adversarial Self-Test, Red-Team Bridge, Compliance Report Exporter, Observability Bridge |

## OWASP LLM Top 10 coverage summary (edition-qualified)

| Risk | Module(s) |
|---|---|
| LLM01 Prompt Injection (2023/2024) | Scope & Intent Guard, Injection Guard, Adversarial Self-Test, Red-Team Bridge |
| LLM02 Sensitive Information Disclosure (2023/2024) | PII Guard |
| LLM03 Supply Chain (2023/2024) | Policy Versioning (see README; no dedicated module file in #3–#17) |
| LLM04 Data and Model Poisoning (2023/2024) | Grounding / Faithfulness Check |
| LLM05 Improper Output Handling (2023/2024) | Response Guard |
| LLM06 Excessive Agency (2023/2024) | Tool Call Guard |
| LLM07 System Prompt Leakage (2023/2024) | Prompt Leak Guard |
| LLM08 Vector and Embedding Weaknesses (2023/2024) | Grounding / Faithfulness Check |
| LLM09 (2023/2024: Overreliance) — later revision: Misinformation | LLM-as-Judge |
| LLM10 Unbounded Consumption (2023/2024) | Cost Circuit Breaker |

## OWASP Agentic Applications 2026 coverage summary

| Risk (ASI0x) | Module(s) | Why it applies here |
|---|---|---|
| ASI01 Agent Goal Hijack | Scope & Intent Guard, Injection Guard, Adversarial Self-Test, Red-Team Bridge | Blocks/tests attempts to redirect the agent off its authorized scope |
| ASI02 Tool Misuse & Exploitation | Tool Call Guard | Validates/blocks out-of-policy tool invocations before execution |
| ASI03 Identity & Privilege Abuse | Policy Engine | Defines and enforces per-project/client authorization boundaries |
| ASI08 Cascading Agent Failures | Cost Circuit Breaker, Observability Bridge | Caps spend and surfaces drift before a single failure cascades across calls/sessions |
| ASI09 Human-Agent Trust Exploitation | Adversarial Self-Test, Red-Team Bridge | Exercises multi-turn and social-engineering-style escalation against the agent |

ASI04 (Agentic Supply Chain), ASI05 (Unexpected Code Execution), ASI06
(Memory & Context Poisoning), ASI07 (Insecure Inter-Agent Communication) and
ASI10 (Rogue Agents) have no dedicated module in the current roadmap
(issues #1–#17); they are out of scope for this cockpit's first shipped
surface and are noted here so the gap is explicit rather than silently
dropped.

## Scope of this document

This table covers the 14 modules listed in `README.md`'s "Modules" table,
plus the Red-Team Bridge module built by issue #14, which is a dedicated
module file under `hermes_guardrails/modules/` but ships as an extension of
Adversarial Self-Test rather than a 15th standalone README entry. Core
scaffolding issues (#1 plugin/hook wiring, #2 policy schema) are referenced
above only insofar as they underpin the Policy Engine row; they are not
themselves rows because they are infrastructure, not guardrail modules.
