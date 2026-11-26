# PROMPT PARA REVISÃO EXTERNA — Motta Guardrails

Você vai revisar criticamente o plano de um projeto open source. Aja como revisor técnico e de produto sênior, sem complacência — aponte falhas reais de arquitetura, posicionamento, nomenclatura e sequenciamento de roadmap. Depois, numa segunda parte, pense de forma totalmente livre sobre o que esse produto deveria ser, sem se limitar ao que já foi decidido.

## Contexto do projeto

**Nome**: Motta Guardrails
**Repositório**: https://github.com/fabiomotta0311/hermes-guardrails-plugin (público, MIT)
**O que é**: um plugin para o Hermes Agent (framework de agentes de IA open source, github.com/NousResearch/hermes-agent) que consolida, num único "cockpit" configurável por projeto, todas as técnicas que o mercado chama de "Guardrails" para agentes de IA em produção.

## Objetivo de negócio (não só técnico)

O autor é um profissional de automação corporativa (RPA/SAP/integração) em processo de profissionalização em criação de agentes de IA. Clientes reais já pediram: (1) controle/observabilidade de custo de LLM, (2) garantia de que o agente não "sai do roteiro" por risco jurídico. O objetivo declarado não é só resolver isso tecnicamente — é também construir reconhecimento pessoal de marca ("Motta") no nicho de guardrails para agentes de IA, inspirado explicitamente no modelo de Matt Pocock (github.com/mattpocock/skills, 333k+ stars): tagline tribal, parágrafo de abertura falando com público não-técnico/comprador antes de qualquer tecnicismo, nome pessoal embutido nos comandos (`/setup-motta-guardrails`), manutenção pública visível via issues abertas.

## Decisões de arquitetura já tomadas

