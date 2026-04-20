from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional
import logging
from database import supabase
from services import schedule_execution_node, get_utc_now

# Configuração básica de log
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Webhook Integration API")

class WebhookPayload(BaseModel):
    """
    Esquema de entrada para o webhook com suporte a agendamento.
    """
    workflow_name: str
    execution_id: str
    numero: str
    nome: str
    email: EmailStr
    agent_id: str
    Prompt_id: Optional[str] = None
    quando_ligar: Optional[str] = None # ISO 8601 string
    empresa: Optional[str] = None
    segmento: Optional[str] = None

@app.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """
    Endpoint de recepção:
    1. Valida regras de negócio.
    2. Cria registro Master no Supabase.
    3. Delega agendamento/execução para background.
    """
    
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

    # 2. Criação do Registro Mestre (Rastreabilidade EDW)
    master_data = {
        'workflow_name': payload.workflow_name,
        'trigger_event_id': payload.execution_id,
        'status': 'PENDING',
        'input_data': payload.dict(),
        'started_at': get_utc_now()
    }

    try:
        response = supabase.table('workflow_executions').insert(master_data).execute()
        db_execution_id = response.data[0]['id']
    except Exception as e:
        logger.error(f"Erro ao criar registro mestre: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao registrar workflow no banco de dados."
        )

    # 3. Delega para o nó de agendamento no services.py
    background_tasks.add_task(schedule_execution_node, db_execution_id, payload.dict())

    # 4. Sucesso de Receção (202 - Accepted)
    return {
        "status": "success",
        "message": "Webhook aceito e registro mestre criado.",
        "execution_db_id": db_execution_id
    }

# Para rodar o servidor:
# uvicorn main:app --reload