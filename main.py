from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, Request, Header, UploadFile, File, Form
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
import logging
import os
import hmac
import csv
import io
import uuid
from arq import create_pool
from arq.connections import RedisSettings
from dotenv import load_dotenv

from database import get_supabase_async
from services import get_utc_now, update_batch_frequency

import re

def validate_time_format(time_str: str) -> bool:
    """Valida se uma string está no formato HH:MM (24h)."""
    pattern = r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$'
    return bool(re.match(pattern, time_str))

load_dotenv()

# Configuração básica de log
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
if "@" in REDIS_URL:
    protocol_user_pass, host_port = REDIS_URL.rsplit("@", 1)
    protocol_user_pass = protocol_user_pass.replace("#", "%23")
    REDIS_URL = f"{protocol_user_pass}@{host_port}"
    
WEBHOOK_API_KEY = os.getenv("WEBHOOK_API_KEY")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia a conexão com o banco de dados Redis durando o ciclo de vida da API."""
    if not WEBHOOK_API_KEY:
        logger.warning("⚠️ WEBHOOK_API_KEY não configurada! O endpoint ficará desprotegido.")
    logger.info("Tentando conectar ao banco de filas Redis...")
    app.state.redis = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    logger.info("Conectado ao Redis com suporte ARQ.")
    yield
    await app.state.redis.close()

app = FastAPI(title="Webhook Integration API", lifespan=lifespan)

class WebhookPayload(BaseModel):
    """
    Esquema de entrada para o webhook com suporte a fila persistente.
    """
    workflow_name: str
    execution_id: str
    numero: str
    nome: str
    email: str
    agent_id: Optional[str] = None
    Prompt_id: Optional[str] = None
    quando_ligar: Optional[str] = None # ISO 8601 string
    empresa: Optional[str] = None
    segmento: Optional[str] = None
    contexto: Optional[str] = None
    from_number: Optional[str] = None

    @field_validator('email', mode='before')
    @classmethod
    def clean_email(cls, v):
        if v is None:
            return "."
        if isinstance(v, str):
            v_stripped = v.strip()
            if v_stripped in ("", "."):
                return "."
            # Limpa espaços e pontuação indevida no fim do e-mail
            v_cleaned = v_stripped.rstrip('. ,;')
            if "@" not in v_cleaned:
                raise ValueError("E-mail inválido. Deve conter '@' ou ser '.' / vazio.")
            return v_cleaned
        return v

    @field_validator('nome', mode='before')
    @classmethod
    def clean_nome(cls, v):
        if isinstance(v, str):
            # Higieniza caracteres extras deixados nos nomes frequentemente (pontos na extração)
            return v.strip(' .-_,;')
        return v

@app.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(request: Request, payload: WebhookPayload, x_api_key: str = Header(alias="X-API-Key")):
    """
    Endpoint de recepção:
    1. Autentica via X-API-Key header.
    2. Valida regras de negócio (Pydantic).
    3. Cria registro Master no Supabase.
    4. Delega o payload inteiro para a fila do Redis via ARQ. O worker vai processar os passos do nó assincronamente.
    """
    
    # 0. Autenticação via API Key
    if not WEBHOOK_API_KEY or not hmac.compare_digest(x_api_key, WEBHOOK_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida ou ausente. Verifique o header 'X-API-Key'."
        )
    
    if not payload.numero.startswith("+"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O número de telefone deve começar com o sinal de '+'"
        )

    # 1.1 Validação Rigorosa de Fuso Horário
    if payload.quando_ligar:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(payload.quando_ligar.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError("Fuso horário ausente")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O campo 'quando_ligar' deve conter fuso horário válido (Ex: -03:00 ou Z)."
            )

    # 2. Criação do Registro Mestre (Rastreabilidade EDW) usando Client Assíncrono do SDK Supabase
    master_data = {
        'workflow_name': payload.workflow_name,
        'trigger_event_id': payload.execution_id,
        'status': 'PENDING',
        'input_data': payload.dict(),
        'started_at': get_utc_now()
    }

    try:
        supabase_async = await get_supabase_async()
        response = await supabase_async.table('workflow_executions').insert(master_data).execute()
        db_execution_id = response.data[0]['id']
    except Exception as e:
        logger.error(f"Erro ao criar registro mestre: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao registrar workflow no banco de dados."
        )

    # 3. Delega para a fila do worker (arq) e libera imediatamente o processo
    await request.app.state.redis.enqueue_job('schedule_execution_node', db_execution_id, payload.dict())

    # 4. Sucesso de Receção (202 - Accepted)
    return {
        "status": "success",
        "message": "Webhook aceito, registro mestre criado e delegado para a fila persistente.",
        "execution_db_id": db_execution_id
    }

@app.post("/webhook/csv", status_code=status.HTTP_200_OK)
async def receive_csv_webhook(
    request: Request,
    file: UploadFile = File(...),
    horario_inicio: str = Form(...),
    horario_fim: str = Form(...),
    contexto: Optional[str] = Form(None),
    frequencia: float = Form(...),
    agent_id: str = Form(...),
    prompt_id: str = Form(...),
    x_api_key: str = Header(alias="X-API-Key")
):
    """
    Endpoint para recepção e agendamento de chamadas a partir de arquivos CSV:
    1. Autentica via X-API-Key.
    2. Valida parâmetros globais e estrutura do CSV em streaming.
    3. Cria registro de lote sob workflow 'csv_scheduling' em workflow_executions.
    4. Salva temporariamente o arquivo CSV localmente.
    5. Enfileira a ingestão em background no worker (ingest_csv_batch) e retorna 200 OK.
    """
    
    # 0. Autenticação via API Key
    if not WEBHOOK_API_KEY or not hmac.compare_digest(x_api_key, WEBHOOK_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida ou ausente. Verifique o header 'X-API-Key'."
        )
        
    # 1. Validação dos Parâmetros Globais
    if frequencia < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A frequência não pode ser nula e nem menor que 1 segundo."
        )
        
    if not validate_time_format(horario_inicio):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O campo 'horario_inicio' deve estar no formato HH:MM (ex: 08:00)."
        )
    if not validate_time_format(horario_fim):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O campo 'horario_fim' deve estar no formato HH:MM (ex: 22:00)."
        )
        
    h_ini_hour, h_ini_min = map(int, horario_inicio.split(":"))
    h_fim_hour, h_fim_min = map(int, horario_fim.split(":"))
    if (h_ini_hour * 60 + h_ini_min) >= (h_fim_hour * 60 + h_fim_min):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O horário de início deve ser menor que o horário de fim."
        )
        
    # 2. Leitura e validação síncrona do CSV em fluxo
    try:
        contents = await file.read()
        try:
            text_content = contents.decode('utf-8')
        except UnicodeDecodeError:
            text_content = contents.decode('latin-1')
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Não foi possível ler o arquivo enviado. Certifique-se de que é um CSV válido. Detalhes: {e}"
        )
        
    csv_file = io.StringIO(text_content)
    reader = csv.reader(csv_file)
    
    try:
        header = next(reader)
    except StopIteration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O arquivo CSV enviado está vazio."
        )
        
    # Normaliza cabeçalhos (limpa espaços e minúsculas)
    header_normalized = [col.strip().lower() for col in header]
    required_columns = ["numero", "nome", "email"]
    
    for col in required_columns:
        if col not in header_normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Validação estrutural falhou: Coluna obrigatória '{col}' ausente no cabeçalho do CSV."
            )
            
    num_idx = header_normalized.index("numero")
    nome_idx = header_normalized.index("nome")
    email_idx = header_normalized.index("email")
    
    row_count = 0
    # Validação rápida de linhas para assegurar campos obrigatórios e formato de telefone
    for row_num, row in enumerate(reader, start=2):
        if not row:
            continue
        if len(row) <= max(num_idx, nome_idx, email_idx):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Erro na linha {row_num} do CSV: Quantidade de colunas é menor do que a esperada."
            )
            
        numero_val = row[num_idx].strip()
        nome_val = row[nome_idx].strip()
        
        if not numero_val:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Erro na linha {row_num} do CSV: O campo 'numero' não pode ser vazio."
            )
        if not nome_val:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Erro na linha {row_num} do CSV: O campo 'nome' não pode ser vazio."
            )
        if not numero_val.startswith("+"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Erro na linha {row_num} do CSV: O número '{numero_val}' deve iniciar com '+' contendo o DDI (Ex: +55...)."
            )
        row_count += 1

    if row_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O arquivo CSV não contém nenhuma linha de dados para processar."
        )

    # 3. Criação do Registro de Lote no Supabase (workflow_executions com workflow_name='csv_scheduling')
    batch_uuid = str(uuid.uuid4())
    master_data = {
        'id': batch_uuid,
        'workflow_name': 'csv_scheduling',
        'status': 'RUNNING',
        'input_data': {
            'contexto': contexto,
            'frequencia': frequencia,
            'horario_inicio': horario_inicio,
            'horario_fim': horario_fim,
            'agent_id': agent_id,
            'prompt_id': prompt_id,
            'file_name': file.filename,
            'total_leads': row_count
        },
        'started_at': get_utc_now()
    }
    
    try:
        supabase_async = await get_supabase_async()
        await supabase_async.table('workflow_executions').insert(master_data).execute()
    except Exception as e:
        logger.error(f"Erro ao registrar execução de lote no Supabase: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno ao registrar lote de disparos no banco de dados."
        )

    # 4. Enfileira o job de ingestão no Redis passando o conteúdo de texto do CSV diretamente (em memória)
    await request.app.state.redis.enqueue_job(
        'ingest_csv_batch', 
        batch_uuid, 
        text_content, 
        contexto, 
        frequencia, 
        agent_id, 
        prompt_id,
        horario_inicio,
        horario_fim
    )

    return {
        "status": "success",
        "message": "Arquivo CSV validado com sucesso e enfileirado para processamento assíncrono.",
        "batch_id": batch_uuid,
        "total_leads": row_count
    }

@app.post("/webhook/csv/cancel", status_code=status.HTTP_200_OK)
async def cancel_csv_webhook(
    request: Request,
    batch_id: Optional[str] = Form(None),
    x_api_key: str = Header(alias="X-API-Key")
):
    """
    Endpoint para cancelamento emergencial (Kill Switch):
    1. Se 'batch_id' for informado, cancela apenas esse lote.
    2. Se não for informado, cancela TODOS os lotes ativos globais (Panic Button).
    """
    
    # 0. Autenticação via API Key
    if not WEBHOOK_API_KEY or not hmac.compare_digest(x_api_key, WEBHOOK_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida ou ausente."
        )

    supabase_async = await get_supabase_async()

    if batch_id:
        # Cancelamento específico de um lote
        # 1. Grava flag rápida de bloqueio no Redis
        await request.app.state.redis.set(f"batch:{batch_id}:status", "cancelled")
        
        # 2. Atualiza status no Supabase
        try:
            await supabase_async.table('workflow_executions').update({
                'status': 'FAILED',
                'error_details': 'Lote de disparos cancelado pelo usuário via endpoint de interrupção.',
                'completed_at': get_utc_now()
            }).eq('id', batch_id).execute()
        except Exception as e:
            logger.error(f"Erro ao atualizar status do cancelamento no Supabase para lote {batch_id}: {e}")

        # 3. Enfileira o Garbage Collector (limpeza gradual das chaves do Redis)
        await request.app.state.redis.enqueue_job('clean_cancelled_jobs', batch_id)

        return {
            "status": "success",
            "message": f"Interrupção do lote {batch_id} ativada. Novos disparos foram bloqueados com sucesso.",
            "batch_id": batch_id
        }
    else:
        # Cancelamento Global (Panic Button)
        # 1. Ativa flag rápida global no Redis
        await request.app.state.redis.set("system:status", "cancelled")
        
        # 2. Busca todos os lotes ativos na workflow_executions
        cancelled_count = 0
        try:
            active_batches_response = await supabase_async.table('workflow_executions')\
                .select('id')\
                .eq('workflow_name', 'csv_scheduling')\
                .in_('status', ['RUNNING', 'PENDING'])\
                .execute()
                
            batch_ids = [b['id'] for b in active_batches_response.data]
            if batch_ids:
                # 3. Atualiza todos para FAILED no Supabase
                await supabase_async.table('workflow_executions').update({
                    'status': 'FAILED',
                    'error_details': 'Cancelamento global (Panic Button) ativado pelo usuário.',
                    'completed_at': get_utc_now()
                }).in_('id', batch_ids).execute()
                
                # 4. Enfileira a limpeza de jobs no Redis para cada lote
                for b_id in batch_ids:
                    await request.app.state.redis.enqueue_job('clean_cancelled_jobs', b_id)
                cancelled_count = len(batch_ids)
        except Exception as e:
            logger.error(f"Erro ao realizar cancelamento global no Supabase: {e}")

        return {
            "status": "success",
            "message": f"Panic Button ativado! Todos os disparos agendados de lotes estão suspensos.",
            "cancelled_batches_count": cancelled_count
        }

class UpdateFrequencyPayload(BaseModel):
    batch_id: str
    frequencia: float

@app.post("/webhook/csv/update-frequency", status_code=status.HTTP_200_OK)
async def update_frequency(
    request: Request,
    payload: UpdateFrequencyPayload,
    x_api_key: str = Header(alias="X-API-Key")
):
    """
    Endpoint para alteração dinâmica da frequência de disparos de um lote ativo:
    1. Autentica via X-API-Key.
    2. Valida o novo valor de frequência.
    3. Reordena e altera os scores (timestamps de agendamento) de todos os jobs pendentes no Redis.
    """
    if not WEBHOOK_API_KEY or not hmac.compare_digest(x_api_key, WEBHOOK_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida ou ausente."
        )

    if payload.frequencia < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A frequência não pode ser menor que 1 segundo."
        )

    try:
        await update_batch_frequency(request.app.state.redis, payload.batch_id, payload.frequencia)
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(ve)
        )
    except Exception as e:
        logger.error(f"Erro ao atualizar frequência do lote {payload.batch_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno ao atualizar a frequência: {e}"
        )

    return {
        "status": "success",
        "message": f"Frequência do lote {payload.batch_id} atualizada para {payload.frequencia}s com sucesso."
    }

@app.get("/webhook/csv/active", status_code=status.HTTP_200_OK)
async def list_active_batches(
    request: Request,
    x_api_key: str = Header(alias="X-API-Key")
):
    """
    Lista todos os lotes (batches) de CSV ativos (com status RUNNING ou PENDING)
    e retorna suas configurações e número de leads ainda pendentes na fila do Redis.
    """
    if not WEBHOOK_API_KEY or not hmac.compare_digest(x_api_key, WEBHOOK_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida ou ausente."
        )

    supabase_async = await get_supabase_async()
    
    # 1. Busca os lotes ativos no Supabase
    try:
        response = await supabase_async.table('workflow_executions')\
            .select('id, status, input_data, started_at')\
            .eq('workflow_name', 'csv_scheduling')\
            .in_('status', ['RUNNING', 'PENDING'])\
            .order('started_at', desc=True)\
            .execute()
        active_batches = response.data or []
    except Exception as e:
        logger.error(f"Erro ao buscar lotes ativos no Supabase: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao consultar banco de dados."
        )

    # 2. Varre o Redis para contar os jobs pendentes de cada lote
    redis_client = request.app.state.redis
    queue_name = 'arq:queue'
    queue_exists = await redis_client.exists(queue_name)
    
    batch_counts = {}
    if queue_exists:
        raw_jobs = await redis_client.zrange(queue_name, 0, -1)
        for job_bytes in raw_jobs:
            job_id = job_bytes.decode('utf-8') if isinstance(job_bytes, bytes) else str(job_bytes)
            if job_id.startswith("job:"):
                parts = job_id.split(":")
                if len(parts) >= 2:
                    b_id = parts[1]
                    batch_counts[b_id] = batch_counts.get(b_id, 0) + 1

    # 3. Consolida as informações
    result = []
    for batch in active_batches:
        batch_id = batch.get('id')
        if not batch_id:
            continue
        input_data = batch.get('input_data') or {}
        
        config = {
            "frequencia": input_data.get("frequencia"),
            "horario_inicio": input_data.get("horario_inicio"),
            "horario_fim": input_data.get("horario_fim"),
            "agent_id": input_data.get("agent_id"),
            "prompt_id": input_data.get("prompt_id"),
            "file_name": input_data.get("file_name"),
            "total_leads_csv": input_data.get("total_leads")
        }
        
        result.append({
            "batch_id": batch_id,
            "status_supabase": batch.get('status'),
            "started_at": batch.get('started_at'),
            "config": config,
            "leads_pendentes_fila": batch_counts.get(batch_id, 0)
        })

    return {
        "status": "success",
        "active_batches": result
    }

@app.get("/webhook/debug/redis")
async def debug_redis(request: Request, x_api_key: str = Header(alias="X-API-Key")):
    """
    Diagnostic endpoint to view keys and enqueued jobs in Redis.
    """
    if not WEBHOOK_API_KEY or not hmac.compare_digest(x_api_key, WEBHOOK_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida ou ausente."
        )
    
    from datetime import datetime, timezone
    import zoneinfo
    
    br_tz = zoneinfo.ZoneInfo("America/Sao_Paulo")
    
    redis_client = request.app.state.redis
    keys = await redis_client.keys("*")
    keys_str = [k.decode('utf-8') if isinstance(k, bytes) else str(k) for k in keys]
    
    queue_name = 'arq:queue'
    queue_exists = await redis_client.exists(queue_name)
    
    jobs_in_queue = []
    batch_counts = {}
    
    if queue_exists:
        raw_jobs = await redis_client.zrange(queue_name, 0, -1, withscores=True)
        for job_bytes, score in raw_jobs:
            job_id = job_bytes.decode('utf-8') if isinstance(job_bytes, bytes) else str(job_bytes)
            
            # Group by batch_id if job_id is like "job:batch_id:lead_id"
            if job_id.startswith("job:"):
                parts = job_id.split(":")
                if len(parts) >= 2:
                    b_id = parts[1]
                    batch_counts[b_id] = batch_counts.get(b_id, 0) + 1
            
            job_key = f"arq:job:{job_id}"
            job_data = await redis_client.get(job_key)
            
            job_info = {
                "job_id": job_id,
                "score_raw": score,
                "score_datetime_utc": datetime.fromtimestamp(score / 1000, tz=timezone.utc).isoformat() if score else None,
                "has_data": job_data is not None
            }
            jobs_in_queue.append(job_info)
            
    return {
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
        "server_time_br": datetime.now(br_tz).isoformat(),
        "total_keys": len(keys_str),
        "queue_exists": queue_exists,
        "jobs_in_queue_count": len(jobs_in_queue),
        "batch_counts": batch_counts,
        "jobs_in_queue_sample": jobs_in_queue[:20]
    }

# Para rodar a API:
# uvicorn main:app --reload

# Para rodar o Worker de filas em outro terminal na mesma pasta:
# arq worker.WorkerSettings