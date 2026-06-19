# API Reference — `pre_call_processing`

Documentação de referência para desenvolvedores que consomem o workflow `pre_call_processing` exclusivamente via API.

---

## Endpoint

| Propriedade | Valor |
| :--- | :--- |
| **URL** | `https://call-github.bkpxmb.easypanel.host/webhook` |
| **Método** | `POST` |
| **Content-Type** | `application/json` |
| **Autenticação** | Header `X-API-Key` obrigatório |
| **Resposta de sucesso** | `202 Accepted` |

---

## Payload (Request Body)

```json
{
  "workflow_name": "pre_call_processing",
  "execution_id": "meu-sistema-origem-uuid-1234",
  "numero": "+5548996027108",
  "nome": "Ryan",
  "email": "ryan@empresa.com",
  "agent_id": "agent_1e4cfa23e3910c557d82167949",
  "Prompt_id": "24",
  "quando_ligar": "2026-04-21T15:00:00-03:00",
  "empresa": "MindFlow Agency",
  "segmento": "Inteligência Artificial"
}
```

### Campos Obrigatórios

| Campo | Tipo | Descrição |
| :--- | :---: | :--- |
| `workflow_name` | `string` | **Sempre** `"pre_call_processing"`. Identifica o workflow a ser executado. Qualquer outro valor resultará em comportamento inesperado. |
| `execution_id` | `string` | ID de rastreabilidade da execução. Deve ser fornecido pelo sistema de origem que ativou a chamada. Permite rastrear execuções cross-workflow, já que múltiplas fontes (CRMs, bots, automações) podem disparar este endpoint. Use um UUID ou identificador único do seu sistema para correlacionar logs de ponta a ponta. |
| `numero` | `string` | Número de telefone do destinatário. **Deve** iniciar com `+`. Formatos aceitos (exigência da Retell AI): |
| | | • Celular: `+55DD9XXXXXXXX` (13 dígitos após o `+`) |
| | | • Fixo: `+55DDXXXXXXXX` (12 dígitos após o `+`) |
| | | Onde `DD` = DDD (código de área com 2 dígitos). |
| `nome` | `string` | **Apenas o primeiro nome** do destinatário. Este valor será injetado no prompt dinâmico e utilizado diretamente na ligação via TTS (Text-to-Speech). |
| `email` | `string` | E-mail válido do destinatário. Validado como `EmailStr` pelo Pydantic — endereços inválidos retornam `422`. |
| `agent_id` | `string` | ID do agente configurado na plataforma Retell AI. Este campo faz override do agente padrão. Exemplo: `"agent_1e4cfa23e3910c557d82167949"`. |
| `Prompt_id` | `string` | ID do prompt armazenado na tabela `Prompts` do Supabase. Pode ser numérico (ex: `"24"`) para busca por `id`, ou um nome textual (ex: `"onboarding_call"`) para busca por `Pormpt_Name`. Se o ID não for encontrado, o workflow falhará com erro descritivo. |

> [!IMPORTANT]
> O campo `Prompt_id` é case-sensitive e utiliza `P` maiúsculo — exatamente `Prompt_id`, **não** `prompt_id`.

### Campos Opcionais

