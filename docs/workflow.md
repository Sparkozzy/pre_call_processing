# Workflow: Processamento Pré-Ligação (`pre_call_processing`)

## Objetivo
Este workflow é responsável por receber uma solicitação de chamada via webhook, buscar o prompt dinâmico associado no banco de dados, tratar os dados e preparar o envio para a Retell AI.

## Definição de Passos (Steps)

| Ordem | Step Name | O Que Faz |
| :--- | :--- | :--- |
| 1 | `pre_call_processing_webhook` | Ponto de entrada (main.py). Cria o Registro Mestre e faz validações básicas. |
| 2 | `pre_call_processing_agendamento_ram` | Verifica a data `quando_ligar`. Decide entre execução imediata ou agendamento via APScheduler. |
| 3 | `pre_call_processing_fetch_prompt` | Consulta a tabela `Prompts` no Supabase utilizando o `prompt_id` recebido. |
| 4 | `pre_call_processing_format_payload` | Limpa o texto do prompt e monta o JSON final seguindo as regras de negócio. |
| 5 | `pre_call_processing_create_retell_call` | Envia o payload formatado para a API da Retell AI para iniciar a chamada. |

## Rastreabilidade (Supabase)
O fluxo deve registrar o início na tabela `workflow_executions` e cada passo em `workflow_step_executions`.

### Input Esperado (Payload Webhook — `WebhookPayload` em `main.py`)

```json
{
  "workflow_name": "pre_call_processing",        // obrigatório (str)
  "execution_id": "UUID vindo da origem",         // obrigatório (str)
  "numero": "+5548996027108",                     // obrigatório (str, deve iniciar com '+')
  "nome": "Ryan",                                 // obrigatório (str)
  "email": "test@example.com",                    // obrigatório (EmailStr)
  "agent_id": "agent_1e4cfa23...",                // obrigatório (str) — Override Agent ID da Retell
  "Prompt_id": "24",                              // obrigatório (str) — ID ou nome do prompt
  "quando_ligar": "2026-04-21T15:00:00-03:00",   // opcional (str) — ISO 8601 com timezone
  "empresa": "MindFlow Agency",                   // opcional (str)
  "segmento": "Inteligência Artificial"           // opcional (str)
}
```

## Regras de Negócio

- O prompt deve ser limpo de caracteres especiais de Markdown.
- Datas devem ser geradas em UTC e convertidas para `America/Sao_Paulo` nos logs.
- **Rigor de Agendamento**: O campo `quando_ligar` é obrigatório para decidir o tempo de execução. Se não possuir fuso horário (Timezone Offset), o webhook deve rejeitar com erro 400.
- Se o `prompt_id` não for encontrado, o workflow deve falhar com erro descritivo.


## Server and deploy

O servidor está configurado para realizar deploy automático do código na branch main do github.

Link do repositório: https://github.com/Sparkozzy/pre_call_processing.git

Para testar workflow em produção, execute comandos como no exemplo de requisição.

### Exemplo de requisição:

``` python
url = "https://call-github.bkpxmb.easypanel.host/webhook"
payload = {
    "workflow_name": "pre_call_processing",
    "execution_id": "test-execution-antigravity-python",
    "numero": "+5548996027108",
    "nome": "Ryan",
    "email": "test@example.com",
    "agent_id": "agent_1e4cfa23e3910c557d82167949",
    "Prompt_id": "24",
    "empresa": "MindFlow Agency",
    "segmento": "Inteligência Artificial",
    "quando_ligar": "2026-04-20T15:00:00-03:00"
}
```