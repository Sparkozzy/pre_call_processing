from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, Request, Header
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
import logging
import os
import hmac
from arq import create_pool
from arq.connections import RedisSettings
from dotenv import load_dotenv

from database import get_supabase_async
from services import get_utc_now

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
    email: EmailStr
    agent_id: Optional[str] = None
    Prompt_id: Optional[str] = None
    quando_ligar: Optional[str] = None # ISO 8601 string
    empresa: Optional[str] = None
    segmento: Optional[str] = None
    contexto: Optional[str] = None

    @field_validator('email', mode='before')
    @classmethod
    def clean_email(cls, v):
        if isinstance(v, str):
            # Limpa espaços e pontuação indevida no fim do e-mail
            return v.strip().rstrip('. ,;')
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

# Para rodar a API:
# uvicorn main:app --reload

# Para rodar o Worker de filas em outro terminal na mesma pasta:
# arq worker.WorkerSettings