| Campo | Tipo | Default | Descrição |
| :--- | :---: | :---: | :--- |
| `quando_ligar` | `string` | `null` | Data/hora para agendar a ligação. Veja seção [Agendamento](#agendamento-quando_ligar) abaixo. Se omitido ou `null`, a ligação é disparada **imediatamente**. |
| `empresa` | `string` | `null` | Nome da empresa do destinatário. Injetado como variável de contexto no prompt dinâmico. |
| `segmento` | `string` | `null` | Segmento de atuação da empresa. Injetado como variável de contexto no prompt dinâmico. |
| `from_number` | `string` | `iatizeia` | Número remetente da ligação (ex: número específico para WhatsApp). Se omitido, utiliza o padrão configurado. |

---

## Agendamento (`quando_ligar`)

O campo `quando_ligar` controla **quando** a ligação será realizada.

### Formato

- **Padrão**: ISO 8601 com timezone offset obrigatório.
- **Fuso horário**: O offset é **obrigatório**. Payloads sem fuso são rejeitados com `400 Bad Request`.

| Formato | Exemplo | Válido? |
| :--- | :--- | :---: |
| ISO 8601 com offset Brasília | `2026-04-21T15:00:00-03:00` | ✅ |
| ISO 8601 com UTC (Z) | `2026-04-21T18:00:00Z` | ✅ |
| ISO 8601 com outro offset | `2026-04-21T14:00:00-04:00` | ✅ |
| ISO 8601 **sem** offset | `2026-04-21T15:00:00` | ❌ `400` |
| Outros formatos (DD/MM/YYYY) | `21/04/2026 15:00` | ❌ `422` |

> [!WARNING]
> O servidor opera internamente no fuso `America/Sao_Paulo` (UTC-3 ou UTC-2 durante horário de verão). Se você enviar `"2026-04-21T15:00:00-03:00"`, a ligação será feita às **15:00 no horário de Brasília**. Se enviar `"2026-04-21T15:00:00Z"`, será às **12:00 no horário de Brasília** (15:00 UTC = 12:00 BRT).

### Comportamento

| Cenário | Comportamento |
| :--- | :--- |
| `quando_ligar` ausente ou `null` | Execução **imediata** |
| Data no **passado** ou **agora** | Execução **imediata** |
| Data no **futuro** | Agendamento persistente via Redis. A ligação será disparada automaticamente no horário especificado. |

---

## Resposta

### `202 Accepted` — Sucesso

```json
{
  "status": "success",
  "message": "Webhook aceito, registro mestre criado e delegado para a fila persistente.",
  "execution_db_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

| Campo | Descrição |
| :--- | :--- |
| `status` | Sempre `"success"` quando aceito. |
| `message` | Confirmação textual de recebimento. |
| `execution_db_id` | UUID do registro mestre criado no Supabase (`workflow_executions`). Utilize este ID para rastrear o status da execução. |

> [!NOTE]
> O `202 Accepted` indica que o payload foi validado e enfileirado para processamento assíncrono. **Não** significa que a ligação já foi realizada. O workflow será executado em background pelo worker.

### Erros

| Status | Causa | Exemplo de `detail` |
| :---: | :--- | :--- |
| `401` | API Key inválida ou ausente | `"API Key inválida ou ausente. Verifique o header 'X-API-Key'."` |
| `400` | `numero` não inicia com `+` | `"O número de telefone deve começar com o sinal de '+'"` |
| `400` | `quando_ligar` sem timezone | `"O campo 'quando_ligar' deve conter fuso horário válido (Ex: -03:00 ou Z)."` |
| `422` | Campos obrigatórios ausentes ou tipos inválidos (ex: email inválido) | Erro de validação Pydantic (automático) |
| `500` | Falha ao criar registro no Supabase | `"Erro ao registrar workflow no banco de dados."` |

---

## Exemplos de Integração

### cURL

```bash
curl -X POST https://call-github.bkpxmb.easypanel.host/webhook \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sua-chave-api-aqui" \
  -d '{
    "workflow_name": "pre_call_processing",
    "execution_id": "crm-lead-capture-uuid-5678",
    "numero": "+5511999887766",
    "nome": "Maria",
    "email": "maria@empresa.com",
    "agent_id": "agent_1e4cfa23e3910c557d82167949",
    "Prompt_id": "24",
    "quando_ligar": "2026-04-21T14:30:00-03:00",
    "empresa": "TechCorp",
    "segmento": "SaaS",
    "from_number": "+555196506656"
  }'
```

### Python (httpx)

```python
import httpx

url = "https://call-github.bkpxmb.easypanel.host/webhook"
payload = {
    "workflow_name": "pre_call_processing",
    "execution_id": "crm-lead-capture-uuid-5678",
    "numero": "+5511999887766",
    "nome": "Maria",
    "email": "maria@empresa.com",
    "agent_id": "agent_1e4cfa23e3910c557d82167949",
    "Prompt_id": "24",
    "quando_ligar": "2026-04-21T14:30:00-03:00",
    "empresa": "TechCorp",
    "segmento": "SaaS"
}

