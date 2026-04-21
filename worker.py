import os
from dotenv import load_dotenv
from arq.connections import RedisSettings

# Importa as rotinas que serão processadas em background pelos workers
from services import schedule_execution_node, continue_workflow_execution

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

class WorkerSettings:
    """Configurações do Worker ARQ (Async Redis Queue).
    Inicie o worker no terminal rodando: 'arq worker.WorkerSettings'
    """
    functions = [
        schedule_execution_node, 
        continue_workflow_execution
    ]
    
    # URL de Conexão com o Redis
    redis_settings = RedisSettings.from_url(REDIS_URL)
    
    # Número de jobs correntes (Ajustar baseado na capacidade de I/O)
    max_jobs = 50
    
    # Garante tentativas em caso de restarts globais (job_timeout)
    job_timeout = 300
