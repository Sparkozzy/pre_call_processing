import os
import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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
    mock_table.order.return_value = mock_table
    mock_table.execute = mock_execute
    
    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    return mock_client

import database
import services
import main

if not main.WEBHOOK_API_KEY:
    main.WEBHOOK_API_KEY = "teste_key"

api_key = main.WEBHOOK_API_KEY
client = TestClient(main.app)

@pytest.fixture(autouse=True)
def setup_csv_mocks():
    # Salva originais
    orig_db = database.get_supabase_async
    orig_srv = services.get_supabase_async
    orig_main = main.get_supabase_async
    orig_redis = getattr(main.app.state, 'redis', None)
    
    # Injeta mocks
    database.get_supabase_async = mock_get_supabase_async
    services.get_supabase_async = mock_get_supabase_async
    main.get_supabase_async = mock_get_supabase_async
    
    mock_redis = MagicMock()
    mock_redis.enqueue_job = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=True)
    mock_redis.zrange = AsyncMock(return_value=[])
    
    main.app.state.redis = mock_redis
    
    yield mock_redis
    
    # Restaura originais
    database.get_supabase_async = orig_db
    services.get_supabase_async = orig_srv
    main.get_supabase_async = orig_main
    if orig_redis is not None:
        main.app.state.redis = orig_redis
    else:
        if hasattr(main.app.state, 'redis'):
            delattr(main.app.state, 'redis')

def test_csv_webhook_auth_failure():
    """Valida se requisições sem API Key válida são rejeitadas com 401."""
    # Passa um arquivo dummy para satisfazer o validador de rota do FastAPI
    csv_data = "numero,nome,email\n+5548991027108,Ryan,ryan@test.com"
    file = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    
    response = client.post(
        "/webhook/csv",
        files={"file": file},
        data={"frequencia": 60.0, "agent_id": "test_agent", "prompt_id": "22", "horario_inicio": "08:00", "horario_fim": "22:00"},
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
        data={"frequencia": 0.5, "agent_id": "test_agent", "prompt_id": "22", "horario_inicio": "08:00", "horario_fim": "22:00"},
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
        data={"frequencia": 10.0, "agent_id": "test_agent", "prompt_id": "22", "horario_inicio": "08:00", "horario_fim": "22:00"},
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
        data={"frequencia": 10.0, "agent_id": "test_agent", "prompt_id": "22", "horario_inicio": "08:00", "horario_fim": "22:00"},
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
        data={"frequencia": 10.0, "agent_id": "test_agent", "prompt_id": "22", "horario_inicio": "08:00", "horario_fim": "22:00"},
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
        data={"frequencia": 60.0, "agent_id": "test_agent", "prompt_id": "22", "contexto": "Campanha Junho", "horario_inicio": "08:00", "horario_fim": "22:00"},
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

@pytest.mark.asyncio
async def test_csv_scheduling_business_hours(setup_csv_mocks):
    """Valida se chamadas agendadas para fora do horário comercial são empurradas para as 08:00."""
    mock_redis = setup_csv_mocks
    
    import datetime
    import zoneinfo
    # Mock get_br_now to return 22:30 (forbidden time) on June 10
    forbidden_time = datetime.datetime(2026, 6, 10, 22, 30, tzinfo=zoneinfo.ZoneInfo("America/Sao_Paulo"))
    
    with patch("services.get_br_now", return_value=forbidden_time):
        ctx = {"redis": mock_redis}
        csv_data = "numero,nome,email\n+5548991027108,Ryan,ryan@test.com\n+5548991027109,Ivan,ivan@test.com"
        
        await services.ingest_csv_batch(
            ctx=ctx,
            batch_id="00000000-0000-0000-0000-000000000000",
            csv_content=csv_data,
            contexto_global="Teste",
            frequencia=1200.0, # 20 minutes
            agent_id="test_agent",
            prompt_id="22",
            horario_inicio="08:00",
            horario_fim="22:00"
        )
                
    # Verify calls enqueued
    calls = mock_redis.enqueue_job.call_args_list
    assert len(calls) >= 2
    
    defer_1 = calls[0].kwargs.get('_defer_until')
    defer_2 = calls[1].kwargs.get('_defer_until')
    
    # First lead should be 08:00:00 next day
    assert defer_1.hour == 8
    assert defer_1.minute == 0
    assert defer_1.day == 11
    
    # Second lead should be 08:20:00 next day
    assert defer_2.hour == 8
    assert defer_2.minute == 20
    assert defer_2.day == 11

@pytest.mark.asyncio
async def test_schedule_execution_node_creates_master(setup_csv_mocks):
    """Valida se o nó cria o registro mestre de execução caso não exista no banco."""
    mock_redis = setup_csv_mocks
    
    from datetime import datetime, timezone, timedelta
    
    mock_table = MagicMock()
    mock_select_res = MagicMock()
    mock_select_res.data = []
    mock_insert_res = MagicMock()
    mock_insert_res.data = [{"id": "some-execution-uuid"}]
    
    mock_table.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_select_res)
    mock_table.insert.return_value.execute = AsyncMock(return_value=mock_insert_res)
    
    mock_supabase = MagicMock()
    mock_supabase.table.return_value = mock_table
    
    with patch("services.get_supabase_async", AsyncMock(return_value=mock_supabase)):
        ctx = {"redis": mock_redis}
        execution_id = "test-execution-uuid"
        
        # Agendado para o futuro
        future_date = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        payload = {
            "workflow_name": "pre_call_processing",
            "quando_ligar": future_date
        }
        
        await services.schedule_execution_node(ctx, execution_id, payload)
    
    mock_supabase.table.assert_any_call('workflow_executions')
    mock_table.insert.assert_called()

def test_csv_webhook_invalid_times():
    """Valida se horários limites inválidos ou invertidos são rejeitados."""
    csv_data = "numero,nome,email\n+5548991027108,Ryan,ryan@test.com"
    file = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    
    # Formato inválido
    response = client.post(
        "/webhook/csv",
        files={"file": file},
        data={"frequencia": 10.0, "agent_id": "test_agent", "prompt_id": "22", "horario_inicio": "invalid", "horario_fim": "22:00"},
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 400
    assert "horario_inicio" in response.json()["detail"]

    # Horários invertidos
    csv_file_2 = ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")
    response2 = client.post(
        "/webhook/csv",
        files={"file": csv_file_2},
        data={"frequencia": 10.0, "agent_id": "test_agent", "prompt_id": "22", "horario_inicio": "18:00", "horario_fim": "09:00"},
        headers={"X-API-Key": api_key}
    )
    assert response2.status_code == 400
    assert "início deve ser menor" in response2.json()["detail"]

def test_csv_update_frequency():
    """Valida se o endpoint de alteração de frequência funciona."""
    batch_id = "00000000-0000-0000-0000-000000000000"
    with patch("main.update_batch_frequency", AsyncMock()) as mock_update:
        response = client.post(
            "/webhook/csv/update-frequency",
            json={"batch_id": batch_id, "frequencia": 15.0},
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_update.assert_called_once()

def test_csv_active_batches():
    """Valida se o endpoint que lista lotes ativos funciona."""
    response = client.get(
        "/webhook/csv/active",
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "active_batches" in response.json()

if __name__ == "__main__":
    pytest.main(["-v", __file__])
