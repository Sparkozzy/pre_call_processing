# Projeto de Migração - Integração de Webhooks e Workflows

Este projeto é um sistema de integração de webhooks e orquestração de workflows orientado a eventos (EDW - Event-Driven-Workflows), utilizando **FastAPI** para o recebimento de dados e **Supabase** para o rastreio e persistência das execuções.

## 🚀 Como Começar

### Pré-requisitos

- Python 3.9+
- Uma conta no [Supabase](https://supabase.com/) com um projeto configurado.

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
```

### Execução

Para rodar o servidor localmente:

```bash
uvicorn main:app --reload --port 8080
```

A API estará disponível em `http://localhost:8080`. Você pode testar enviando um POST para `/webhook`.

O projeto conta com scripts de teste de integração para validar o recebimento de webhooks, agendamento e rastreabilidade.

### Teste de Agendamento (APScheduler)
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


- `main.py`: Ponto de entrada da aplicação FastAPI.
- `services.py`: Lógica de negócio e orquestração dos passos do workflow.
- `database.py`: Configuração da conexão com o Supabase.
- `.agents/`: Pasta contendo configurações e skills para agentes de IA.
- `docs/`: Documentação do projeto, incluindo o guia de arquitetura para agentes (`architecture.md`).
- `tests/`: Testes automatizados.


## 🛠️ Tecnologias Utilizadas

- **FastAPI**: Framwork web moderno e rápido.
- **Supabase**: Backend-as-a-Service para banco de dados e autenticação.
- **Structlog**: Logging estruturado para monitoramento.
- **Pydantic**: Validação de dados.

---
*Este projeto foi desenvolvido seguindo padrões de engenharia de automação sênior.*