1. **Composição, não duplicação**: o plugin detecta e integra com plugins já existentes no catálogo do Hermes (`privacy-shield`, `injection-shield`) em vez de reimplementar PII/anti-injection do zero — para não entrar em rota de colisão de hooks nem disputa com autores já estabelecidos.
2. **Hooks/middleware nativos do Hermes** como base técnica: `pre_llm_call`/`post_llm_call` (hooks observadores) e `llm_request`/`llm_execution`/`tool_request`/`tool_execution` (middleware que pode reescrever ou bloquear antes da execução real). Fonte: `hermes-agent.nousresearch.com/docs/developer-guide/plugins` e `/middleware`.
3. **Buraco conhecido e assumido**: não existe hook nativo "antes da entrega final ao usuário" (`on_output`) no Hermes hoje — há uma issue aberta no upstream (#45881) pedindo exatamente isso. O plano assume cobertura parcial via `llm_execution` na última chamada do turno, com essa limitação documentada no disclosure, não escondida.
4. **Observabilidade protocol-first, não vendor-first**: em vez de integrar direto com o SDK do Langfuse, o plugin emite eventos via API padrão OpenTelemetry, aproveitando o contexto de span já aberto se o plugin `traceverse_hermes_otel` ou `langfuse` (oficial, no catálogo Hermes) estiver habilitado. Fallback para log local `.jsonl` se nenhum OTel estiver presente.
5. **Autoteste adversarial nativo + ponte pra ferramentas externas**: banco de ataques leve e curado dentro do plugin (não um motor de red-team completo) para autoteste/demo; compatibilidade via contrato de entrada/saída padronizado para ferramentas maduras externas (Promptfoo redteam, DeepTeam, Garak, PyRIT) em vez de competir com elas.
6. **Cobertura mapeada a duas taxonomias públicas reconhecidas**: NeMo Guardrails (5 categorias de rail: Input, Dialog, Retrieval, Execution, Output) e OWASP Top 10 for LLM Applications — para que o comprador reconheça a referência sem precisar entender a implementação.

## Lista de 14 módulos do v1

Scope & Intent Guard, Policy Engine (por projeto), PII Guard (integração+fallback), Injection Guard (integração+fallback), Prompt Leak Guard, Tool Call Guard, Response Guard, LLM-as-Judge, Cost Circuit Breaker (bloqueio ativo, não só observação), Grounding/Faithfulness Check, Adversarial Self-Test, Compliance Report Exporter, Observability Bridge (OTel), Channel Policy.

Dessas, a proporção estimada é: ~12 funcionalidades viabilizadas por capacidade já nativa do Hermes (hooks/middleware expostos), ~3 dependentes de biblioteca de terceiro que "faz o trabalho pesado" (ainda não auditadas quanto a licença — risco aberto), ~5 de desenvolvimento original (principalmente o Scope & Intent Guard determinístico por política de projeto, que foi identificado como o gap real de mercado — nenhum plugin do catálogo Hermes faz isso hoje).

## Roadmap de implantação (19 issues abertas no GitHub)

Issues #1-2: scaffold do plugin + schema de política YAML (core).
Issues #3-16: os 14 módulos listados acima, cada um com objetivo e critério de aceite próprio.
Issue #17: bridge de observabilidade OTel.
Issues #18-19: disclosure técnico para submissão ao catálogo + documento de correspondência completa NeMo+OWASP+origem-por-módulo.

## Decisão de naming e escopo deliberadamente excluído

- Nome "Guardrails" mantido (sem variação) após confirmar que não há colisão no catálogo do Hermes e que o termo é tratado como genérico de mercado, não propriedade do projeto "Guardrails AI" (biblioteca específica homônima).
- Um plugin separado de "Evals" (qualidade de resposta geral, G-Eval, gold dataset, não ligado a segurança) foi deliberadamente postergado para depois, para não diluir o posicionamento do Guardrails com um público comprador diferente (produto/qualidade vs. compliance/jurídico).
- README bilíngue: `README.md` (inglês, principal, por alcance global) e `README-pt.md` (português, mercado local). Abertura de ambos é um parágrafo de 3-5 frases falando com leitor não-técnico (problema → custo real de falhar → o que o produto entrega), antes de qualquer tagline técnica.

## PARTE 1 — O que pedimos pra você revisar

1. A decisão de composição (integrar com `privacy-shield`/`injection-shield` em vez de reimplementar) é sólida ou subestima risco de dependência de terceiro instável?
2. O sequenciamento das 19 issues está certo, ou algo deveria vir antes/depois do que está?
3. Existe algum ponto cego técnico na lista de 14 módulos — algo que a cobertura NeMo+OWASP sugere mas que não está coberto?
4. A estratégia de marca pessoal (nome "Motta" nos comandos, parágrafo de negócio no README, repo público desde o dia 1 com issues visíveis) tem algum risco que não foi considerado?
5. O posicionamento "Guardrails" vs. "Evals" como dois produtos separados está certo, ou deveria ser uma coisa só desde o início?

## PARTE 2 — Modo adicional: pensamento de negócio sem as amarras do que já decidimos

As seções acima descrevem decisões já tomadas. Para esta parte, ignore-as como restrição — queremos sua opinião **não-enviesada** por elas, pensando puramente como estrategista de produto/negócio, não como revisor de arquitetura.

Pergunta central: **se você fosse projetar do zero um "cockpit de Guardrails" para vender/posicionar no mercado de agentes de IA hoje, o que ele teria que a lista de 14 módulos acima não tem — ou nem imaginou?**

Para responder isso, pense em:

1. **Modelo de negócio, não só feature.** O cockpit deveria ser só plugin gratuito de reputação, ou existe uma versão paga/enterprise que faria sentido (ex.: hosted dashboard multi-cliente, certificação/selo de conformidade, suporte SLA)? Que camada de valor justificaria cobrança, mesmo que o core continue open source?
2. **O que todo comprador de guardrail vai perguntar em 12 meses que ninguém está perguntando hoje.** Regulação de IA está mudando rápido (EU AI Act, marcos regulatórios no Brasil). Que capacidade o cockpit precisaria ter *antes* de ser perguntado, para parecer visionário em vez de reativo?
3. **Efeito de rede / lock-in saudável.** Existe uma forma de o cockpit gerar dados/benchmarks agregados (anonimizados, opt-in) entre instalações que vire ativo competitivo único — algo que só existe porque muita gente usa, não que um concorrente consegue copiar de um dia pro outro?
4. **Categoria adjacente que ninguém está nomeando ainda.** "Guardrails" e "Evals" já são termos ocupados. Existe uma terceira categoria emergente (ex.: "auditoria contínua de agente", "seguro de comportamento de IA", "certificação de compliance de agente") que o cockpit poderia originar e dominar por ser o primeiro a nomear?
5. **O que um CFO, não um CTO, pagaria para resolver.** A lista atual fala a língua de compliance/jurídico/engenharia. O que a versão "fala com o financeiro" desse mesmo produto precisaria entregar (ROI mensurável, redução de prêmio de seguro, benchmark de custo por operação) para abrir outro orçamento inteiro dentro do cliente?

Não se limite a sugerir "módulo 15, 16, 17" na mesma lógica da lista atual — se a resposta certa for "isso deveria ser um produto diferente", ou "a arquitetura inteira devia ser outra", diga isso explicitamente. Queremos a ideia que a gente não teve, não validação da que já temos.
