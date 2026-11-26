# Hermes Guardrails Cockpit

**O painel único de controle para tudo que o mercado chama de "Guardrails" em agentes de IA — dentro do Hermes Agent, sem sair dele.**

PII, prompt injection, fuga de escopo, vazamento de system prompt, controle de custo, LLM-as-judge e autoteste adversarial contínuo — configurados por projeto, num só lugar, com prova de cobertura mapeada aos frameworks de referência do mercado.

## Por que isso existe

Equipes que colocam agentes de IA em produção enfrentam a mesma pergunta de cliente/jurídico/compliance: *"como garantimos que isso não vai responder o que não deveria, não vai vazar dado sensível, e não vai sair do orçamento?"*

O Hermes Guardrails Cockpit responde isso com um painel único de configuração, auditável, versionado por projeto — em vez de dez scripts soltos e três ferramentas desconectadas.

## Cobertura

Mapeado contra as duas referências que qualquer avaliação de segurança de agente de IA cita:

### NeMo Guardrails — Rails

| Rail | Módulo |
|---|---|
| Input Rails | Scope & Intent Guard |
| Dialog Rails | Policy Engine por projeto |
| Retrieval Rails | Grounding / Faithfulness Check |
| Execution Rails | Tool Call Guard |
| Output Rails | Response Guard + Judge |

### OWASP Top 10 for LLM Applications

| Risco | Módulo correspondente |
|---|---|
| LLM01 Prompt Injection | Injection Guard + Adversarial Self-Test |
| LLM02 Sensitive Information Disclosure | PII Guard |
| LLM03 Supply Chain | Policy Versioning |
| LLM04 Data and Model Poisoning | Grounding Check |
| LLM06 Excessive Agency | Tool Call Guard |
| LLM07 System Prompt Leakage | Prompt Leak Guard |
| LLM08 Vector and Embedding Weaknesses | Grounding Check |
| LLM09 Overreliance | Judge + Confidence Threshold |
| LLM10 Unbounded Consumption | Cost Circuit Breaker |

## Módulos

| Módulo | O que faz |
|---|---|
| **Scope & Intent Guard** | Valida se a mensagem do usuário está dentro do roteiro permitido do projeto antes de qualquer chamada ao modelo |
| **Policy Engine** | Políticas versionadas e isoladas por projeto/cliente — escopo, tom, tópicos proibidos |
| **PII Guard** | Detecção e tokenização de dados pessoais em trânsito |
| **Injection Guard** | Defesa contra prompt injection direta e indireta (via conteúdo de ferramenta) |
| **Prompt Leak Guard** | Bloqueia tentativas de exfiltrar o system prompt / instruções internas |
| **Tool Call Guard** | Valida e pode bloquear chamadas de ferramenta fora de política |
| **Response Guard** | Valida a resposta final antes dela seguir no fluxo — pode substituir por resposta canônica |
| **LLM-as-Judge** | Avaliação configurável de qualidade/conformidade da resposta, com reason e threshold |
| **Cost Circuit Breaker** | Teto de gasto por sessão/projeto/dia, com bloqueio automático de novas chamadas |
| **Grounding / Faithfulness Check** | Confere se afirmações citadas estão apoiadas no contexto recuperado (RAG) |
| **Adversarial Self-Test** | Banco de ataques categorizado, execução on-demand/agendada, relatório de taxa de detecção e deriva ao longo do tempo |
| **Compliance Report Exporter** | Relatório de auditoria legível por não-técnico, por período/projeto |
| **Observability Bridge** | Emite eventos e métricas de guardrail no padrão OpenTelemetry, visíveis em qualquer backend compatível (Langfuse, Grafana Tempo, etc.) |
| **Channel Policy** | Rigor de política diferenciado por canal de entrega |

## Instalação

```bash
hermes plugins install fabiomotta0311/hermes-guardrails-plugin
hermes plugins enable hermes-guardrails
```

## Configuração

Políticas por projeto vivem em `~/.hermes/guardrails/<projeto>.yaml`. Ver [`docs/configuration.md`](docs/configuration.md).

## Status

Em desenvolvimento ativo. Ver [issues](https://github.com/fabiomotta0311/hermes-guardrails-plugin/issues) para o roadmap de implantação.

## Licença

MIT
