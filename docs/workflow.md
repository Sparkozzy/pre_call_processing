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
| 5 | `pre_call_processing_create_retell_call` | **BLOQUEADO:** Envia o comando para a Retell AI. |

## Rastreabilidade (Supabase)
O fluxo deve registrar o início na tabela `workflow_executions` e cada passo em `workflow_step_executions`.

### Input Esperado (Payload Webhook)
{
  "workflow_name": "pre_call_processing",
  "execution_id": "string (UUID vindo da origem)",
  "prompt_id": "string",
  "numero": "string (ex: +55...)",
  "nome": "string",
  "email": "string",
  "quando_ligar": "string (ISO 8601 com timezone, ex: 2026-04-17T12:00:00-03:00)"
}
```

## Regras de Negócio
- O prompt deve ser limpo de caracteres especiais de Markdown.
- Datas devem ser geradas em UTC e convertidas para `America/Sao_Paulo` nos logs.
- **Rigor de Agendamento**: O campo `quando_ligar` é obrigatório para decidir o tempo de execução. Se não possuir fuso horário (Timezone Offset), o webhook deve rejeitar com erro 400.
- Se o `prompt_id` não for encontrado, o workflow deve falhar com erro descritivo.
