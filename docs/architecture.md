# Project Architecture - AI Agent Guide

Este documento serve como guia técnico para agentes de IA que operam neste repositório. Ele detalha a estrutura, o fluxo de dados e os padrões de design adotados.

## 🏛️ Visão Geral

O projeto é um sistema **Event-Driven Workflow (EDW)** focado na migração e integração de processos de automação. Ele utiliza uma arquitetura baseada em microsserviços leves com FastAPI e delegação de estado para o Supabase.

## 📂 Estrutura de Repositório

```text
Migracao/
├── .agents/                 # Inteligência do Agente
│   ├── Skills/              # Skills específicas (n8n, LangGraph, MCP, etc.)
│   ├── workflows/           # Definições de fluxos de agentes
│   └── System_prompt.md     # Persona e regras de comportamento do agente
├── docs/                    # Documentação técnica
│   ├── architecture.md      # Guia para agentes (este arquivo)
│   └── conventions.md       # Convenções de código e padrões EDW
├── notebooks/               # Exploração de dados e prototipagem
├── tests/                   # Testes unitários e de integração
├── Skill_ideas/             # Backlog de ideias para novas skills
├── main.py                  # Entrypoint: API FastAPI
├── services.py              # Core Logic: Orquestração e Steps
├── database.py              # Camada de Dados: Cliente Supabase
├── requirements.txt         # Dependências do projeto
├── .env                     # Variáveis de ambiente (IGNORAR NO GIT)
└── README.md                # Guia para humanos. MANTENHA ATUALIZADO
```

## 🗄️ Estrutura de Banco de Dados (Mestre-Detalhe)

A arquitetura utiliza o padrão Parent-Child para garantir observabilidade total e rastreabilidade de eventos.

### Tabela 1: `workflow_executions` (Mestre)
Armazena o contexto global e o estado consolidado.
- `id` (uuid, PK)
- `workflow_name` (varchar) - Ex: `envia_ligacao`
- `trigger_event_id` (varchar) - ID de correlação original.
- `status` (varchar) - PENDING, RUNNING, SUCCESS, FAILED.
- `input_data` (jsonb) - Payload inicial.
- `output_data` (jsonb) - Resumo ou dado final.
- `error_details` (text) - Razão da falha global.
- `started_at` (timestamptz) - Início (UTC ISO 8601).
- `completed_at` (timestamptz) - Finalização.
- `created_at` (timestamptz) - Criação do registro.

### Tabela 2: `workflow_step_executions` (Detalhe)
Log imutável de cada tentativa e etapa executada.
- `id` (uuid, PK)
- `execution_id` (uuid, FK) - Referência ao Mestre.
- `step_name` (varchar) - Padrão `{{workflow}}_{{OQF}}`.
- `status` (varchar) - SUCCESS, FAILED, SKIPPED.
- `attempt` (integer) - Contador de retentativas.
- `input_data` (jsonb)
- `output_data` (jsonb)
- `error_details` (text)
- `started_at` (timestamptz) - ISO UTC.
- `completed_at` (timestamptz)

## ⚙️ Ciclo de Vida do Workflow

1. **Inicialização**: Cria registro em `workflow_executions`.
2. **Execução de Nós**: Cada nó é a mínima ação rastreável.
3. **Gestão de Retries**: Falhas em etas resultam em novos registros em `workflow_step_executions` com `attempt` incrementado.
4. **Finalização**: Atualização do status final no Mestre.

## 🛠️ Regras Críticas de Implementação

- **Datas**: Persistir sempre em UTC (Z), mas processar em fuso `America/Sao_Paulo`.
- **Rastrabilidade**: Passar `workflow_id`, `from_workflow` e `execution_id` entre fluxos que se comunicam.
- **Naming**: Seguir rigorosamente o padrão `workflow_name` e `workflow_name_step_name`.

---
*Este documento deve ser consultado antes de qualquer refatoração.*

