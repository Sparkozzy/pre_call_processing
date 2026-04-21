# Workflow: Retell AI Call Creation

## Descrição Geral
Este módulo é responsável por realizar a chamada final para a API da Retell AI, iniciando a ligação telefônica para o lead processado. Ele utiliza o prompt formatado e as variáveis de contexto preparadas nos passos anteriores.

## Flowchart
```mermaid
flowchart TD
    Start[Nó Anterior: format_payload] --> RetellReq[HTTP Request: Create Phone Call]
    RetellReq --> Success[Workflow Success]
    RetellReq --> Failure[Retry / Error Logic]
```

## Detalhes dos Nós

### 1. HTTP Request (Retell AI)
- **Tipo**: `n8n-nodes-base.httpRequest`
- **Descrição**: Realiza uma requisição POST para o endpoint da Retell AI para criar uma nova chamada telefônica.
- **URL**: `https://api.retellai.com/v2/create-phone-call`
- **Headers**:
    - `Authorization`: `Bearer key_540f92099b59f815c67870d0aba3`
- **Corpo (Payload)**:
    - `from_number`: Número de origem ("iatizeia").
    - `to_number`: Número do lead (formato +55...).
    - `override_agent_id`: ID do agente Retell.
    - `retell_llm_dynamic_variables`: Dicionário contendo as variáveis injetadas (nome, prompt, contexto, etc).
- **Entradas**: `format_payload`
- **Saídas**: Finalização do Workflow (Master Status: SUCCESS)

---
*Documentação gerada automaticamente via Antigravity n8n-documenter.*
