import pytest
import asyncio
import os
import uuid
import httpx
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import main
import database
import services

# Garante que a API Key exista para o teste
if not main.WEBHOOK_API_KEY:
    main.WEBHOOK_API_KEY = "mf_sk_2026_pre_call_xK9v3Qm7bR4wT1nZ"

api_key = main.WEBHOOK_API_KEY

@pytest.fixture(autouse=True)
def restore_redis_state():
    orig_redis = getattr(main.app.state, 'redis', None)
    yield
    if orig_redis is not None:
        main.app.state.redis = orig_redis
    else:
        if hasattr(main.app.state, 'redis'):
            delattr(main.app.state, 'redis')

@pytest.mark.anyio
async def test_immediate_workflow_api_to_supabase():
    """Valida a execução imediata: Webhook -> Supabase -> Processamento -> Finalização"""
    # Reset do cliente async para garantir que use o loop atual deste teste
    database._supabase_async_client = None
    
    execution_id = f"TDD-IMMEDIATE-{uuid.uuid4()}"
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "+5548996027108",
        "nome": "Ryan Immediate Test",
        "email": "ryan@test.com",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": "2",  # Prompt ID 2 existente na base
        "quando_ligar": None  # Execução imediata
    }

    mock_redis = MagicMock()
    enqueued_jobs = []
    
    async def fake_enqueue_job(job_name, *args, **kwargs):
        enqueued_jobs.append((job_name, args, kwargs))
        return MagicMock()

    mock_redis.enqueue_job = fake_enqueue_job
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.close = AsyncMock()

    main.app.state.redis = mock_redis

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": api_key}
        )
        
        assert response.status_code == 202
        res_data = response.json()
        db_execution_id = res_data["execution_db_id"]
        assert db_execution_id is not None
        
        print(f"\n[TDD-IMMEDIATE] Webhook aceito. ID gerado no Supabase: {db_execution_id}")

        # Chama a execução imediata
        assert len(enqueued_jobs) == 1
        job_name, args, kwargs = enqueued_jobs[0]
        ctx = {"redis": mock_redis}
        
        print("[TDD-IMMEDIATE] Executando schedule_execution_node")
        await services.schedule_execution_node(ctx, *args)

    from database import supabase
    
    # Valida Master
    master = supabase.table('workflow_executions').select("*").eq('id', db_execution_id).execute()
    assert len(master.data) == 1
    assert master.data[0]['status'] == 'SUCCESS'

    # Valida Steps (deve conter fetch, format e retell)
    steps = supabase.table('workflow_step_executions').select("*").eq('execution_id', db_execution_id).execute()
    step_names = [s['step_name'] for s in steps.data]
    
    print(f"[TDD-IMMEDIATE] Passos gravados no Supabase: {step_names}")
    assert any('fetch_prompt' in name for name in step_names)
    assert any('format_payload' in name for name in step_names)
    assert any('create_retell_call' in name for name in step_names)

@pytest.mark.anyio
async def test_scheduled_workflow_api_to_supabase():
    """Valida o fluxo agendado: Webhook -> Supabase -> Agendamento Redis (Step Gravado) -> Deferment"""
    # Reset do cliente async para garantir que use o loop atual deste teste
    database._supabase_async_client = None
    
    execution_id = f"TDD-SCHEDULED-{uuid.uuid4()}"
    
    # Agendado para +5 minutos no futuro (fuso de Brasília)
    future_date = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "+5548996027108",
        "nome": "Ryan Scheduled Test",
        "email": "ryan@test.com",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": "2",
        "quando_ligar": future_date
    }

    mock_redis = MagicMock()
    enqueued_jobs = []
    
    async def fake_enqueue_job(job_name, *args, **kwargs):
        enqueued_jobs.append((job_name, args, kwargs))
        return MagicMock()

    mock_redis.enqueue_job = fake_enqueue_job
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.close = AsyncMock()

    main.app.state.redis = mock_redis

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": api_key}
        )
        
        assert response.status_code == 202
        db_execution_id = response.json()["execution_db_id"]
        
        print(f"\n[TDD-SCHEDULED] Webhook aceito com data futura: {future_date}. ID: {db_execution_id}")

        # Chama a rotina de decisão de agendamento
        assert len(enqueued_jobs) == 1
        job_name, args, kwargs = enqueued_jobs[0]
        ctx = {"redis": mock_redis}
        
        print("[TDD-SCHEDULED] Executando schedule_execution_node (deve agendar)")
        await services.schedule_execution_node(ctx, *args)

    from database import supabase
    
    # Valida Steps (deve conter o nó agendamento_redis marcado como SUCCESS)
    steps = supabase.table('workflow_step_executions').select("*").eq('execution_id', db_execution_id).execute()
    step_names = [s['step_name'] for s in steps.data]
    
    print(f"[TDD-SCHEDULED] Passos gravados no Supabase: {step_names}")
    assert any('agendamento_redis' in name for name in step_names)
    
    # O status do passo deve ser SUCCESS indicando que o agendamento no Redis foi concluído
    target_step = [s for s in steps.data if 'agendamento_redis' in s['step_name']][0]
    assert target_step['status'] == 'SUCCESS'
    print("✨ [TDD] Todos os cenários de teste (imediato e agendado) validados com SUCESSO no Supabase! ✨")


