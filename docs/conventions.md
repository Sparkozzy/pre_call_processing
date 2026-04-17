# Convenções de Código - Python Workflow (EDW)

Este documento define as diretrizes obrigatórias para o desenvolvimento de fluxos de trabalho orientados a eventos (Event-Driven Workflows) neste projeto.

## 🕒 Gestão de Tempo e Datas

- **Banco de Dados**: O campo `created_at` (e similares que vão para a persistência) deve usar o formato **ISO 8601 em UTC/Z**.
- **Lógica de Código**: Datas manipuladas internamente devem seguir o formato ISO 8601, mas convertidas para o fuso horário de Brasília (**America/Sao_Paulo**).

## 🔗 Comunicação entre Workflows

Ao acionar ou transitar entre fluxos, os seguintes metadados de rastreabilidade são **obrigatórios**:
- `workflow_id`: Identificador fixo do tipo de workflow.
- `from_workflow`: Nome do workflow de origem.
- `execution_id`: Identificador único da execução atual (UUID).

## 🧩 Definição de Nós (Nodes)

- Um **Nó** é a **mínima ação rastreável** dentro de um workflow.
- **Regra de Ouro**: Nunca mescle ações distintas. Busca de dados, transformação e envio externo devem ser nós separados.
- Exemplo de sequência correta:
  1. `schedule_started`
  2. `fetch_data`
  3. `transform_data`
  4. `send_external_api`

## 🏗️ Stack Tecnológica

- **Backend**: Sempre usar **Python**.
- **Frameorks**: Usar **FastAPI** ou **FastMCP**.
- **Proibido**: Nunca utilizar Flask ou frameworks que não suportem nativamente padrões modernos de asincronia e performance exigidos para EDW.

## 📛 Convenção de Nomenclatura

- **Workflows**: Nomes únicos e descritivos (ex: `envia_ligacao`).
- **Steps (Passos)**: Devem seguir o padrão `{{workflow_name}}_{{OQF}}`, onde OQF é "O Que Faz" (ex: `envia_ligacao_recebe_webhook`).

## ⏰ Agendamento e Resiliência (APScheduler)

Para fluxos que exigem execução futura, seguimos o padrão de **Agendamento em RAM**:

1.  **Imediação**: Se a data agendada for passada ou atual, a execução segue para o próximo nó imediatamente.
2.  **Futuro**: Se a data for futura, utilizamos o `APScheduler` (BackgroundScheduler) para programar a chamada da função de continuação do workflow na RAM.
3.  **Rastreabilidade de Agendamento**: O ato de agendar é um **Nó (Step)**. Deve ser registrado em `workflow_step_executions` como `SUCCESS`, contendo no `output_data` a confirmação do horário agendado.
4.  **Resposta do Webhook**: O webhook deve retornar `202 Accepted` imediatamente após criar o registro mestre e delegar para o nó de agendamento.

## 📊 Estrutura de Monitoramento (Mestre-Detalhe)

Toda execução deve ser registrada no Supabase seguindo o padrão Mestre-Detalhe:

1. **Início do Fluxo**: Registrar entrada em `workflow_executions` (Master).
2. **Cada Passo**: Registrar cada tentativa e resultado em `workflow_step_executions` (Detail).

### Tabela: workflow_executions
- `status`: PENDING, RUNNING, SUCCESS, FAILED.
- `input_data` / `output_data`: JSONB.

### Tabela: workflow_step_executions
- `execution_id`: FK para a tabela mestre.
- `step_name`: Nome seguindo a convenção de nomenclatura.
- `attempt`: Contador de tentativas (inicia em 1).
- `status`: SUCCESS, FAILED, SKIPPED.

---
*Este documento é a fonte da verdade para o desenvolvimento do ecossistema MindFlow.*
