[🇧🇷 Português](#português) | 🇺🇸 English

# Technical Disclosure

This document states, in plain language, exactly what Motta Guardrails sees,
accesses, and stores. It exists to let a reviewer (catalog maintainer,
client compliance team, or a project's own engineers) make a decision
without having to read the source first. No marketing language — if a claim
below is wrong, it is a bug in this document, file an issue.

## What the plugin sees

Motta Guardrails registers hooks and middleware on the following Hermes
lifecycle points: `pre_llm_call`, `post_llm_call`, `llm_request`,
`llm_execution`, `pre_tool_call`, `post_tool_call`, `tool_request`,
`tool_execution`.

Because every module is wired through this dispatcher, **every module that
is enabled sees**:

- Every user message sent to the agent, before the LLM is called.
- Every assistant response produced by the LLM, before it reaches the user.
- Every tool call the agent makes — the tool name and its arguments — before
  the tool executes.
- Every tool result — what the tool returned — before that result goes back
  to the LLM or the user.

There is no partial subscription model in v1: a module that is enabled for
a project and declares a hook/middleware for one of the points above
receives the full payload for that point (session id, message/response
text, model name, tool name/args/result, depending on the hook), not a
redacted or sampled version of it. If you enable Motta Guardrails for a
project, every message, response, and tool call in that project passes
through whichever modules you turned on.

## What gets written to disk

- **Cost Circuit Breaker** and **Adversarial Self-Test** write local state
  files under `~/.hermes/guardrails/` (spend counters and self-test run
  history, respectively). These files live on the machine running Hermes;
  nothing is uploaded anywhere by these modules.
- Per the module contract (`docs/module-contract.md`), any module that
  blocks or rewrites a request is expected to write an audit log entry
  (JSON line) to `~/.hermes/guardrails/audit/<project>.jsonl`.
- Policies are read from `~/.hermes/guardrails/<project>.yaml` (see
  `hermes_guardrails/policy.py`). Nothing in this plugin encrypts or
  restricts access to these files beyond normal filesystem permissions on
  the host running Hermes.

## What the audit log is documented to contain — and what that means for you

The audit log is **documented** to contain policy decisions and rule names
(e.g. "blocked — rule: out-of-scope-medical") and **not** raw prompt
content or secrets. That is the intended design, described in the module
contract and the README. **This disclosure cannot verify that every module
actually honors that design** — this document was written from this
isolated worktree, without running every module's code against real
traffic or reading every module file's audit-writing logic line by line.
Treat "audit logs don't contain raw prompt content" as a requirement each
module must honor, not as a verified fact about this codebase as it
stands. If you are evaluating this plugin for a project with sensitive
data, read the audit-writing code of every module you enable before
trusting that the log is safe to share with a third party (e.g. a client's
compliance team), and open an issue if you find one that writes raw
content.

## Network calls

**No module in this v1 makes an outbound network call on its own.** The
one exception is explicit, per-project user configuration: if you
configure an external LLM-as-judge or embedding provider (for modules such
as `LLM-as-Judge` or `Grounding / Faithfulness Check`), that module will
call the provider you configured, with whatever payload that integration
sends it.

Be aware that, as of this writing, several of the modules that would call
an external provider (judge scoring, embedding-based grounding checks) are
documented elsewhere in this repository as **stub / placeholder
implementations** — they may not yet perform a live call even when
configured to. Do not assume a module labelled "LLM-as-Judge" or
"Grounding Check" is actually reaching an external API in this version;
confirm against that module's own file and tests before relying on it.

## Summary table

| Question | Answer |
|---|---|
| Does it see every user message? | Yes, for any enabled module with a hook on `pre_llm_call`/`llm_request`. |
| Does it see every assistant response? | Yes, for any enabled module with a hook on `post_llm_call`/`llm_execution`. |
| Does it see every tool call and result? | Yes, for any enabled module with a hook on `pre_tool_call`/`post_tool_call`/`tool_request`/`tool_execution`. |
| Does it write files to disk? | Yes — policy YAML (read), audit JSONL (write), and state files for Cost Circuit Breaker / Adversarial Self-Test, all under `~/.hermes/guardrails/`. |
| Does it phone home / call external services by default? | No. Only if you explicitly configure an external judge/embedding provider for a specific module. |
| Are those external-provider integrations fully live today? | Partially — some are documented stubs. Verify per module before relying on them. |
| Is "no raw prompt content in audit logs" independently verified for every module? | No — it's the documented design, not something this disclosure confirms by inspection of every module's code. |

## Open verification items

These are not resolved by this document and must be checked before real
catalog submission or production use:

1. Run `hermes plugins doctor . --ci` and `hermes plugins validate . --json`
   against this repository and confirm they pass — not done as part of
   writing this disclosure.
2. Review the actual audit-log-writing code in every module file under
   `hermes_guardrails/modules/` to confirm none of them write raw prompt
   content, response content, or secrets to
   `~/.hermes/guardrails/audit/<project>.jsonl`.
3. Confirm, module by module, which external-provider integrations
   (LLM-as-judge, embedding/grounding) are live calls versus stubs, and
   update this document and the module's own docs accordingly when that
   changes.

---

## Português

# Disclosure Técnico

Este documento descreve, em linguagem direta, o que o Motta Guardrails vê,
acessa e armazena. Existe para permitir que um revisor (mantenedor do
catálogo, equipe de compliance de um cliente, ou os próprios engenheiros do
projeto) tome uma decisão sem precisar ler o código-fonte primeiro. Sem
linguagem de marketing — se alguma afirmação abaixo estiver errada, é um bug
deste documento, abra uma issue.

## O que o plugin vê

O Motta Guardrails registra hooks e middleware nos seguintes pontos do
ciclo de vida do Hermes: `pre_llm_call`, `post_llm_call`, `llm_request`,
`llm_execution`, `pre_tool_call`, `post_tool_call`, `tool_request`,
`tool_execution`.

Como todo módulo passa por esse dispatcher, **qualquer módulo habilitado
vê**:

- Toda mensagem do usuário enviada ao agente, antes da chamada ao LLM.
- Toda resposta do assistente produzida pelo LLM, antes de chegar ao
  usuário.
- Toda chamada de tool feita pelo agente — nome e argumentos — antes da
  tool executar.
- Todo resultado de tool — o que a tool retornou — antes de voltar ao LLM
  ou ao usuário.

Não há modelo de assinatura parcial na v1: um módulo habilitado para um
projeto que declara um hook/middleware em um dos pontos acima recebe o
payload completo daquele ponto (session id, texto da mensagem/resposta,
nome do modelo, nome/args/resultado da tool, dependendo do hook), não uma
versão redigida ou amostrada. Se você habilitar o Motta Guardrails em um
projeto, toda mensagem, resposta e chamada de tool daquele projeto passa
pelos módulos que você ativou.

## O que é gravado em disco

- **Cost Circuit Breaker** e **Adversarial Self-Test** gravam arquivos de
  estado locais em `~/.hermes/guardrails/` (contadores de gasto e
  histórico de execuções do self-test, respectivamente). Esses arquivos
  vivem na máquina que executa o Hermes; nada é enviado para fora por
  esses módulos.
- Conforme o contrato de módulo (`docs/module-contract.md`), qualquer
  módulo que bloqueia ou reescreve uma requisição deve gravar uma entrada
  de log de auditoria (linha JSON) em
  `~/.hermes/guardrails/audit/<project>.jsonl`.
- Políticas são lidas de `~/.hermes/guardrails/<project>.yaml` (ver
  `hermes_guardrails/policy.py`). Nada neste plugin criptografa ou
  restringe acesso a esses arquivos além das permissões normais do sistema
  de arquivos na máquina que executa o Hermes.

## O que o log de auditoria está documentado para conter — e o que isso significa

O log de auditoria está **documentado** para conter decisões de política e
nomes de regra (ex.: "blocked — rule: out-of-scope-medical") e **não**
conteúdo bruto de prompt ou segredos. Esse é o design pretendido, descrito
no contrato de módulo e no README. **Este disclosure não consegue verificar
se todo módulo realmente respeita esse design** — este documento foi
escrito a partir deste worktree isolado, sem executar o código de cada
módulo contra tráfego real ou ler linha a linha a lógica de escrita de
auditoria de cada arquivo de módulo. Trate "logs de auditoria não contêm
conteúdo bruto de prompt" como um requisito que cada módulo deve respeitar,
não como um fato verificado sobre este código no estado atual. Se você está
avaliando este plugin para um projeto com dados sensíveis, leia o código de
escrita de auditoria de cada módulo que habilitar antes de confiar que o
log é seguro para compartilhar com terceiros (ex.: equipe de compliance de
um cliente), e abra uma issue se encontrar algum que grave conteúdo bruto.

## Chamadas de rede

**Nenhum módulo nesta v1 faz chamada de rede de saída por conta própria.**
A única exceção é configuração explícita por projeto, feita pelo usuário:
se você configurar um provedor externo de LLM-as-judge ou embedding (para
módulos como `LLM-as-Judge` ou `Grounding / Faithfulness Check`), esse
módulo vai chamar o provedor que você configurou, com o payload que essa
integração enviar.

Esteja ciente de que, no momento da escrita, vários dos módulos que
chamariam um provedor externo (scoring de judge, checagem de grounding
baseada em embedding) estão documentados em outras partes deste
repositório como **implementações stub / placeholder** — podem ainda não
realizar uma chamada real mesmo quando configurados para isso. Não assuma
que um módulo chamado "LLM-as-Judge" ou "Grounding Check" está de fato
alcançando uma API externa nesta versão; confirme no próprio arquivo e nos
testes daquele módulo antes de confiar nisso.

## Tabela-resumo

| Pergunta | Resposta |
|---|---|
| Vê toda mensagem do usuário? | Sim, para qualquer módulo habilitado com hook em `pre_llm_call`/`llm_request`. |
| Vê toda resposta do assistente? | Sim, para qualquer módulo habilitado com hook em `post_llm_call`/`llm_execution`. |
| Vê toda chamada e resultado de tool? | Sim, para qualquer módulo habilitado com hook em `pre_tool_call`/`post_tool_call`/`tool_request`/`tool_execution`. |
| Grava arquivos em disco? | Sim — YAML de política (leitura), JSONL de auditoria (escrita), e arquivos de estado do Cost Circuit Breaker / Adversarial Self-Test, todos sob `~/.hermes/guardrails/`. |
| Faz chamadas externas por padrão? | Não. Somente se você configurar explicitamente um provedor externo de judge/embedding para um módulo específico. |
| Essas integrações com provedor externo já estão realmente ativas hoje? | Parcialmente — algumas são stubs documentados. Verifique módulo por módulo antes de confiar. |
| "Sem conteúdo bruto de prompt nos logs de auditoria" está verificado de forma independente para todo módulo? | Não — é o design documentado, não algo que este disclosure confirma por inspeção do código de cada módulo. |

## Itens abertos de verificação

Não resolvidos por este documento; precisam ser checados antes da
submissão real ao catálogo ou uso em produção:

1. Executar `hermes plugins doctor . --ci` e
   `hermes plugins validate . --json` contra este repositório e confirmar
   que passam — não feito como parte da escrita deste disclosure.
2. Revisar o código real de escrita de log de auditoria em cada arquivo de
   módulo sob `hermes_guardrails/modules/` para confirmar que nenhum grava
   conteúdo bruto de prompt, de resposta, ou segredos em
   `~/.hermes/guardrails/audit/<project>.jsonl`.
3. Confirmar, módulo por módulo, quais integrações com provedor externo
   (LLM-as-judge, embedding/grounding) são chamadas reais versus stubs, e
   atualizar este documento e a documentação do respectivo módulo quando
   isso mudar.