@pytest.mark.anyio
async def test_webhook_auth_failure_integration():
    """Valida se requisições com API Key inválida retornam 401 e não gravam nada no Supabase"""
    database._supabase_async_client = None
    execution_id = f"TDD-AUTH-FAIL-{uuid.uuid4()}"
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "+5548996027108",
        "nome": "Ryan Auth Fail",
        "email": "ryan@test.com",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": "2"
    }
    
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": "INVALID_KEY"}
        )
        assert response.status_code == 401
    
    from database import supabase
    # Verifica que nada foi gravado no Supabase para esta execução
    res = supabase.table('workflow_executions').select("*").eq('trigger_event_id', execution_id).execute()
    assert len(res.data) == 0


@pytest.mark.anyio
async def test_webhook_invalid_phone_integration():
    """Valida se número sem '+' retorna 400 e não grava no Supabase"""
    database._supabase_async_client = None
    execution_id = f"TDD-PHONE-FAIL-{uuid.uuid4()}"
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "5548996027108",  # Sem '+'
        "nome": "Ryan Phone Fail",
        "email": "ryan@test.com",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": "2"
    }
    
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 400
        assert "deve começar com o sinal de '+'" in response.json()["detail"]
        
    from database import supabase
    res = supabase.table('workflow_executions').select("*").eq('trigger_event_id', execution_id).execute()
    assert len(res.data) == 0


@pytest.mark.anyio
async def test_webhook_invalid_timezone_integration():
    """Valida se quando_ligar sem timezone retorna 400 e não grava no Supabase"""
    database._supabase_async_client = None
    execution_id = f"TDD-TZ-FAIL-{uuid.uuid4()}"
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "+5548996027108",
        "nome": "Ryan TZ Fail",
        "email": "ryan@test.com",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": "2",
        "quando_ligar": "2026-04-21T15:00:00"  # Sem offset/Z
    }
    
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 400
        assert "deve conter fuso horário válido" in response.json()["detail"]
        
    from database import supabase
    res = supabase.table('workflow_executions').select("*").eq('trigger_event_id', execution_id).execute()
    assert len(res.data) == 0


@pytest.mark.anyio
async def test_webhook_invalid_email_integration():
    """Valida se e-mail inválido retorna 422 e não grava no Supabase"""
    database._supabase_async_client = None
    execution_id = f"TDD-EMAIL-FAIL-{uuid.uuid4()}"
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "+5548996027108",
        "nome": "Ryan Email Fail",
        "email": "not-an-email",  # E-mail inválido
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": "2"
    }
    
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 422
        
    from database import supabase
    res = supabase.table('workflow_executions').select("*").eq('trigger_event_id', execution_id).execute()
    assert len(res.data) == 0


