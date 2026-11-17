import pytest
import os
import uuid
import httpx
from unittest.mock import AsyncMock, MagicMock

import main
import database

# Se WEBHOOK_API_KEY não estiver no ambiente local, define para teste
if not main.WEBHOOK_API_KEY:
    main.WEBHOOK_API_KEY = "mf_sk_2026_pre_call_master_test"

@pytest.fixture(autouse=True)
def restore_redis_state():
    orig_redis = getattr(main.app.state, 'redis', None)
    mock_redis = MagicMock()
    mock_redis.enqueue_job = AsyncMock(return_value=MagicMock())
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.close = AsyncMock()
    main.app.state.redis = mock_redis
    yield
    if orig_redis is not None:
        main.app.state.redis = orig_redis
    elif hasattr(main.app.state, 'redis'):
        delattr(main.app.state, 'redis')

@pytest.mark.anyio
async def test_auth_rejection_invalid_key():
    """Chave inválida ou ausente deve retornar 401 Unauthorized"""
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        # Sem header
        r1 = await ac.post("/webhook", json={"numero": "+5511999998888"})
        assert r1.status_code in (401, 422)

        # Chave inexistente
        r2 = await ac.post("/webhook", headers={"X-API-Key": "chave_inexistente_123"}, json={
            "workflow_name": "test",
            "execution_id": "exec_1",
            "numero": "+5511999998888",
            "nome": "Test",
            "email": "test@test.com",
            "Prompt_id": "33"
        })
        assert r2.status_code == 401
        assert "API Key inválida ou revogada" in r2.json()["detail"]

@pytest.mark.anyio
async def test_auth_success_master_key():
    """Chave master deve autenticar normalmente"""
    database._supabase_async_client = None
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/webhook", headers={"X-API-Key": main.WEBHOOK_API_KEY}, json={
            "workflow_name": "test_master",
            "execution_id": f"exec_{uuid.uuid4().hex[:8]}",
            "numero": "+5511999998888",
            "nome": "Test Master",
            "email": "test@test.com",
            "Prompt_id": "33",
            "agent_id": "agent_test"
        })
        assert r.status_code == 202
        assert r.json()["status"] == "success"

@pytest.mark.anyio
async def test_auth_success_client_token_and_auto_fill():
    """
    Token da ATS ('c6UPm81mhr') deve autenticar como cliente ATS (ID 3),
    e preencher automaticamente Prompt_id (33) e agent_id (agent_4f1dba5e5432cd193d324754bf)
    sem que o cliente precise enviar esses campos!
    """
    database._supabase_async_client = None
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        exec_id = f"exec_ats_{uuid.uuid4().hex[:8]}"
        r = await ac.post("/webhook", headers={"X-API-Key": "c6UPm81mhr"}, json={
            "workflow_name": "trafego_ats_teste",
            "execution_id": exec_id,
            "numero": "+5548999999999",
            "nome": "Cliente ATS Teste",
            "email": "cliente@ats.com.br"
            # Omitindo Prompt_id e agent_id propositalmente!
        })
        assert r.status_code == 202
        assert r.json()["status"] == "success"
        db_id = r.json()["execution_db_id"]

        # Verifica no Supabase se os dados foram persistidos com Prompt_id 33 e agent_id da ATS
        supabase_async = await database.get_supabase_async()
        res = await supabase_async.table("workflow_executions").select("*").eq("id", db_id).execute()
        assert len(res.data) == 1
        input_data = res.data[0]["input_data"]
        assert input_data.get("client_id") == "3"
        assert str(input_data.get("Prompt_id")) == "33"
        assert input_data.get("agent_id") == "agent_4f1dba5e5432cd193d324754bf"
        print("\n[TEST PASS] Token ATS autenticado e parâmetros auto-preenchidos com sucesso!")
