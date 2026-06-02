# Workflow: Agendamento de Disparos via CSV (`csv_scheduling`)

Este documento estabelece o design técnico e a especificação de funcionamento para a ingestão, validação e agendamento temporal de ligações telefônicas em lote a partir do upload de arquivos CSV no ecossistema `pre_call_processing`, utilizando exclusivamente as tabelas existentes de rastreabilidade do Supabase.

---

## 🎯 1. Objetivo do Workflow

Permitir o upload de arquivos CSV contendo múltiplos leads para realizar o agendamento em massa de disparos de chamadas telefônicas. O sistema distribui o agendamento de cada chamada de acordo com uma frequência (intervalo de tempo em segundos) parametrizada pelo usuário, gerencia a observabilidade de forma assíncrona para evitar gargalos utilizando o padrão Parent-Child nas tabelas de auditoria existentes e oferece um mecanismo de interrupção imediata de todos os disparos programados de forma segura (Kill Switch).

---

## 🔗 2. Especificação do Endpoint

### Rota: `POST /webhook/csv`
*   **Autenticação**: Header `X-API-Key` (mesma chave configurada em `WEBHOOK_API_KEY`).
*   **Content-Type**: `multipart/form-data`
*   **Payload da Requisição**:
    *   `file` (UploadFile, obrigatório): Arquivo `.csv`.
    *   `contexto` (string, opcional): Contexto base global da chamada.
    *   `frequencia` (float, obrigatório): Intervalo em segundos entre cada disparo ($\ge 1$).
    *   `agent_id` (string, obrigatório): Identificador do agente no Retell AI.
    *   `prompt_id` (string, obrigatório): ID numérico do prompt de referência no Supabase.

### Resposta de Sucesso (`200 OK`)
```json
{
  "status": "success",
  "message": "Arquivo CSV validado com sucesso. Processamento assíncrono de ingestão iniciado.",
  "batch_id": "c8e268a0-2f95-46cb-8d02-eb828b8cf4c8",
  "total_leads": 12500
}
```

### Resposta de Falha de Validação (`400 Bad Request` ou `422 Unprocessable Entity`)
```json
{
  "status": "error",
  "detail": "Validação de dados falhou: Coluna obrigatória 'numero' ausente no cabeçalho do CSV."
}
```

---

## 📋 3. Regras de Validação Síncrona (Na API)

Para garantir que a requisição seja respondida instantaneamente e com segurança, a validação é executada na thread HTTP síncrona usando leitura em fluxo (streaming):

1.  **Validação dos Parâmetros Globais**:
    *   Verifica se `frequencia` é um número válido e $\ge 1$.
    *   Verifica se `agent_id` e `prompt_id` foram informados.
2.  **Validação da Estrutura do CSV**:
    *   O arquivo deve possuir cabeçalho na primeira linha contendo obrigatoriamente as colunas: `numero`, `nome` e `email`.
3.  **Validação de Dados das Linhas**:
    *   Nenhum registro pode conter o campo `numero` ou `nome` vazio/nulo.
    *   Caso a coluna `numero` possua registros sem o sinal de `+`, o validador lança um erro instruindo a correção (toda chamada internacional exige DDI ex: `+55...`).
    *   Se o campo `email` estiver vazio em alguma linha, a validação o aceita e o preenche dinamicamente com o valor `"."` (um único ponto) antes do enfileiramento, evitando falhas de processamento.
4.  **Ação em caso de Falha**:
    *   Qualquer linha inválida ou coluna obrigatória faltante causa a rejeição imediata da requisição inteira, retornando o erro explicativo com status HTTP adequado, **sem gravar dados ou agendar nenhuma ligação**.

---

## ⚙️ 4. Fluxo de Ingestão e Rastreabilidade EDW (Supabase)

Se o arquivo e os parâmetros passarem na validação síncrona inicial, o lote é registrado como uma execução do workflow `"csv_scheduling"` na tabela **`workflow_executions`** (sem a criação de tabelas dedicadas):

1.  **Capa do Lote**: A API insere um registro mestre na tabela existente `workflow_executions`:
    *   `workflow_name`: `"csv_scheduling"`
    *   `status`: `"RUNNING"` (inicia processamento)
    *   `input_data`: `{ "contexto": contexto, "frequencia": frequencia, "agent_id": agent_id, "prompt_id": prompt_id, "file_name": file.filename }`
    *   `started_at`: `get_utc_now()`
    *   *Nota*: O `id` (UUID) retornado por essa inserção será o nosso **`batch_id`**.
2.  **Salvamento Local**: Salva temporariamente o arquivo CSV localmente no diretório `scratch/` ou temporário com o nome `batch_{batch_id}.csv`.
3.  **Enfileiramento**: Enfileira o job `ingest_csv_batch` no Redis passando o `batch_id` e o caminho do arquivo, e retorna `200 OK` de imediato com o `batch_id` para o cliente.