@pytest.mark.anyio
async def test_webhook_prompt_not_found_integration():
    """Valida se um Prompt inexistente faz o workflow falhar e gravar FAILED com erro descritivo no Supabase"""
    database._supabase_async_client = None
    execution_id = f"TDD-PROMPT-FAIL-{uuid.uuid4()}"
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "+5548996027108",
        "nome": "Ryan Bad Prompt",
        "email": "ryan@test.com",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": "999999999"  # Não existe
    }
    
    mock_redis = MagicMock()
    enqueued_jobs = []
    async def fake_enqueue_job(job_name, *args, **kwargs):
        enqueued_jobs.append((job_name, args, kwargs))
        return MagicMock()
    mock_redis.enqueue_job = fake_enqueue_job
    mock_redis.get = AsyncMock(return_value=None)
    
    main.app.state.redis = mock_redis
    
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 202
        db_execution_id = response.json()["execution_db_id"]
        
        # Roda o worker logicamente
        assert len(enqueued_jobs) == 1
        job_name, args, kwargs = enqueued_jobs[0]
        ctx = {"redis": mock_redis}
        
        await services.schedule_execution_node(ctx, *args)
        
    from database import supabase
    master = supabase.table('workflow_executions').select("*").eq('id', db_execution_id).execute()
    assert len(master.data) == 1
    assert master.data[0]['status'] == 'FAILED'
    assert "não encontrado na tabela Prompts" in master.data[0]['error_details']


@pytest.mark.anyio
async def test_webhook_prompt_by_name_integration():
    """Valida se busca por nome textual de prompt funciona e executa com sucesso"""
    database._supabase_async_client = None
    execution_id = f"TDD-PROMPT-NAME-{uuid.uuid4()}"
    
    from database import supabase
    # Busca um prompt válido na base para obter seu Pormpt_Name
    prompts = supabase.table('Prompts').select("*").limit(1).execute()
    if not prompts.data or not prompts.data[0].get('Pormpt_Name'):
        pytest.skip("Nenhum prompt com nome textual disponível no banco.")
        
    prompt_name = prompts.data[0]['Pormpt_Name']
    
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "+5548996027108",
        "nome": "Ryan Name Test",
        "email": "ryan@test.com",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": prompt_name
    }
    
    mock_redis = MagicMock()
    enqueued_jobs = []
    async def fake_enqueue_job(job_name, *args, **kwargs):
        enqueued_jobs.append((job_name, args, kwargs))
        return MagicMock()
    mock_redis.enqueue_job = fake_enqueue_job
    mock_redis.get = AsyncMock(return_value=None)
    
    main.app.state.redis = mock_redis
    
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 202
        db_execution_id = response.json()["execution_db_id"]
        
        assert len(enqueued_jobs) == 1
        job_name, args, kwargs = enqueued_jobs[0]
        ctx = {"redis": mock_redis}
        
        await services.schedule_execution_node(ctx, *args)
        
    master = supabase.table('workflow_executions').select("*").eq('id', db_execution_id).execute()
    assert len(master.data) == 1
    assert master.data[0]['status'] == 'SUCCESS'


@pytest.mark.anyio
async def test_webhook_null_vars_cleanup_integration():
    """Valida se campos empresa/segmento nulos não causam falha e rodam com sucesso"""
    database._supabase_async_client = None
    execution_id = f"TDD-NULL-VARS-{uuid.uuid4()}"
    test_payload = {
        "workflow_name": "pre_call_processing",
        "execution_id": execution_id,
        "numero": "+5548996027108",
        "nome": "Ryan Null Vars",
        "email": "ryan@test.com",
        "agent_id": "agent_1e4cfa23e3910c557d82167949",
        "Prompt_id": "2",
        "empresa": None,  # Nulos
        "segmento": None
    }
    
    mock_redis = MagicMock()
    enqueued_jobs = []
    async def fake_enqueue_job(job_name, *args, **kwargs):
        enqueued_jobs.append((job_name, args, kwargs))
        return MagicMock()
    mock_redis.enqueue_job = fake_enqueue_job
    mock_redis.get = AsyncMock(return_value=None)
    
    main.app.state.redis = mock_redis
    
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook",
            json=test_payload,
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 202
        db_execution_id = response.json()["execution_db_id"]
        
        assert len(enqueued_jobs) == 1
        job_name, args, kwargs = enqueued_jobs[0]
        ctx = {"redis": mock_redis}
        
        await services.schedule_execution_node(ctx, *args)
        
    from database import supabase
    master = supabase.table('workflow_executions').select("*").eq('id', db_execution_id).execute()
    assert len(master.data) == 1
    assert master.data[0]['status'] == 'SUCCESS'

