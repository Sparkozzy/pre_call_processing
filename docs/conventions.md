# Convenções de Código - Python Workflow (EDW)

Este documento define as diretrizes obrigatórias para o desenvolvimento de fluxos de trabalho orientados a eventos (Event-Driven Workflows) neste projeto.

## 🕒 Gestão de Tempo e Datas

- **Banco de Dados**: O campo `created_at` (e similares que vão para a persistência) deve usar o formato **ISO 8601 em UTC/Z**.
- **Lógica de Código**: Datas manipuladas internamente devem seguir o formato ISO 8601, mas convertidas para o fuso horário de Brasília (**America/Sao_Paulo**).
- **Funções padrão:**
  - `get_utc_now()` → ISO 8601 UTC para persistência.
  - `get_br_now()` → datetime com fuso `America/Sao_Paulo` para lógica interna.
  - `parse_iso_to_br(iso_date)` → Converte ISO 8601 de qualquer fuso para Brasília.
- **Validação de Webhook**: O campo `quando_ligar` deve obrigatoriamente conter timezone offset (ex: `-03:00` ou `Z`). Payloads sem fuso são rejeitados com `400 Bad Request`.

## 🔗 Comunicação entre Workflows

Ao acionar ou transitar entre fluxos, os seguintes metadados de rastreabilidade são **obrigatórios**:
- `workflow_id`: Identificador fixo do tipo de workflow.
- `from_workflow`: Nome do workflow de origem.
- `execution_id`: Identificador único da execução atual (UUID).

## 🧩 Definição de Nós (Nodes)

- Um **Nó** é a **mínima ação rastreável** dentro de um workflow.
- **Regra de Ouro**: Nunca mescle ações distintas. Busca de dados, transformação e envio externo devem ser nós separados.
- Exemplo de sequência atual (workflow `pre_call_processing`):
  1. `agendamento_redis` — Decisão de timing (imediato vs futuro via Redis `_defer_until`).
  2. `fetch_prompt` — Busca dados no Supabase (async).
  3. `format_payload` — Transformação de dados e substituição de variáveis.
  4. `create_retell_call` — Envio assíncrono (httpx) para API externa (Retell AI).

### Executor Genérico (`run_step_with_retry`)

Todos os nós devem ser executados via `run_step_with_retry()`. Esta função garante:
- Registro automático de cada tentativa em `workflow_step_executions` (sucesso e falha).
- Retry configurável por nó (`max_retries`) com **exponential backoff + jitter** (`2^attempt + random(0,1)`, cap 30s).
- Recebe um `worker_func` opcional (async) com a lógica real; sem ele, executa simulação (fallback).
- Todas as chamadas internas usam `await` (non-blocking).

## 🏗️ Stack Tecnológica

- **Backend**: Sempre usar **Python**.
- **Frameworks**: Usar **FastAPI** ou **FastMCP**.
- **Proibido**: Nunca utilizar Flask, `requests` (bloqueante), `time.sleep()`, `BackgroundTasks` (FastAPI), ou `APScheduler`.
- **HTTP Requests**: Usar `httpx.AsyncClient()` para chamadas assíncronas a APIs externas (ex: Retell AI).
- **Filas e Agendamento**: Usar `arq` (Async Redis Queue) para processamento em background e agendamento de tarefas futuras.

## 📛 Convenção de Nomenclatura

- **Workflows**: Nomes únicos e descritivos em `snake_case` (ex: `pre_call_processing`).
- **Steps (Passos)**: Devem seguir o padrão `{{workflow_name}}_{{OQF}}`, onde OQF é "O Que Faz" (ex: `pre_call_processing_fetch_prompt`).

## ✍️ Tratamento de Prompts

### Substituição de Variáveis
Prompts buscados do Supabase podem conter variáveis de contexto nos formatos:
- `{{variavel}}` ou `{{ variavel }}` (double braces)
- `{variavel}` ou `{ variavel }` (single braces — fallback)

Variáveis suportadas no mapeamento atual:

