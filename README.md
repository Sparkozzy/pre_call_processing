# Projeto de Migração - Integração de Webhooks e Workflows

Este projeto é um sistema de integração de webhooks e orquestração de workflows orientado a eventos (EDW - Event-Driven-Workflows), utilizando **FastAPI** para o recebimento de dados, **APScheduler** para agendamento de execuções, e **Supabase** para o rastreio e persistência das execuções.

O workflow ativo (`pre_call_processing`) automatiza o disparo de chamadas telefônicas via **Retell AI**, desde o recebimento do webhook até a criação da ligação.

## 🚀 Como Começar

### Pré-requisitos

- Python 3.9+
- Uma conta no [Supabase](https://supabase.com/) com um projeto configurado.
- Uma API Key da [Retell AI](https://www.retellai.com/).

### Instalação

1. Clone o repositório.
2. Crie um ambiente virtual:
   ```bash
   python -m venv venv
   ```
3. Ative o ambiente:
   - **No Windows (PowerShell):**
     ```powershell
     .\venv\Scripts\Activate.ps1
     ```
   - **No Windows (Promp de Comando):**
     ```cmd
     venv\Scripts\activate
     ```
   - **No Linux/macOS:**
     ```bash
     source venv/bin/activate
     ```
4. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

### Configuração

Crie um arquivo `.env` na raiz do projeto com as seguintes chaves:

```env
SUPABASE_URL=sua_url_do_supabase
SUPABASE_KEY=sua_chave_anon_ou_service_role
RETELL_API_KEY=sua_api_key_retell
RETELL_FROM_NUMBER=numero_de_origem  # Opcional (default: iatizeia)
```

### Execução

Para rodar o servidor localmente:

```bash
uvicorn main:app --reload --port 8080
```

A API estará disponível em `http://localhost:8080`. Você pode testar enviando um POST para `/webhook`.

### Endpoint de Produção

O servidor de produção é deployado automaticamente via push na branch `main`:

```
POST https://call-github.bkpxmb.easypanel.host/webhook
```

### Exemplo de Requisição

```python
import requests

url = "http://localhost:8080/webhook"  # ou URL de produção
payload = {
    "workflow_name": "pre_call_processing",
    "execution_id": "test-execution-001",
    "numero": "+5548996027108",
    "nome": "Ryan",
    "email": "test@example.com",
    "agent_id": "agent_1e4cfa23e3910c557d82167949",
    "Prompt_id": "24",
    "empresa": "MindFlow Agency",
    "segmento": "Inteligência Artificial",
    "quando_ligar": "2026-04-21T15:00:00-03:00"
}

response = requests.post(url, json=payload)
print(response.json())
```

### Testes

#### Teste de Agendamento (APScheduler)
Certifique-se de que o servidor está rodando e execute:

```bash
$env:PYTHONPATH="."; python tests/test_scheduling_flow.py
```

Isso validará:
1. Recebimento do Webhook.
2. Registro Mestre no Supabase.
3. Agendamento em RAM.
4. Execução futura automática com troca de status para SUCCESS.

## 📂 Estrutura do Projeto

- `main.py`: Ponto de entrada da API FastAPI (webhook, validação, registro mestre).
- `services.py`: Lógica de negócio, agendamento (APScheduler), orquestração dos nós e integração Retell AI.
- `database.py`: Configuração da conexão singleton com o Supabase.
- `.agents/`: Configurações e skills para agentes de IA.
- `docs/`: Documentação técnica (`architecture.md`, `conventions.md`, `workflow.md`).
- `tests/`: Testes automatizados.

## 🛠️ Tecnologias Utilizadas

- **FastAPI**: Framework web moderno e rápido (ASGI).
- **Supabase**: Backend-as-a-Service para banco de dados e persistência.
- **APScheduler**: Agendamento de tarefas em background (RAM).
- **Pydantic**: Validação de dados e schemas de entrada.
- **Requests**: Chamadas HTTP para a API da Retell AI.
- **python-dotenv**: Carregamento de variáveis de ambiente.

---
*Este projeto foi desenvolvido seguindo padrões de engenharia de automação sênior (EDW - MindFlow).*
