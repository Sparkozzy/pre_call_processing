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
    "segmento": "SaaS"
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

## Rastreabilidade

Após receber o `execution_db_id` na resposta `202`, é possível acompanhar o ciclo de vida da execução consultando o Supabase:

### Status do Workflow (tabela `workflow_executions`)

| Status | Significado |
| :--- | :--- |
| `PENDING` | Payload aceito, aguardando processamento ou agendamento. |
| `RUNNING` | Worker iniciou a execução dos nós (fetch_prompt → format → retell_call). |
| `SUCCESS` | Ligação criada com sucesso na Retell AI. O campo `output_data` contém o `call_id`. |
| `FAILED` | Falha em algum nó após todas as tentativas de retry. O campo `error_details` contém a razão. |

### Passos Executados (tabela `workflow_step_executions`)

Cada nó gera um registro imutável com `execution_id` apontando para o registro mestre:

| Step Name | Descrição |
| :--- | :--- |
| `pre_call_processing_agendamento_redis` | Decisão de execução imediata vs agendamento futuro. |
| `pre_call_processing_fetch_prompt` | Busca do prompt dinâmico no Supabase. |
| `pre_call_processing_format_payload` | Substituição de variáveis e limpeza de Markdown. |
| `pre_call_processing_create_retell_call` | Chamada à API da Retell AI para disparar a ligação. |

---

> [!TIP]
> A documentação interativa (Swagger UI) está disponível em: `https://call-github.bkpxmb.easypanel.host/docs`