response = httpx.post(url, json=payload)
print(response.status_code)  # 202
print(response.json())       # {"status": "success", "message": "...", "execution_db_id": "..."}
```

### Python (requests)

```python
import requests

url = "https://call-github.bkpxmb.easypanel.host/webhook"
payload = {
    "workflow_name": "pre_call_processing",
    "execution_id": "n8n-workflow-abc-uuid-9012",
    "numero": "+5521988776655",
    "nome": "João",
    "email": "joao@startup.io",
    "agent_id": "agent_1e4cfa23e3910c557d82167949",
    "Prompt_id": "24"
}

response = requests.post(url, json=payload)
print(response.status_code)  # 202
```

### JavaScript (fetch)

```javascript
const response = await fetch("https://call-github.bkpxmb.easypanel.host/webhook", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    workflow_name: "pre_call_processing",
    execution_id: "frontend-form-uuid-3456",
    numero: "+5548996027108",
    nome: "Ryan",
    email: "ryan@mindflow.com",
    agent_id: "agent_1e4cfa23e3910c557d82167949",
    Prompt_id: "24",
    quando_ligar: "2026-04-21T15:00:00-03:00",
    empresa: "MindFlow Agency",
    segmento: "Inteligência Artificial"
  })
});

const data = await response.json();
console.log(data.execution_db_id); // UUID para rastreamento
```

---

## Agendamento em Lote via CSV (Bulk Scheduling)

Permite o agendamento em massa de ligações disparadas a partir de um arquivo CSV, com distribuição temporal controlada por frequência e suporte a cancelamento imediato de chamadas programadas.

### Endpoint: `/webhook/csv`

| Propriedade | Valor |
| :--- | :--- |
| **URL** | `https://call-github.bkpxmb.easypanel.host/webhook/csv` |
| **Método** | `POST` |
| **Content-Type** | `multipart/form-data` |
| **Autenticação** | Header `X-API-Key` obrigatório |
| **Resposta de sucesso** | `200 OK` |

#### Parâmetros do Formulário (Form-Data)

| Campo | Tipo | Obrigatório | Descrição |
| :--- | :---: | :---: | :--- |
| `file` | `file` | ✅ | Arquivo `.csv` contendo as colunas obrigatórias no cabeçalho: `numero`, `nome` e `email`. |
| `horario_inicio` | `string` | ✅ | Horário de início do expediente para as ligações no formato `HH:MM` (ex: `08:00`). |
| `horario_fim` | `string` | ✅ | Horário de término do expediente para as ligações no formato `HH:MM` (ex: `22:00`). |
| `frequencia` | `float` | ✅ | Intervalo em segundos entre cada disparo ($\ge 1$). Ex: `60` agendará ligações a cada 1 minuto. |
| `agent_id` | `string` | ✅ | ID do agente configurado na plataforma Retell AI. |
| `prompt_id` | `string` | ✅ | ID do prompt no Supabase (numérico ou textual). |
| `contexto` | `string` | ❌ | Contexto base global da chamada. |

#### Regras do Arquivo CSV e Validação
1.  **Cabeçalho**: A primeira linha do CSV deve conter obrigatoriamente as colunas `numero`, `nome` e `email` (independente da ordem).
2.  **Valores Obrigatórios**: As colunas `numero` e `nome` não podem conter valores vazios. O campo `numero` deve obrigatoriamente começar com `+` (DDI do país).
3.  **Fallback de E-mail**: Se o e-mail estiver em branco em alguma linha, a API o aceita automaticamente e o converte para o valor `"."` (um único ponto) para evitar falhas no Pydantic.
4.  **Colunas Adicionais**: Quaisquer outras colunas incluídas no CSV (ex: `cidade`, `valor_divida`) são mapeadas automaticamente e concatenadas de forma dinâmica ao final do `contexto` individual daquele lead no formato `"nome: valor; "`.
5.  **Distribuição Temporal Indexada**: Os disparos são agendados sequencialmente. O lead do index $i$ (iniciando em 0) é agendado com atraso de $i \times frequencia$ segundos no fuso de Brasília (`America/Sao_Paulo`), respeitando a janela de funcionamento definida em `horario_inicio` e `horario_fim` (ligações fora deste horário são adiadas para o início do expediente seguinte).