| Variável | Origem |
| :--- | :--- |
| `customer_name` | `payload.nome` |
| `empresa` | `payload.empresa` |
| `segmento` | `payload.segmento` |
| `email` | `payload.email` |
| `numero_do_lead` | `payload.numero` |
| `now` | `get_br_now()` formatado DD/MM/YYYY HH:MM |
| `data_atual_iso` | `get_utc_now()` |

### Limpeza de Markdown (`strip_markdown`)
Após a substituição de variáveis, o texto é limpo para compatibilidade com TTS (Text-to-Speech):
- Remove bold (`**texto**`), italic (`*texto*`, `__texto__`).
- Remove headers (`# Titulo`).
- Remove bullets (`- item`, `* item`, `+ item`).
- Remove blockquotes (`> texto`).
- **Não** remove underscores simples (`_`) para preservar nomes de variáveis/termos técnicos.

## ⏰ Agendamento e Resiliência (Redis + ARQ)

Para fluxos que exigem execução futura, seguimos o padrão de **Agendamento Persistente via Redis**:

1.  **Imediata**: Se a data agendada for passada ou atual, a execução segue para o próximo nó imediatamente.
2.  **Futuro**: Se a data for futura, utilizamos o `arq` com `_defer_until` para agendar o job no Redis de forma **persistente**.
3.  **Rastreabilidade de Agendamento**: O ato de agendar é um **Nó (Step)** (`agendamento_redis`). Deve ser registrado em `workflow_step_executions` como `SUCCESS`, contendo no `output_data` a confirmação do horário agendado.
4.  **Resposta do Webhook**: O webhook deve retornar `202 Accepted` imediatamente após criar o registro mestre e enfileirar no Redis.
5.  **Persistência**: Jobs agendados vivem no Redis. Restarts do servidor **não perdem** jobs pendentes (desde que o Redis esteja ativo).

## 📊 Estrutura de Monitoramento (Mestre-Detalhe)

Toda execução deve ser registrada no Supabase seguindo o padrão Mestre-Detalhe:

1. **Início do Fluxo**: Registrar entrada em `workflow_executions` (Master) com status `PENDING`.
2. **Início de Execução**: Atualizar status para `RUNNING` ao começar os nós.
3. **Cada Passo**: Registrar cada tentativa e resultado em `workflow_step_executions` (Detail).
4. **Finalização**: Atualizar registro mestre para `SUCCESS` (com `call_id`) ou `FAILED` (com `error_details`).

### Tabela: workflow_executions
- `status`: PENDING → RUNNING → SUCCESS | FAILED.
- `input_data` / `output_data`: JSONB.

### Tabela: workflow_step_executions
- `execution_id`: FK para a tabela mestre.
- `step_name`: Nome seguindo a convenção de nomenclatura.
- `attempt`: Contador de tentativas (inicia em 1).
- `status`: SUCCESS, FAILED, SKIPPED.

---
*Este documento é a fonte da verdade para o desenvolvimento do ecossistema MindFlow.*

## 🗃️ Estrutura da tabela "Prompts"

Esta tabela é utilizada por engenheiros de prompts. É uma proteção para que eles não precisem acessar os códigos para mudar os prompts e nem precisem fazer commit toda vez que forem mudar uma palavra nos prompts.

### Colunas:

- `id`: identificador numérico único do prompt. É a informação que enviamos na requisição para o Supabase na hora de buscar os prompts.
- `created_at`: data de criação dos prompts.
- `Nome do cliente`: identifica para qual cliente aquele prompt foi criado.
- `Prompt_Text`: prompt para ser utilizado exclusivamente no WhatsApp.
- `Ligação/txt`: Prompt para ser utilizado em ligações. **Priorizado pelo workflow `pre_call_processing`.**
- `Pormpt_Name`: nome do prompt. É utilizado como um "apelido" para facilitar a vida de engenheiros de prompt. Também serve como chave de busca alternativa quando `Prompt_id` não é numérico.
- `Prompt Insta`: Prompt para agente do Instagram.
