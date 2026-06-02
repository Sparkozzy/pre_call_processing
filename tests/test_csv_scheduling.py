import os
import io
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

# 1. Mock do Supabase Client para isolamento completo em testes
async def mock_get_supabase_async():
    # Resposta que o execute final retornará após ser awaitado
    mock_response = MagicMock()
    mock_response.data = [{"id": "mocked-uuid-lote"}]
    
    # O método execute() é um AsyncMock que retorna a resposta
    mock_execute = AsyncMock(return_value=mock_response)
    
    # O mock do construtor de queries suporta encadeamento fluido
    mock_table = MagicMock()
    mock_table.insert.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.execute = mock_execute
    
    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    return mock_client

# Injeta mocks nos módulos antes de importar o app
import database
database.get_supabase_async = mock_get_supabase_async

import services
services.get_supabase_async = mock_get_supabase_async

import main
if not main.WEBHOOK_API_KEY:
    main.WEBHOOK_API_KEY = "teste_key"

# 2. Configura o TestClient e mocka a conexão de lifespan do Redis
api_key = main.WEBHOOK_API_KEY
client = TestClient(main.app)

# Injeta mock do Redis no app state após inicialização para os endpoints síncronos
main.app.state.redis = MagicMock()
main.app.state.redis.enqueue_job = AsyncMock()
main.app.state.redis.get = AsyncMock(return_value=None)
main.app.state.redis.set = AsyncMock()

def test_csv_webhook_auth_failure():
    """Valida se requisições sem API Key válida são rejeitadas com 401."""
    # Passa um arquivo dummy para satisfazer o validador de rota do FastAPI
    csv_data = "numero,nome,email\n+5548991027108,Ryan,ryan@test.com"
    file = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    
    response = client.post(
        "/webhook/csv",
        files={"file": file},
        data={"frequencia": 60.0, "agent_id": "test_agent", "prompt_id": "22"},
        headers={"X-API-Key": "INVALID_KEY"}
    )
    assert response.status_code == 401
    assert "API Key inválida" in response.json()["detail"]

def test_csv_webhook_invalid_params():
    """Valida se parâmetros de formulário inválidos (frequência < 1) são rejeitados."""
    csv_data = "numero,nome,email\n+5548991027108,Ryan,ryan@test.com"
    file = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    
    response = client.post(
        "/webhook/csv",
        files={"file": file},
        data={"frequencia": 0.5, "agent_id": "test_agent", "prompt_id": "22"},
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 400
    assert "frequência não pode ser nula e nem menor que 1" in response.json()["detail"]

def test_csv_webhook_missing_header():
    """Valida se o CSV sem coluna obrigatória é rejeitado síncronamente."""
    # CSV sem a coluna 'numero'
    csv_data = "phone,nome,email\n+5548991027108,Ryan,ryan@test.com"
    file = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    
    response = client.post(
        "/webhook/csv",
        files={"file": file},
        data={"frequencia": 10.0, "agent_id": "test_agent", "prompt_id": "22"},
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 400
    assert "Coluna obrigatória 'numero' ausente" in response.json()["detail"]

def test_csv_webhook_missing_required_value():
    """Valida se uma linha com o campo 'numero' vazio é rejeitada."""
    # Linha 2 tem número em branco
    csv_data = "numero,nome,email\n,Ryan,ryan@test.com"
    file = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    
    response = client.post(
        "/webhook/csv",
        files={"file": file},
        data={"frequencia": 10.0, "agent_id": "test_agent", "prompt_id": "22"},
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 400
    assert "O campo 'numero' não pode ser vazio" in response.json()["detail"]

def test_csv_webhook_missing_plus_sign():
    """Valida se um número sem o sinal de '+' é rejeitado síncronamente."""
    # Número iniciando em 55 sem o '+'
    csv_data = "numero,nome,email\n5548991027108,Ryan,ryan@test.com"
    file = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    
    response = client.post(
        "/webhook/csv",
        files={"file": file},
        data={"frequencia": 10.0, "agent_id": "test_agent", "prompt_id": "22"},
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 400
    assert "deve iniciar com '+'" in response.json()["detail"]

def test_csv_webhook_success_with_email_fallback():
    """Valida se o CSV correto com e-mail em branco é aceito e processado."""
    # Email em branco na segunda linha, email "." na terceira
    csv_data = (
        "numero,nome,email,cidade,valor\n"
        "+5548991027108,Ryan Lead,,\n"
        "+5548991027109,Ivan Lead,.,Florianopolis,1500"
    )
    file = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    
    response = client.post(
        "/webhook/csv",
        files={"file": file},
        data={"frequencia": 60.0, "agent_id": "test_agent", "prompt_id": "22", "contexto": "Campanha Junho"},
        headers={"X-API-Key": api_key}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "batch_id" in data
    assert data["total_leads"] == 2
    
    # Retorna o ID gerado para teste de cancelamento subsequente
    return data["batch_id"]

def test_csv_cancel_lote():
    """Valida se o endpoint de cancelamento para lote específico funciona."""
    batch_id = "00000000-0000-0000-0000-000000000000"
    response = client.post(
        "/webhook/csv/cancel",
        data={"batch_id": batch_id},
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["batch_id"] == batch_id

def test_csv_cancel_global():
    """Valida se o cancelamento global (Panic Button) funciona."""
    response = client.post(
        "/webhook/csv/cancel",
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "Panic Button ativado" in response.json()["message"]

if __name__ == "__main__":
    pytest.main(["-v", __file__])