#### Resposta de Sucesso (`200 OK`)
```json
{
  "status": "success",
  "message": "Arquivo CSV validado com sucesso e enfileirado para processamento assíncrono.",
  "batch_id": "8939723b-2a96-462d-a048-33b0e60b0478",
  "total_leads": 12500
}
```

---

### Endpoint: `/webhook/csv/cancel` (Kill Switch)

Permite suspender de imediato disparos agendados no Redis que ainda não foram efetuados, evitando picos de custos ou erros de envio.

| Propriedade | Valor |
| :--- | :--- |
| **URL** | `https://call-github.bkpxmb.easypanel.host/webhook/csv/cancel` |
| **Método** | `POST` |
| **Content-Type** | `application/x-www-form-urlencoded` ou `multipart/form-data` |
| **Autenticação** | Header `X-API-Key` obrigatório |
| **Resposta de sucesso** | `200 OK` |

#### Parâmetros do Formulário (Form-Data / URL-Encoded)

| Campo | Tipo | Obrigatório | Descrição |
| :--- | :---: | :---: | :--- |
| `batch_id` | `string` | ❌ | UUID do lote gerado pelo endpoint `/webhook/csv`. Se omitido, aciona o **Panic Button** e cancela **TODOS** os lotes ativos no sistema. |

#### Resposta de Sucesso (`200 OK`)
```json
{
  "status": "success",
  "message": "Interrupção do lote 8939723b-2a96-462d-a048-33b0e60b0478 ativada. Novos disparos foram bloqueados com sucesso.",
  "batch_id": "8939723b-2a96-462d-a048-33b0e60b0478"
}
```

---

### Endpoint: `/webhook/csv/update-frequency`

Permite alterar dinamicamente a frequência (intervalo) de disparos de um lote que já está em andamento na fila do Redis.

| Propriedade | Valor |
| :--- | :--- |
| **URL** | `https://call-github.bkpxmb.easypanel.host/webhook/csv/update-frequency` |
| **Método** | `POST` |
| **Content-Type** | `application/json` |
| **Autenticação** | Header `X-API-Key` obrigatório |
| **Resposta de sucesso** | `200 OK` |

#### Corpo da Requisição (JSON Payload)

| Campo | Tipo | Obrigatório | Descrição |
| :--- | :---: | :---: | :--- |
| `batch_id` | `string` | ✅ | UUID do lote a ser atualizado. |
| `frequencia` | `float` | ✅ | Novo intervalo em segundos entre cada disparo ($\ge 1$). |

#### Resposta de Sucesso (`200 OK`)
```json
{
  "status": "success",
  "message": "Frequência do lote 8939723b-2a96-462d-a048-33b0e60b0478 atualizada para 15.0s com sucesso."
}
```

---

### Endpoint: `/webhook/csv/active`

Permite consultar quais lotes (batches) de CSV estão ativos no momento (status `PENDING` ou `RUNNING` no Supabase) e a quantidade de disparos que ainda restam pendentes na fila do Redis.

| Propriedade | Valor |
| :--- | :--- |
| **URL** | `https://call-github.bkpxmb.easypanel.host/webhook/csv/active` |
| **Método** | `GET` |
| **Autenticação** | Header `X-API-Key` obrigatório |
| **Resposta de sucesso** | `200 OK` |

#### Resposta de Sucesso (`200 OK`)
```json
{
  "status": "success",
  "active_batches": [
    {
      "batch_id": "8939723b-2a96-462d-a048-33b0e60b0478",
      "status_supabase": "RUNNING",
      "started_at": "2026-06-19T14:30:00+00:00",
      "config": {
        "frequencia": 60.0,
        "horario_inicio": "08:00",
        "horario_fim": "22:00",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "prompt_id": "24",
        "file_name": "leads_campanha.csv",
        "total_leads_csv": 500
      },
      "leads_pendentes_fila": 125
    }
  ]
}
```

