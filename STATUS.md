# Status

Este documento descreve o estado real do projeto agora. O `README.md`/`README-pt.md` descrevem o produto final — o que o Motta Guardrails **vai ser**. Este arquivo descreve o que **já existe** no repositório, sem embelezar.

## O que existe hoje

- Repositório público, licença MIT.
- README bilíngue (inglês principal, português secundário) descrevendo a visão completa do produto.
- 19 issues abertas no GitHub cobrindo o roadmap de construção: scaffold do plugin, schema de política, os 14 módulos do cockpit, bridge de observabilidade, e preparação para submissão ao catálogo Hermes.
- Nenhum código de plugin executável ainda. Nenhuma das 19 issues foi implementada.

## Como este projeto é construído

Todas as 19 issues são tarefas de construção independentes, não etapas com gate de teste entre si. A integração e o teste de ponta a ponta acontecem **uma única vez, no final, quando todas as issues estiverem implementadas** — não a cada issue fechada. Isso é intencional: este é um projeto novo, sem usuário em produção, então não há custo de regressão a proteger issue por issue. Testar cada módulo isoladamente antes da integração completa seria tempo de desenvolvimento gasto duas vezes.

## Como acompanhar o progresso real

O roadmap público em [issues](https://github.com/fabiomotta0311/hermes-guardrails-plugin/issues) é a fonte de verdade sobre o que falta construir. Issue fechada = tarefa implementada. Repositório sem release = produto ainda não testado ponta a ponta.