### Processamento no Worker (`ingest_csv_batch`):
*   O Worker registra o início da ingestão na tabela **`workflow_step_executions`**:
    *   `execution_id`: `batch_id`
    *   `step_name`: `"csv_scheduling_ingestion"`
    *   `status`: `"RUNNING"`
*   Abre o arquivo CSV em pedaços (batches internos de **2.000 linhas**) usando Pandas (`chunksize`) ou `csv.DictReader` em blocos para manter o consumo de RAM estável.
*   **Mapeamento de Contexto**: Para cada linha do CSV, o Worker extrai as colunas obrigatórias. Quaisquer outras colunas adicionais detectadas na planilha (ex: `cidade`, `produto`) serão convertidas em string e concatenadas ao final do campo `contexto` individual daquele lead no formato:
    `"cidade: Florianópolis; produto: Licença Premium; "` adicionado após o `contexto` global.
*   **Cálculo da Fila Temporal (Frequência)**:
    Cada lead $i$ da lista (index 0) é enfileirado para execução futura no Redis através do ARQ utilizando a propriedade `_defer_until` calculada em Brasília (`America/Sao_Paulo`):
    $$\text{Tempo de Disparo } (i) = \text{Data Atual} + (i \times \text{frequencia})$$
    O payload do lead enfileirado no Redis conterá o campo `"from_batch_id"` associado ao UUID do lote.
*   Ao terminar a ingestão de todas as linhas com sucesso:
    *   Atualiza o status de `"csv_scheduling_ingestion"` em `workflow_step_executions` para `"SUCCESS"` com o total de leads no `output_data`.
    *   Atualiza o status mestre do lote na tabela `workflow_executions` para `"SUCCESS"` (com `total_leads` no `output_data`).
    *   Exclui o CSV temporário do disco.

---

## 🛑 5. Mecanismo de Encerramento Imediato (Kill Switch Híbrido)

### Rota de Cancelamento: `POST /webhook/csv/cancel`
*   **Payload (JSON/Form)**: `{"batch_id": "UUID-DO-LOTE"}` (opcional)
*   **Comportamento**:
    *   **Cancelamento de Lote Específico**: Se `batch_id` for fornecido, a API muda o status da execução com esse `id` na tabela `workflow_executions` para `"FAILED"` com `error_details = "Cancelado pelo usuário"`, e cria a chave de interrupção rápida no Redis:
        `batch:{batch_id}:status = "cancelled"`
    *   **Cancelamento Global (Panic Button)**: Se nenhum `batch_id` for fornecido, a API busca na `workflow_executions` todas as execuções de `workflow_name = "csv_scheduling"` com status `"RUNNING"` ou `"PENDING"` e as atualiza para `"FAILED"`, ativando a flag global no Redis:
        `system:status = "cancelled"`

### Comportamento no Worker:
Antes do Worker rodar qualquer disparo na Retell AI, ele executa obrigatoriamente a verificação de estado em $\approx 0.1\text{ms}$:
1.  Checa se `system:status` está marcado como `"cancelled"` no Redis.
2.  Checa se `batch:{batch_id}:status` correspondente ao lead está marcado como `"cancelled"` no Redis.
3.  Se qualquer uma das condições for verdadeira:
    *   O Worker **cancela o disparo**.
    *   Não faz a chamada HTTP para a Retell AI.
    *   Muda o status do passo do workflow para `SKIPPED` ou ignora.
4.  **Garbage Collection (Limpeza Físicas de Chaves)**:
    Após a ativação da flag, o Worker dispara de forma assíncrona um job secundário de limpeza física. Este job percorre as chaves de jobs agendados daquele lote e as deleta do Redis de forma gradativa (ex: 500 chaves por bloco com intervalo de milissegundos) para liberar memória sem sobrecarregar a CPU do Redis.

---

## 📊 6. Execuções Individuais Graduais (Evitando Duplicidade)

Para evitar duplicidade no banco e pico de gravação de 30 mil registros simultâneos:
*   Nenhum lead individual do CSV é registrado no Supabase no momento do upload.
*   Quando o tempo agendado de um lead $i$ no Redis chega, o Worker executa a rotina `schedule_execution_node` normalmente.
*   Neste exato momento, o workflow cria de forma natural seu próprio registro de execução na tabela `workflow_executions` (status `PENDING` $\to$ `RUNNING`), executa os nós do fluxo de chamada e finaliza com `SUCCESS` ou `FAILED`, preenchendo o campo `trigger_event_id` com o UUID do `batch_id` que originou o disparo.
*   Isso garante a rastreabilidade perfeita e correlação Parent-Child nativa, sem redundância e de forma extremamente escalável.