---

## Exemplos de Integração em Lote (CSV)

### 1. Upload de CSV via cURL
```bash
curl -X POST https://call-github.bkpxmb.easypanel.host/webhook/csv \
  -H "X-API-Key: sua-chave-api-aqui" \
  -F "file=@/caminho/do/meu/arquivo.csv" \
  -F "horario_inicio=08:00" \
  -F "horario_fim=18:00" \
  -F "frequencia=60" \
  -F "agent_id=agent_1e4cfa23e3910c557d82167949" \
  -F "prompt_id=24" \
  -F "contexto=Campanha de Reengajamento"
```

### 2. Cancelamento de Lote via cURL
```bash
curl -X POST https://call-github.bkpxmb.easypanel.host/webhook/csv/cancel \
  -H "X-API-Key: sua-chave-api-aqui" \
  -F "batch_id=8939723b-2a96-462d-a048-33b0e60b0478"
```

### 3. Alteração Dinâmica de Frequência via cURL
```bash
curl -X POST https://call-github.bkpxmb.easypanel.host/webhook/csv/update-frequency \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sua-chave-api-aqui" \
  -d '{
    "batch_id": "8939723b-2a96-462d-a048-33b0e60b0478",
    "frequencia": 15.0
  }'
```

### 4. Consulta de Lotes Ativos via cURL
```bash
curl -X GET https://call-github.bkpxmb.easypanel.host/webhook/csv/active \
  -H "X-API-Key: sua-chave-api-aqui"
```

### 5. Ingestão de CSV em Python (httpx)
```python
import httpx

url = "https://call-github.bkpxmb.easypanel.host/webhook/csv"
headers = {"X-API-Key": "sua-chave-api-aqui"}

files = {"file": ("leads.csv", open("leads.csv", "rb"), "text/csv")}
data = {
    "horario_inicio": "08:30",
    "horario_fim": "17:30",
    "frequencia": 30.0,
    "agent_id": "agent_1e4cfa23e3910c557d82167949",
    "prompt_id": "24",
    "contexto": "Lote Promocional Junho"
}

with httpx.Client() as client:
    response = client.post(url, headers=headers, files=files, data=data)
    print(response.status_code)  # 200
    print(response.json())       # {"status": "success", "batch_id": "..."}
```

---

## Rastreabilidade

Após receber a confirmação de sucesso, é possível acompanhar todo o ciclo de vida e passos executados no Supabase de forma nativa:

### Status de Execução (tabela `workflow_executions`)

O lote do CSV é mapeado como uma execução sob o nome de `"csv_scheduling"`, onde o `id` é o `batch_id`:

| Workflow Name | Status | Significado |
| :--- | :--- | :--- |
| `csv_scheduling` | `RUNNING` | Arquivo CSV validado. Worker está processando o enfileiramento das ligações em chunks. |
| `csv_scheduling` | `SUCCESS` | Ingestão concluída com sucesso. Todos os leads foram agendados no Redis. |
| `csv_scheduling` | `FAILED` | Falha ao processar o CSV ou lote interrompido/cancelado pelo usuário. |
| `pre_call_processing` | `RUNNING` | A ligação de um lead individual do lote alcançou sua hora de disparo e foi iniciada. |
| `pre_call_processing` | `SUCCESS` | A chamada individual do lote foi completada (contém `trigger_event_id` = `batch_id`). |

### Passos de Execução (tabela `workflow_step_executions`)

Registra o progresso de leitura e ingestão em background do lote:

| Step Name | Descrição |
| :--- | :--- |
| `csv_scheduling_ingestion` | Leitura assíncrona do CSV em chunks de 2.000 linhas, cálculo do delay indexado e enfileiramento das tarefas no Redis. |
| `pre_call_processing_agendamento_redis` | Verificação do Kill Switch rápido (Redis flag) no instante do disparo e prosseguimento do nó. |

---

> [!TIP]
> A documentação interativa (Swagger UI) com suporte a testes de endpoints está disponível em: `https://call-github.bkpxmb.easypanel.host/docs`
